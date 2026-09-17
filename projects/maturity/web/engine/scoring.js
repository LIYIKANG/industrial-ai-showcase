/**
 * 计分引擎 —— 纯函数、确定性：同一组答案永远得到同一份结果。
 *
 * 维度分 = 该维度下所有已答题（评估题 + 命中的痛点追问）得分率 × 100；
 * 总分   = 四个维度按权重加权；
 * 第 5 轴「AI 行动力」= 组织维度中 ai 标记题的得分率 × 100；
 * 行业相对位置 = 正态近似 Φ((总分 − 行业基准) / σ)，文案保留「估计 / 约」措辞。
 */
import { AI_AXIS_NAME, BENCHMARKS, BENCHMARK_SIGMA, CORE_QUESTIONS, DIMS, DIM_WEIGHTS, INDUSTRY_NAME, PAINS, TIERS, } from './config.js';
/** 当前作答涉及的全部题目：9 道评估题 + 已选痛点的追问 */
export function activeQuestions(painIds) {
    const followUps = PAINS.filter((p) => painIds.includes(p.id)).map((p) => p.followUp);
    return [...followUps, ...CORE_QUESTIONS];
}
function ratioToScore(points, maxPoints) {
    if (maxPoints <= 0)
        return 0;
    return Math.round((points / maxPoints) * 100);
}
/** 标准正态 CDF 的近似（Abramowitz–Stegun 公式，误差 < 1e-7） */
function normalCdf(z) {
    const t = 1 / (1 + 0.2316419 * Math.abs(z));
    const d = 0.3989423 * Math.exp((-z * z) / 2);
    const p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))));
    return z >= 0 ? 1 - p : p;
}
export function computeResult(input) {
    const questions = activeQuestions(input.painIds);
    const points = { D: 0, S: 0, M: 0, O: 0 };
    const maxPoints = { D: 0, S: 0, M: 0, O: 0 };
    let aiPoints = 0;
    let aiMax = 0;
    for (const q of questions) {
        const idx = input.answers[q.id];
        if (idx === undefined || !q.options[idx])
            continue;
        const score = q.options[idx].score;
        points[q.dim] += score;
        maxPoints[q.dim] += 3;
        if (q.ai) {
            aiPoints += score;
            aiMax += 3;
        }
    }
    const dims = {
        D: ratioToScore(points.D, maxPoints.D),
        S: ratioToScore(points.S, maxPoints.S),
        M: ratioToScore(points.M, maxPoints.M),
        O: ratioToScore(points.O, maxPoints.O),
    };
    const aiAction = ratioToScore(aiPoints, aiMax);
    const totalScore = Math.round(Object.keys(DIM_WEIGHTS).reduce((sum, dim) => sum + dims[dim] * DIM_WEIGHTS[dim], 0));
    const tier = TIERS.find((t) => totalScore >= t.min) ?? TIERS[TIERS.length - 1];
    const bench = BENCHMARKS[input.industryId] ?? BENCHMARKS.other;
    const z = (totalScore - bench.total) / BENCHMARK_SIGMA;
    const percentile = Math.min(95, Math.max(5, Math.round(normalCdf(z) * 100)));
    const industryName = INDUSTRY_NAME[input.industryId] ?? INDUSTRY_NAME.other;
    const positionText = `估计超过约 ${percentile}% 的${industryName}同行（基于行业基准近似测算）`;
    const radar = [
        { label: DIMS.D.short, value: dims.D, bench: bench.dims.D },
        { label: DIMS.S.short, value: dims.S, bench: bench.dims.S },
        { label: DIMS.M.short, value: dims.M, bench: bench.dims.M },
        { label: DIMS.O.short, value: dims.O, bench: bench.dims.O },
        { label: AI_AXIS_NAME, value: aiAction, bench: bench.ai },
    ];
    return {
        totalScore,
        tier,
        dims,
        aiAction,
        benchmark: { industryId: input.industryId, total: bench.total, dims: bench.dims, ai: bench.ai },
        percentile,
        positionText,
        radar,
    };
}
