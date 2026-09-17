/**
 * 规则引擎 —— 行业关键词归类、推荐场景卡、专家总评拼装与置信提示（矛盾检测）。
 * 与 scoring.ts 一样是纯函数：同一组输入永远得到同一份输出。
 */

import {
  CARDS,
  COPY,
  CORE_QUESTIONS,
  DIMS,
  INDUSTRIES,
  INDUSTRY_NAME,
  PAINS,
  SIZES,
  type CardDef,
  type DimId,
  type IndustryId,
} from './config.js';
import type { AnswerMap, AssessmentInput, AssessmentResult } from './scoring.js';

/* --------------------------------------------------------------------------
   行业关键词归类（自填行业时的本地方案）
   -------------------------------------------------------------------------- */

export interface ClassifyOutcome {
  industryId: IndustryId;
  industryName: string;
  method: 'keyword' | 'none';
}

export function classifyIndustry(freeText: string): ClassifyOutcome {
  const text = freeText.trim();
  if (text) {
    for (const industry of INDUSTRIES) {
      if (industry.keywords.some((kw) => text.toLowerCase().includes(kw.toLowerCase()))) {
        return { industryId: industry.id, industryName: industry.name, method: 'keyword' };
      }
    }
  }
  return { industryId: 'other', industryName: INDUSTRY_NAME.other, method: 'none' };
}

/* --------------------------------------------------------------------------
   推荐场景卡：触发规则打分 → 取前 3 张，第一张高亮；前置条件逐卡判断
   -------------------------------------------------------------------------- */

export interface RecommendedCard extends CardDef {
  /** 前置条件是否已满足（true → ✓；false → ⏳ 并展示补课建议） */
  prereqMet: boolean;
}

/** 选项分值直接读答案：未答按 -1 处理，任何 >= 阈值 的判断都不会误命中 */
function optScore(answers: AnswerMap, questionId: string): number {
  const question =
    PAINS.find((p) => p.followUp.id === questionId)?.followUp ??
    CORE_QUESTIONS.find((q) => q.id === questionId);
  if (!question) return -1;
  const idx = answers[questionId];
  if (idx === undefined || !question.options[idx]) return -1;
  return question.options[idx].score;
}

interface CardRule {
  id: string;
  /** 返回触发权重；0 表示不触发。痛点 / 快赢命中各 +2，维度信号 +1 */
  weight: (input: AssessmentInput, result: AssessmentResult) => number;
  prereqMet: (input: AssessmentInput, result: AssessmentResult) => boolean;
}

const CARD_RULES: CardRule[] = [
  {
    id: 'card_report',
    weight: (input) => {
      let w = 0;
      if (input.painIds.includes('p_data')) w += 2;
      if (input.quickWins.includes('qw_report')) w += 2;
      if (optScore(input.answers, 'M2') >= 0 && optScore(input.answers, 'M2') <= 1) w += 1;
      return w;
    },
    prereqMet: (input) => optScore(input.answers, 'D1') >= 1,
  },
  {
    id: 'card_know',
    weight: (input) => {
      let w = 0;
      if (input.painIds.includes('p_knowledge')) w += 2;
      if (input.quickWins.includes('qw_know')) w += 2;
      if (input.quickWins.includes('qw_qa')) w += 2;
      return w;
    },
    prereqMet: (input) => {
      const s = optScore(input.answers, 'f_knowledge');
      // 未触发追问（没选该痛点）时默认可满足；追问答 0（全在脑子里）则需先补文档
      return s !== 0;
    },
  },
  {
    id: 'card_trace',
    weight: (input) => {
      let w = 0;
      if (input.painIds.includes('p_quality')) w += 2;
      if (input.quickWins.includes('qw_trace')) w += 2;
      return w;
    },
    prereqMet: (input) => {
      const s = optScore(input.answers, 'f_quality');
      return s >= 2 || (s === -1 && optScore(input.answers, 'D1') >= 2);
    },
  },
  {
    id: 'card_maint',
    weight: (input) => (input.painIds.includes('p_equipment') ? 2 : 0),
    prereqMet: (input) => optScore(input.answers, 'f_equipment') >= 2,
  },
  {
    id: 'card_plan',
    weight: (input) => {
      let w = 0;
      if (input.painIds.includes('p_delivery')) w += 2;
      if (input.painIds.includes('p_inventory')) w += 2;
      return w;
    },
    prereqMet: (input) =>
      optScore(input.answers, 'D2') >= 2 || optScore(input.answers, 'f_inventory') >= 2,
  },
  {
    id: 'card_doc',
    weight: (input) => {
      let w = 0;
      if (input.quickWins.includes('qw_doc')) w += 2;
      if (input.quickWins.includes('qw_qa')) w += 1;
      return w;
    },
    prereqMet: (input) => optScore(input.answers, 'M1') >= 1,
  },
];

export function buildCards(
  input: AssessmentInput,
  result: AssessmentResult,
): RecommendedCard[] {
  const scored = CARD_RULES.map((rule) => ({
    rule,
    weight: rule.weight(input, result),
  })).filter((entry) => entry.weight > 0);

  scored.sort((a, b) => b.weight - a.weight);

  let picked = scored.slice(0, 3).map((entry) => entry.rule);

  // 兜底：什么都没触发（如只选「说不上来」且痛点极少）时给通用起步组合
  if (picked.length === 0) {
    picked = CARD_RULES.filter((r) => r.id === 'card_report' || r.id === 'card_know');
  }

  return picked.map((rule) => ({
    ...CARDS[rule.id],
    prereqMet: rule.prereqMet(input, result),
  }));
}

/* --------------------------------------------------------------------------
   置信提示：作答自相矛盾时在报告中明确说出来
   -------------------------------------------------------------------------- */

export function buildConfidenceFlags(
  input: AssessmentInput,
  result: AssessmentResult,
): string[] {
  const flags: string[] = [];
  const m2 = optScore(input.answers, 'M2');
  const d2 = optScore(input.answers, 'D2');
  const d1 = optScore(input.answers, 'D1');
  const s2 = optScore(input.answers, 'S2');
  const o1 = optScore(input.answers, 'O1');

  if (m2 === 3 && d2 <= 1 && d2 >= 0) {
    flags.push(
      '您提到经营指标可实时看到，但同时表示数据账实经常不符——看板数据的可信度值得先复核。',
    );
  }
  if (input.painIds.includes('p_data') && d1 === 3 && s2 >= 2) {
    flags.push(
      '您选择了「数据分散」的痛点，但系统化与集成程度的作答较高，两者略有出入，建议现场访谈时再确认。',
    );
  }
  if (o1 === 3 && (result.dims.D + result.dims.S) / 2 < 35) {
    flags.push(
      '高层投入意愿很强，但数据与系统基础尚薄弱——建议先以小场景验证再放大投入，避免期望与地基错位。',
    );
  }
  return flags;
}

/* --------------------------------------------------------------------------
   专家总评：按答卷组合拼装，结尾保留固定收尾句
   -------------------------------------------------------------------------- */

export function buildExpertText(
  input: AssessmentInput,
  result: AssessmentResult,
  cards: RecommendedCard[],
  flags: string[],
): string {
  const sizeLabel = SIZES.find((s) => s.id === input.sizeId)?.label ?? '';
  const industryName = INDUSTRY_NAME[input.industryId] ?? INDUSTRY_NAME.other;

  const dimIds = Object.keys(result.dims) as DimId[];
  const strongest = dimIds.reduce((a, b) => (result.dims[b] > result.dims[a] ? b : a));
  const weakest = dimIds.reduce((a, b) => (result.dims[b] < result.dims[a] ? b : a));

  const painLabels = PAINS.filter((p) => input.painIds.includes(p.id)).map((p) => p.label);

  const parts: string[] = [];

  parts.push(
    `作为一家${sizeLabel ? `${sizeLabel}规模的` : ''}${industryName}企业，贵司整体 AI 就绪度处于「${result.tier.name}」（总分 ${result.totalScore}，${result.positionText}）。`,
  );

  if (result.dims[strongest] !== result.dims[weakest]) {
    parts.push(
      `相对优势在${DIMS[strongest].name}（${result.dims[strongest]} 分），当前的主要短板是${DIMS[weakest].name}（${result.dims[weakest]} 分）——它很可能是后续 AI 场景能否跑通的约束项。`,
    );
  } else {
    parts.push(`四个维度得分较为均衡，说明基础没有明显偏科，适合按价值优先级选择切入场景。`);
  }

  if (painLabels.length > 0) {
    parts.push(
      `您提到的「${painLabels.join('」「')}」，在同行业中相当典型；下方推荐的场景正是针对这些问题给出的切入路径。`,
    );
  }

  if (cards.length > 0) {
    parts.push(
      `结合您希望优先见效的环节，建议从「${cards[0].title}」入手，三个月内先跑出一个可衡量的闭环，再逐步扩展。`,
    );
  }

  if (flags.length > 0) {
    parts.push(`另外请留意：${flags[0]}`);
  }

  parts.push(COPY.closing);

  return parts.join('\n');
}
