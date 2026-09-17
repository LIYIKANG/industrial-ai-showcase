// 参数设定页：指标上下限 + 求解默认设置。

import { api } from './api.js';
import { $, el, clear, fmtAuto, toast } from './ui.js';

let meta = null;
let ranges = {};   // 库存实际范围，用于提示「目标是否可达」

const SETTING_DEFS = [
  { key: 'bloom_tolerance', label: '冻力默认容差 ±', type: 'number', step: 0.5,
    desc: '配料页每行留空时使用这个值' },
  { key: 'whole_batch', label: '整批取料（一批用了就必须用完）', type: 'bool',
    desc: '车间现实：拆批会留下要重新化验、贴标、入库的零头。代价是总量无法精确命中订单量' },
  { key: 'weight_tol_pct', label: '实配总量允许偏差 ±%', type: 'number', step: 0.5,
    desc: '仅整批取料时生效。批重是 63~934kg 的任意实数，凑出恰好 3000kg 是子集和问题，必须留窗口' },
  { key: 'min_take', label: '单批最小取用量 (kg)', type: 'number', step: 1,
    desc: '避免解出 0.3kg 这种车间没法执行的碎量。整批取料时该项无意义、自动忽略' },
  { key: 'max_batches', label: '每单最多用几批', type: 'number', step: 1,
    desc: '0 表示不限制；限制越严越可能无解' },
  { key: 'candidates', label: '每单候选批次数', type: 'number', step: 10,
    desc: '按冻力接近程度预筛。越大解越优，但求解越慢' },
  { key: 'time_limit', label: '求解时限 (秒)', type: 'number', step: 1,
    desc: '好解通常几秒内就找到，剩余时间用于证明最优；超时返回的方案往往同样好' },
  { key: 'worst_grade', label: '可接受的最差水不溶物等级', type: 'select',
    desc: '差于该等级的批次直接排除，不参与配料' },
];

// 「取料偏好」用到的冻力分段界线。单独成组，因为它们需要配一块实时预览面板。
const BAND_DEFS = [
  { key: 'high_bloom_from', label: '高冻力料：冻力 ≥', type: 'number', step: 5,
    desc: '达到这个值才算「高冻力料」。配料页选「保留高冻力料」时，系统会避开这段库存' },
  { key: 'low_bloom_to', label: '低冻力料：冻力 ≤', type: 'number', step: 5,
    desc: '低于这个值才算「低冻力料」。配料页选「保留低冻力料」时，系统会避开这段库存' },
];

export function init(paramsMeta) {
  meta = paramsMeta;
  $('#btnSaveParams').addEventListener('click', save);
  $('#btnResetParams').addEventListener('click', async () => {
    if (!confirm('确定恢复所有参数为默认值？当前设定会被覆盖。')) return;
    try {
      const cfg = await api.resetParams();
      meta.limits = cfg.limits;
      meta.settings = cfg.settings;
      render();
      toast('已恢复默认');
    } catch (e) {
      toast(e.message, true);
    }
  });
}

export async function load() {
  try {
    const s = await api.stats();
    ranges = {};
    for (const p of s.指标) {
      if (p.type === 'numeric') ranges[p.key] = { min: p.最小, max: p.最大 };
    }
  } catch { /* 范围提示是锦上添花，取不到就不显示 */ }
  render();
}

// ---------------------------------------------------------------- 渲染

function render() {
  renderLimits();
  renderSettings();
  renderBands();
  refreshBandPreview();
}

function renderLimits() {
  const tbody = clear($('#paramTable').tBodies[0]);

  for (const p of meta.params) {
    if (p.role !== 'limit') continue;
    const lim = meta.limits[p.key] ?? { lo: null, hi: null, enabled: true };
    const rg = ranges[p.key];

    const chk = el('input', {
      type: 'checkbox', 'data-k': p.key, 'data-f': 'enabled',
      checked: lim.enabled !== false,
    });
    if (lim.enabled !== false) chk.setAttribute('checked', '');

    tbody.append(el('tr', {}, [
      el('td', {}, chk),
      el('td', { class: 'left', html: `<b>${p.label}</b>` }),
      el('td', { class: 'left muted', text: p.unit || '—' }),
      el('td', {}, el('input', {
        type: 'number', step: 'any', 'data-k': p.key, 'data-f': 'lo',
        value: lim.lo ?? '', placeholder: '不限',
      })),
      el('td', {}, el('input', {
        type: 'number', step: 'any', 'data-k': p.key, 'data-f': 'hi',
        value: lim.hi ?? '', placeholder: '不限',
      })),
      el('td', { class: 'left range-hint',
        text: rg ? `${fmtAuto(rg.min)} ~ ${fmtAuto(rg.max)}` : '—' }),
      el('td', { class: 'left muted', text: describe(p, lim, rg) }),
    ]));
  }
}

/** 给出「这条限制会不会直接导致无解」的即时判断。 */
function describe(p, lim, rg) {
  if (lim.enabled === false) return '未启用，不参与约束';
  if (!rg) return '';
  if (lim.lo !== null && lim.lo !== undefined && lim.lo > rg.max) {
    return `下限高于库存最大值 ${fmtAuto(rg.max)}，必然无解`;
  }
  if (lim.hi !== null && lim.hi !== undefined && lim.hi < rg.min) {
    return `上限低于库存最小值 ${fmtAuto(rg.min)}，必然无解`;
  }
  const tight =
    (lim.lo !== null && lim.lo !== undefined && lim.lo > (rg.min + (rg.max - rg.min) * 0.75)) ||
    (lim.hi !== null && lim.hi !== undefined && lim.hi < (rg.min + (rg.max - rg.min) * 0.25));
  return tight ? '接近库存边界，可选料很少' : '';
}

function renderSettings() {
  renderInputs($('#settingsGrid'), SETTING_DEFS);
}

function renderBands() {
  renderInputs($('#bandGrid'), BAND_DEFS, refreshBandPreview);
}

function renderInputs(box, defs, onInput) {
  clear(box);

  for (const d of defs) {
    let input;
    if (d.type === 'bool') {
      input = el('input', { type: 'checkbox', 'data-s': d.key, 'data-bool': '1' });
      input.checked = meta.settings[d.key] !== false;
      if (onInput) input.addEventListener('change', onInput);
    } else if (d.type === 'select') {
      input = el('select', { 'data-s': d.key }, meta.grades.map((g) =>
        el('option', { value: g, text: g, selected: meta.settings[d.key] === g })));
      input.value = meta.settings[d.key] ?? meta.grades[meta.grades.length - 1];
    } else {
      input = el('input', {
        type: 'number', step: d.step, 'data-s': d.key,
        value: meta.settings[d.key] ?? '',
      });
      if (onInput) input.addEventListener('input', onInput);
    }
    box.append(el('label', {}, [
      el('span', { text: d.label }),
      input,
      el('div', { class: 'desc', text: d.desc }),
    ]));
  }
}

// ---------------------------------------------------------------- 分段预览

let bandTimer = null;

/** 界线改了就重算三段的批次数与吨位 —— 光给输入框，用户没法判断 275 和 260 差在哪。 */
function refreshBandPreview() {
  clearTimeout(bandTimer);
  bandTimer = setTimeout(async () => {
    const box = $('#bandPreview');
    const hi = +($('[data-s="high_bloom_from"]')?.value ?? 0);
    const lo = +($('[data-s="low_bloom_to"]')?.value ?? 0);
    if (!(hi > lo)) {
      clear(box).append(el('div', { class: 'banner err', text: '低冻力料上界必须小于高冻力料下界' }));
      return;
    }
    try {
      const d = await api.bloomBands({ high_from: hi, low_to: lo });
      clear(box);

      const seg = (name, v, cls) => el('div', { class: `band ${cls}` }, [
        el('div', { class: 'band-name', text: name }),
        el('div', { class: 'band-v' }, [
          `${v.批次.toLocaleString('zh-CN')}`, el('span', { class: 'u', text: ' 批' }),
        ]),
        el('div', { class: 'band-sub', text: `${v.吨位} 吨 · 占库存 ${v.占比}%` }),
      ]);

      box.append(el('div', { class: 'band-row' }, [
        seg(`低冻力料 ≤ ${lo}`, d.低冻力料, 'low'),
        seg(`中间料 ${lo} ~ ${hi}`, d.中间料, 'mid'),
        seg(`高冻力料 ≥ ${hi}`, d.高冻力料, 'high'),
      ]));

      const q = d.分位;
      box.append(el('div', { class: 'band-hint' }, [
        el('b', { text: '库存冻力分位数：' }),
        `最小 ${d.最小} · p5 ${q.p5} · p15 ${q.p15} · p25 ${q.p25} · 中位 ${q.p50} · ` +
        `p75 ${q.p75} · p85 ${q.p85} · p95 ${q.p95} · 最大 ${d.最大}`,
      ]));
    } catch (e) {
      clear(box).append(el('div', { class: 'banner err', text: e.message }));
    }
  }, 300);
}

// ---------------------------------------------------------------- 保存

async function save() {
  const limits = {};
  for (const node of $('#paramTable').querySelectorAll('[data-k]')) {
    const key = node.dataset.k;
    const field = node.dataset.f;
    limits[key] ??= {};
    if (field === 'enabled') limits[key].enabled = node.checked;
    else limits[key][field] = node.value === '' ? null : +node.value;
  }

  for (const [key, v] of Object.entries(limits)) {
    if (v.lo !== null && v.hi !== null && v.lo !== undefined && v.hi !== undefined && v.lo > v.hi) {
      toast(`${key}：下限不能大于上限`, true);
      return;
    }
  }

  const settings = {};
  for (const node of document.querySelectorAll('#settingsGrid [data-s], #bandGrid [data-s]')) {
    const key = node.dataset.s;
    settings[key] = node.dataset.bool ? node.checked
      : node.tagName === 'SELECT' ? node.value
      : (node.value === '' ? null : +node.value);
  }

  try {
    const cfg = await api.saveParams({ limits, settings });
    meta.limits = cfg.limits;
    meta.settings = cfg.settings;
    render();
    toast('已保存，下次求解即刻生效');
  } catch (e) {
    toast(e.message, true);
  }
}
