// 方案对比页：同一客户需求下横向评估多个求解方案。
//
// 对比的公平前提是约束完全一致 —— 所有方案共用同一套加权平均约束、同一份客户规格、
// 同一个候选池，只有目标函数不同。加权平均本身不是可选项，它是物理事实。

import { api } from './api.js';
import { $, el, clear, fmt, fmtAuto, toast } from './ui.js';
import { palette, svgEl } from './charts.js';

let meta = null;
let methods = null;
let pricing = null;
let last = null;
let revision = 0;
function invalidate() {
  revision += 1;
  last = null;
  clear($('#cmpResult')).append(el('div', { class: 'banner warn', role: 'status', text: '输入已修改，请重新开始对比。' }));
}


const money = (v) => `¥${(v ?? 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`;

// 预设场景。每一组都已针对当前库存**实跑验证过可解**，不会在演示时当场翻车。
// targets 的值是 [目标值, 容差, 方向]。
//
// 覆盖面刻意做宽：从 2 项指标到 6 项指标、从 1.2 吨到 20 吨、从常规牌号到高端稀缺牌号，
// 并且**保留一组人工也能做出来的**（常规 210）—— 全部让对照组失败反而不可信。
const PRESETS = [
  {
    name: '常规牌号 210', weight: 3000, tag: '入门',
    desc: '2 项指标。人工也能配出来，差别只在批次数 8 vs 4',
    targets: {
      '冻力Bloomg': [210, 5, 'min'],
      '水分%': [12, 1, 'max'],
    },
  },
  {
    name: '标准食用明胶 210', weight: 3000, tag: '常用',
    desc: '5 项指标，典型食用胶规格单',
    targets: {
      '冻力Bloomg': [210, 5, 'min'],
      '水分%': [12, 0.5, 'max'],
      '灰分%': [0.5, 0.15, 'max'],
      'PH值': [5.6, 0.25, 'both'],
      '二氧化硫mg/kg': [12, 4, 'max'],
    },
  },
  {
    name: '严规格多指标 210', weight: 2000, tag: '人工失效',
    desc: '5 项指标且 PH、粘度卡得紧 —— 人工只盯冻力必然失控',
    targets: {
      '冻力Bloomg': [210, 5, 'min'],
      'PH值': [5.5, 0.15, 'max'],
      '勃氏粘度mPa/s': [4.5, 0.3, 'both'],
      '水分%': [11.8, 0.5, 'max'],
      '灰分%': [0.4, 0.12, 'max'],
    },
  },
  {
    name: '高牌号硬胶囊 260', weight: 1500, tag: '高端稀缺',
    desc: '6 项指标。冻力已进入产能塌陷区，高端料被啃掉多少要盯紧',
    targets: {
      '冻力Bloomg': [260, 6, 'min'],
      '水分%': [11.5, 0.6, 'max'],
      '灰分%': [0.45, 0.15, 'max'],
      '透过率450%': [85, 4, 'min'],
      '透过率620%': [96, 2, 'min'],
      '二氧化硫mg/kg': [12, 4, 'max'],
    },
  },
  {
    name: '药用高透过 240', weight: 1200, tag: '高要求',
    desc: '6 项指标，透过率与二氧化硫双重收紧',
    targets: {
      '冻力Bloomg': [240, 6, 'min'],
      '透过率450%': [88, 3, 'min'],
      '透过率620%': [97, 1.5, 'min'],
      '二氧化硫mg/kg': [8, 3, 'max'],
      '灰分%': [0.35, 0.12, 'max'],
      'PH值': [5.7, 0.2, 'both'],
    },
  },
  {
    name: '低牌号大批量 150', weight: 20000, tag: '大单',
    desc: '20 吨。批次数上到几十批，工时差异被放大',
    targets: {
      '冻力Bloomg': [150, 8, 'min'],
      '水分%': [12.5, 0.8, 'max'],
      '灰分%': [0.6, 0.25, 'max'],
      '电导率us/cm': [120, 20, 'max'],
      '粘度下降%': [2.5, 1.2, 'max'],
    },
  },
  {
    name: '清库存宽规格 180', weight: 8000, tag: '消化呆滞',
    desc: '规格宽松、量大 —— 最能体现「最大消化难用料」的价值',
    targets: {
      '冻力Bloomg': [180, 15, 'min'],
      '水分%': [13, 1, 'max'],
      '灰分%': [0.8, 0.3, 'max'],
    },
  },
];

export function init(paramsMeta) {
  meta = paramsMeta;
  ['input', 'change'].forEach(event => $('#view-compare').addEventListener(event, e => {
    if (e.target.matches('input, select')) invalidate();
  }));
  $('#btnCompare').addEventListener('click', run);
  $('#btnSavePricing').addEventListener('click', savePricing);
  $('#btnResetPricing').addEventListener('click', async () => {
    if (!confirm('恢复默认定价曲线？')) return;
    pricing = await api.resetPricing();
    invalidate();
    renderPricing();
    toast('已恢复默认');
  });
}

export async function load() {
  if (!methods) {
    try {
      [methods, pricing] = await Promise.all([api.compareMethods(), api.pricing()]);
    } catch (e) {
      toast(`加载失败：${e.message}`, true);
      return;
    }
    renderMethods();
    renderTargets();
    renderPresets();
    renderPricing();
    applyPreset(PRESETS[DEFAULT_PRESET]);
  }
}

// ---------------------------------------------------------------- 预设场景

const DEFAULT_PRESET = 1;   // 打开就载入「标准食用明胶」，点一下就能对比

function renderPresets() {
  const box = clear($('#cmpPresets'));
  for (const [i, p] of PRESETS.entries()) {
    box.append(el('button', {
      // 默认选中的那一组也要有选中态，否则用户看不出当前填的是哪个预设
      class: `preset-chip ${i === DEFAULT_PRESET ? 'on' : ''}`, type: 'button',
      title: p.desc,
      onclick: (e) => {
        for (const b of box.children) b.classList.remove('on');
        e.currentTarget.classList.add('on');
        applyPreset(p);
        toast(`已载入「${p.name}」：${Object.keys(p.targets).length} 项指标 · ${p.weight.toLocaleString('zh-CN')} kg`);
      },
    }, [
      el('span', { class: 'pc-tag', text: p.tag }),
      el('span', { class: 'pc-name', text: p.name }),
      el('span', { class: 'pc-meta', text: `${Object.keys(p.targets).length}项 · ${p.weight.toLocaleString('zh-CN')}kg` }),
    ]));
  }
}

/** 把预设写进输入区：先全部清空再逐项填，避免上一组的勾选残留。 */
function applyPreset(p) {
  invalidate();
  const box = $('#cmpTargets');
  $('#cmpName').value = p.name;
  $('#cmpWeight').value = p.weight;

  for (const cb of box.querySelectorAll('[data-t]')) {
    const k = cb.dataset.t;
    cb.checked = false;
    cb.closest('.target-item').classList.remove('on');
    box.querySelector(`[data-tv="${k}"]`).value = '';
    box.querySelector(`[data-tt="${k}"]`).value = '';
    box.querySelector(`[data-td="${k}"]`).value = meta.default_dir[k] ?? 'both';
  }

  const missing = [];
  for (const [k, [v, tol, dir]] of Object.entries(p.targets)) {
    const cb = box.querySelector(`[data-t="${k}"]`);
    if (!cb) { missing.push(k); continue; }
    cb.checked = true;
    cb.closest('.target-item').classList.add('on');
    box.querySelector(`[data-tv="${k}"]`).value = v;
    box.querySelector(`[data-tt="${k}"]`).value = tol;
    box.querySelector(`[data-td="${k}"]`).value = dir;
  }
  // 预设写死在前端，改了 config.PARAMS 就可能对不上 —— 出问题要看得见
  if (missing.length) toast(`预设中有本系统不支持的指标：${missing.join('、')}`, true);

  clear($('#cmpResult'));
}

// ---------------------------------------------------------------- 输入区

function renderMethods() {
  const box = clear($('#cmpMethods'));
  const all = [
    { ...methods.baseline, baseline: true },
    ...methods.objectives.map((o) => ({ ...o, baseline: false })),
  ];
  for (const m of all) {
    const box2 = el('input', { type: 'checkbox', 'data-m': m.key });
    box2.checked = true;               // 默认全选，对比越全越有说服力
    box.append(el('label', { class: `method-item ${m.baseline ? 'base' : ''}` }, [
      box2,
      el('div', {}, [
        el('div', { class: 'mi-name' }, [
          m.label,
          m.baseline ? el('span', { class: 'tag lim', text: '对照组' }) : null,
        ]),
        el('div', { class: 'mi-desc', text: m.desc }),
      ]),
    ]));
  }
}

/** 目标指标输入 —— 与配料页同构，保证两页填出来的规格含义一致。 */
function renderTargets() {
  const box = $('#cmpTargets');
  if (box.childElementCount) return;
  const on = { [meta.target_col]: '' };

  for (const key of meta.targetable) {
    const p = meta.params.find((x) => x.key === key);
    if (!p) continue;
    const checked = Object.hasOwn(on, key);
    const cb = el('input', {
      type: 'checkbox', 'data-t': key,
      onchange: (e) => e.currentTarget.closest('.target-item')
        .classList.toggle('on', e.currentTarget.checked),
    });
    if (checked) cb.setAttribute('checked', '');
    const dir = el('select', { class: 'tdir', 'data-td': key,
      onclick: (e) => e.stopPropagation() }, [
      el('option', { value: 'min', text: '≥' }),
      el('option', { value: 'both', text: '±' }),
      el('option', { value: 'max', text: '≤' }),
    ]);
    dir.value = meta.default_dir[key] ?? 'both';

    box.append(el('label', { class: `target-item ${checked ? 'on' : ''}` }, [
      cb,
      el('span', { class: 'tname' }, [p.label, p.unit ? el('i', { text: ` ${p.unit}` }) : null]),
      dir,
      el('input', { type: 'number', step: 'any', class: 'tval', 'data-tv': key, placeholder: '目标值' }),
      el('span', { class: 'pm', text: '±' }),
      el('input', { type: 'number', step: 'any', min: '0', class: 'ttol', 'data-tt': key,
        placeholder: String(meta.default_tol[key] ?? '') }),
    ]));
  }
}

function renderPricing() {
  const box = clear($('#pricingRows'));
  for (const [i, p] of pricing.points.entries()) {
    box.append(el('div', { class: 'price-row' }, [
      el('span', { class: 'muted', text: '冻力' }),
      el('input', { type: 'number', step: '1', 'data-pb': i, value: p.bloom }),
      el('span', { class: 'muted', text: `→ ${pricing.currency}` }),
      el('input', { type: 'number', step: '0.5', 'data-pp': i, value: p.price }),
    ]));
  }
}

async function savePricing() {
  const rows = $('#pricingRows').children;
  const points = [];
  for (let i = 0; i < rows.length; i++) {
    points.push({
      bloom: +rows[i].querySelector('[data-pb]').value,
      price: +rows[i].querySelector('[data-pp]').value,
    });
  }
  try {
    pricing = await api.savePricing({ points });
    invalidate();
    renderPricing();
    toast('定价已保存，下次对比生效');
  } catch (e) {
    toast(e.message, true);
  }
}

function collect() {
  const targets = {};
  const errors = [];
  for (const cb of $('#cmpTargets').querySelectorAll('[data-t]')) {
    if (!cb.checked) continue;
    const k = cb.dataset.t;
    const v = $(`[data-tv="${k}"]`, $('#cmpTargets')).value.trim();
    const t = $(`[data-tt="${k}"]`, $('#cmpTargets')).value.trim();
    if (v === '') { errors.push('勾选的指标必须填目标值'); continue; }
    targets[k] = { value: +v, direction: $(`[data-td="${k}"]`, $('#cmpTargets')).value };
    if (t !== '') targets[k].tolerance = +t;
  }
  const weight = +$('#cmpWeight').value;
  if (!(weight > 0)) errors.push('需求量必须大于 0');
  if (!Object.keys(targets).length) errors.push('至少勾选一项目标指标');

  const ms = [...$('#cmpMethods').querySelectorAll('[data-m]')]
    .filter((x) => x.checked).map((x) => x.dataset.m);
  if (ms.length < 2) errors.push('至少勾选两个方案才能对比');

  return { orders: [{ name: $('#cmpName').value.trim() || '对比订单', weight, targets }],
           methods: ms, errors: [...new Set(errors)] };
}

// ---------------------------------------------------------------- 求解

async function run() {
  const submittedRevision = ++revision;
  last = null;
  const { orders, methods: ms, errors } = collect();
  const box = clear($('#cmpResult'));
  if (errors.length) { toast(errors[0], true); return; }

  const btn = $('#btnCompare');
  btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = '对比中…';
  box.append(el('div', { class: 'banner warn' }, [
    el('span', { class: 'spinner' }),
    `正在跑 ${ms.length} 个方案${$('#cmpPareto').checked ? ' + 帕累托前沿' : ''}，可能需要十几秒…`,
  ]));

  try {
    const r = await api.compare({ orders, methods: ms, with_pareto: $('#cmpPareto').checked });
    if (submittedRevision !== revision) return;
    last = r;
    clear(box);
    if (r.status === 'INFEASIBLE') {
      box.append(el('div', { class: 'banner err' }, [el('div', {}, [
        el('b', { text: '所有方案都无解　' }),
        el('div', { style: { marginTop: '4px' }, text: r.reason })])]));
      return;
    }
    render(box, r);
  } catch (e) {
    if (submittedRevision !== revision) return;
    clear(box).append(el('div', { class: 'banner err', text: `对比失败：${e.message}` }));
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}

// ---------------------------------------------------------------- 结果

function render(box, r) {
  const ok = r.方案.filter((x) => x.状态 === 'OK');
  const base = r.方案.find((x) => x.对照组 && x.状态 === 'OK');

  box.append(el('div', { class: 'banner warn' }, [el('div', {}, [
    el('b', { text: '口径说明' }),
    ...r.口径说明.map((s) => el('div', { style: { marginTop: '3px' }, text: `· ${s}` })),
  ])]));

  scorecard(box, r, ok, base);
  metricBars(box, ok);
  // 前沿退化成 1 个点是**正常结果**而不是故障：凸定价下最便宜的方案往往
  // 同时也是批次最少的，其余点全被支配。但静默不画图会让用户以为功能坏了 ——
  // 他勾了选项、多等了几十秒，得说清楚为什么没有图。
  if (r.帕累托?.length > 1) pareto(box, r.帕累托);
  else if (r.帕累托?.length === 1) degeneratePareto(box, r.帕累托[0]);
  inventoryImpact(box, ok);
  redundancy(box, ok);
  picksDiff(box, ok);
}

/** 记分卡：每方案一列，各维最优高亮，对照组作基准显示增减。 */
function scorecard(box, r, ok, base) {
  const rows = [
    { k: '是否达标', get: (s) => s.全部达标, fmt: (v) => v ? '✓ 达标' : '✗ 未达标', best: null },
    { k: '料本合计（元）', get: (s) => s.料本, fmt: money, best: 'min' },
    { k: '单位成本（元/kg）', get: (s) => s.单位成本, fmt: (v) => `${fmt(v, 2)}`, best: 'min' },
    { k: '冻力冗余折价', get: (s) => s.冻力冗余折价, fmt: money, best: 'min', hint: '交付高于要求的部分，白送的价值' },
    { k: '指标冗余合计', get: (s) => s.冗余合计占容差, fmt: (v) => `${fmt(v * 100, 0)}% 容差`, best: 'min' },
    { k: '批次数', get: (s) => s.批次数, fmt: (v) => `${v} 批`, best: 'min', hint: '每批一次投料/清洗/转运' },
    { k: '消化难用料', get: (s) => s.消化难用料kg, fmt: (v) => `${fmt(v, 0)} kg`, best: 'max', hint: '两端料，替代口径' },
    { k: '计算耗时', get: (s) => s.耗时秒, fmt: (v) => v < 1 ? `${fmt(v * 1000, 0)} ms` : `${fmt(v, 2)} s`, best: 'min' },
  ];

  const thead = el('thead', {}, el('tr', {}, [
    el('th', { class: 'left', text: '评估项' }),
    ...r.方案.map((x) => el('th', { class: x.对照组 ? 'base-col' : '' }, [
      x.名称, x.对照组 ? el('div', { class: 'muted', style: { fontSize: '11px' }, text: '基准' }) : null,
    ])),
  ]));

  const tbody = el('tbody');
  for (const row of rows) {
    const vals = r.方案.map((x) => x.状态 === 'OK' ? row.get(x.评分) : null);
    // 只在**达标**的方案里评最优：配不出合格品的方案再快再便宜也不算赢，
    // 否则贪心会因为「335ms 最快」拿一个绿框，读表的人会被带偏。
    const nums = r.方案
      .map((x, i) => (x.状态 === 'OK' && x.评分.全部达标 ? vals[i] : null))
      .filter((v) => typeof v === 'number');
    const bestVal = !nums.length ? null
      : row.best === 'min' ? Math.min(...nums)
      : row.best === 'max' ? Math.max(...nums) : null;

    tbody.append(el('tr', {}, [
      el('td', { class: 'left' }, [row.k,
        row.hint ? el('div', { class: 'muted', style: { fontSize: '11px' }, text: row.hint }) : null]),
      ...r.方案.map((x, i) => {
        if (x.状态 !== 'OK') {
          return el('td', { class: 'muted', title: x.reason ?? '' }, x.状态 === 'INFEASIBLE' ? '无解' : '异常');
        }
        const v = vals[i];
        const isBest = bestVal !== null && x.评分.全部达标
          && typeof v === 'number' && Math.abs(v - bestVal) < 1e-9;
        const cell = [el('b', { text: row.fmt(v) })];
        if (base && !x.对照组 && typeof v === 'number' && row.best) {
          const bv = row.get(base.评分);
          if (typeof bv === 'number' && bv !== 0) {
            const d = (v / bv - 1) * 100;
            const good = row.best === 'min' ? d < 0 : d > 0;
            if (Math.abs(d) >= 0.05) {
              cell.push(el('div', { class: `delta ${good ? 'good' : 'bad'}`,
                text: `${d > 0 ? '+' : ''}${fmt(d, 1)}%` }));
            }
          }
        }
        return el('td', { class: `num ${isBest ? 'best' : ''} ${x.对照组 ? 'base-col' : ''}` }, cell);
      }),
    ]));
  }

  box.append(el('section', { class: 'card' }, [
    el('div', { class: 'card-head' }, [
      el('h2', { text: '评估记分卡' }),
      el('span', { class: 'muted', style: { fontSize: '12px' },
        // 必须写明「达标方案中」：贪心配不出合格品却可能料本更低、耗时更短，
        // 读表的人看到更小的数字没标绿，会怀疑表算错了。
        text: '绿框 = 达标方案中的最优（未达标方案不参与评选）；百分比为相对对照组的增减' }),
    ]),
    el('div', { class: 'table-wrap' }, el('table', { class: 'data-table score-table' }, [thead, tbody])),
  ]));
}

/** 四维归一化对比条。同一指标内部按最大值归一，跨指标不可比 —— 已在标题说明。 */
function metricBars(box, ok) {
  const P = palette();
  const dims = [
    { k: '料本', get: (s) => s.料本, fmt: money, better: 'min' },
    { k: '指标冗余', get: (s) => s.冗余合计占容差, fmt: (v) => `${fmt(v * 100, 0)}%`, better: 'min' },
    { k: '批次数', get: (s) => s.批次数, fmt: (v) => `${v} 批`, better: 'min' },
    { k: '消化难用料', get: (s) => s.消化难用料kg, fmt: (v) => `${fmt(v, 0)} kg`, better: 'max' },
    { k: '计算耗时', get: (s) => s.耗时秒, fmt: (v) => v < 1 ? `${fmt(v * 1000, 0)}ms` : `${fmt(v, 1)}s`, better: 'min' },
  ];

  const grid = el('div', { class: 'dim-grid' });
  const pass = ok.filter((x) => x.评分.全部达标);
  for (const d of dims) {
    const vals = ok.map((x) => d.get(x.评分));
    const max = Math.max(...vals, 1e-9);
    // 同记分卡：只在达标方案里评最优
    const pv = pass.map((x) => d.get(x.评分));
    const best = !pv.length ? null
      : d.better === 'min' ? Math.min(...pv) : Math.max(...pv);
    grid.append(el('div', { class: 'dim-box' }, [
      el('div', { class: 'dim-title' }, [d.k,
        el('span', { class: 'muted', text: d.better === 'min' ? ' 越低越好' : ' 越高越好' })]),
      ...ok.map((x, i) => {
        const v = vals[i];
        const isBest = best !== null && x.评分.全部达标 && Math.abs(v - best) < 1e-9;
        return el('div', { class: 'dim-row' }, [
          el('span', { class: 'dr-name', title: x.评分.全部达标 ? '' : '该方案未达标',
            text: x.评分.全部达标 ? x.名称 : `${x.名称} ✗` }),
          el('div', { class: 'dr-track' }, el('i', {
            style: { width: `${Math.max(v / max * 100, 1.5)}%`,
                     background: isBest ? P.accent : P.dark ? '#3a434e' : '#c7cfd8' },
          })),
          el('span', { class: `dr-val ${isBest ? 'best' : ''}`, text: d.fmt(v) }),
        ]);
      }),
    ]));
  }
  box.append(el('section', { class: 'card viz-root' }, [
    el('div', { class: 'card-head' }, [el('h2', { text: '各维度对比' }),
      el('span', { class: 'muted', style: { fontSize: '12px' }, text: '每格内部按最大值归一，跨格不可比' })]),
    grid,
  ]));
}

/** 前沿只剩一个点时的说明卡 —— 不画图，但要讲清楚原因。 */
function degeneratePareto(box, p) {
  box.append(el('section', { class: 'card' }, [
    el('div', { class: 'card-head' }, el('h2', { text: '成本 ↔ 批次数 权衡前沿' })),
    el('div', { style: { padding: '16px 18px' } }, [
      el('div', { class: 'banner ok' }, [el('div', {}, [
        el('b', { text: '本次没有权衡空间 —— 这是好事' }),
        el('div', { style: { marginTop: '4px' } },
          `最省钱的方案（${p.实际批次} 批，料本 ${money(p.料本)}）同时也是批次最少的，`
          + '多用批次并不会更便宜，所以前沿上只有这一个点，无需取舍。'),
      ])]),
      el('p', { class: 'muted', style: { margin: '10px 0 0', fontSize: '13px' } },
        '出现权衡的典型场景：便宜料分散在冻力两端（例如呆滞料被折价），'
        + '此时想省钱就得多开几个批次去凑，成本与批次数才会互相拉扯。'),
    ]),
  ]));
}

/** 帕累托前沿：批次数 ↔ 料本 的权衡。 */
function pareto(box, pts) {
  const P = palette();
  const host = el('div', { class: 'viz-root' });
  const W = 800, H = 260, m = { t: 18, r: 20, b: 38, l: 76 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const xs = pts.map((p) => p.实际批次), ys = pts.map((p) => p.料本);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const y0 = Math.min(...ys) * 0.998, y1 = Math.max(...ys) * 1.002;
  const sx = (v) => m.l + (v - x0) / (x1 - x0 || 1) * iw;
  const sy = (v) => m.t + ih - (v - y0) / (y1 - y0 || 1) * ih;

  const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, width: '100%', height: H,
    role: 'img', 'aria-label': '批次数与料本的权衡前沿' });
  for (let i = 0; i <= 3; i++) {
    const v = y0 + (y1 - y0) / 3 * i;
    svg.append(svgEl('line', { x1: m.l, x2: m.l + iw, y1: sy(v), y2: sy(v), stroke: P.grid }));
    svg.append(svgEl('text', { x: m.l - 8, y: sy(v) + 4, 'text-anchor': 'end', fill: P.mute,
      'font-size': 11 }, money(v)));
  }
  svg.append(svgEl('path', {
    d: pts.map((p, i) => `${i ? 'L' : 'M'}${sx(p.实际批次)},${sy(p.料本)}`).join(''),
    fill: 'none', stroke: P.accent, 'stroke-width': 2, 'stroke-linejoin': 'round',
  }));
  for (const p of pts) {
    svg.append(svgEl('circle', { cx: sx(p.实际批次), cy: sy(p.料本), r: 5, fill: P.accent,
      stroke: P.dark ? '#171b21' : '#fff', 'stroke-width': 2 }));
    svg.append(svgEl('text', { x: sx(p.实际批次), y: H - 12, 'text-anchor': 'middle',
      fill: P.mute, 'font-size': 11 }, `${p.实际批次}批`));
  }
  host.append(svg);

  box.append(el('section', { class: 'card' }, [
    el('div', { class: 'card-head' }, [el('h2', { text: '权衡前沿：批次数 ↔ 料本' })]),
    el('p', { class: 'hint', html: '每个点是「最多用 N 批时，料本最低能到多少」。' +
      '曲线越陡说明<b>多用一批能省下越多钱</b>；曲线走平说明再加批次已无收益。' }),
    host,
  ]));
}

/** 库存影响：每个方案啃掉了哪一段库存。 */
function inventoryImpact(box, ok) {
  const segs = ['低冻力料', '中间料', '高冻力料'];
  const thead = el('thead', {}, el('tr', {}, [
    el('th', { class: 'left', text: '方案' }),
    ...segs.flatMap((s) => [el('th', { text: `${s} 消耗` }), el('th', { text: '剩余' })]),
  ]));
  const tbody = el('tbody', {}, ok.map((x) => el('tr', {}, [
    el('td', { class: 'left', text: x.名称 }),
    ...segs.flatMap((s) => {
      const v = x.库存影响[s];
      return [
        el('td', { class: 'num' }, v.消耗吨 > 0
          ? el('b', { text: `${fmt(v.消耗吨, 2)} 吨` })
          : el('span', { class: 'muted', text: '—' })),
        el('td', { class: 'num muted', text: `${fmt(v.配料后吨, 1)} 吨` }),
      ];
    }),
  ])));

  box.append(el('section', { class: 'card' }, [
    el('div', { class: 'card-head' }, [el('h2', { text: '库存影响：各方案啃掉了哪一段' })]),
    el('p', { class: 'hint', html: '高冻力料稀缺（占库存不到一成），<b>被消耗多少直接决定后面还能不能接高牌号订单</b>。' }),
    el('div', { class: 'table-wrap' }, el('table', { class: 'data-table' }, [thead, tbody])),
  ]));
}

/** 指标冗余明细：每项超出客户要求多少。 */
function redundancy(box, ok) {
  const keys = [...new Set(ok.flatMap((x) => x.评分.冗余明细.map((d) => d.参数)))];
  if (!keys.length) return;
  const labelOf = (k) => meta.params.find((p) => p.key === k)?.label ?? k;

  const thead = el('thead', {}, el('tr', {}, [
    el('th', { class: 'left', text: '指标' }),
    el('th', { text: '客户要求' }),
    ...ok.map((x) => el('th', { text: x.名称 })),
  ]));
  const tbody = el('tbody');
  for (const k of keys) {
    const any = ok.map((x) => x.评分.冗余明细.find((d) => d.参数 === k)).find(Boolean);
    const sym = { min: '≥', max: '≤', both: '±' }[any?.方向] ?? '';
    tbody.append(el('tr', {}, [
      el('td', { class: 'left', text: labelOf(k) }),
      el('td', { class: 'num muted', text: `${sym} ${fmtAuto(any?.要求)}` }),
      ...ok.map((x) => {
        const d = x.评分.冗余明细.find((y) => y.参数 === k);
        if (!d) return el('td', { class: 'muted', text: '—' });
        return el('td', { class: 'num' }, [
          el('b', { text: fmtAuto(d.实际) }),
          el('div', { class: d['占容差%'] > 50 ? 'delta bad' : 'muted', style: { fontSize: '11px' },
            text: d.冗余 > 0 ? `冗余 ${fmtAuto(d.冗余)}（${fmt(d['占容差%'], 0)}%）` : '无冗余' }),
        ]);
      }),
    ]));
  }
  box.append(el('section', { class: 'card' }, [
    el('div', { class: 'card-head' }, [el('h2', { text: '指标冗余明细' })]),
    el('p', { class: 'hint', html: '冗余 = 交付指标<b>超出客户要求的部分</b>。冻力配到 213 而客户只要 210，那 3 个 Bloom 就是白送的。' }),
    el('div', { class: 'table-wrap' }, el('table', { class: 'data-table' }, [thead, tbody])),
  ]));
}

/** 用料单并排：哪几批多个方案都选中了。 */
function picksDiff(box, ok) {
  const all = new Map();
  for (const x of ok) {
    for (const o of x.订单) {
      for (const p of o.用料) {
        const rec = all.get(p.数据编号) ?? { id: p.数据编号, bloom: p[meta.target_col], by: {} };
        rec.by[x.方案] = (rec.by[x.方案] ?? 0) + p.取用kg;
        all.set(p.数据编号, rec);
      }
    }
  }
  const rows = [...all.values()].sort((a, b) => Object.keys(b.by).length - Object.keys(a.by).length
    || a.bloom - b.bloom);

  const thead = el('thead', {}, el('tr', {}, [
    el('th', { class: 'left', text: '批号' }), el('th', { text: '冻力' }),
    ...ok.map((x) => el('th', { text: x.名称 })),
  ]));
  const tbody = el('tbody', {}, rows.map((r) => el('tr', {}, [
    el('td', { class: 'left', text: `#${r.id}` }),
    el('td', { class: 'num', text: fmtAuto(r.bloom) }),
    ...ok.map((x) => r.by[x.方案]
      ? el('td', { class: 'num' }, el('b', { text: `${fmt(r.by[x.方案], 0)} kg` }))
      : el('td', { class: 'muted', text: '—' })),
  ])));

  box.append(el('section', { class: 'card' }, [
    el('div', { class: 'card-head' }, [
      el('h2', { text: '用料单对照' }),
      el('span', { class: 'muted', style: { fontSize: '12px' }, text: `共涉及 ${rows.length} 个批号` }),
      el('div', { class: 'toolbar' }, el('button', {
        class: 'btn ghost', text: '导出 CSV', onclick: () => exportCsv(ok, rows) })),
    ]),
    el('div', { class: 'table-wrap', style: { maxHeight: '460px', overflowY: 'auto' } },
      el('table', { class: 'data-table' }, [thead, tbody])),
  ]));
}

function exportCsv(ok, rows) {
  const lines = ['方案对比'];
  lines.push(['评估项', ...ok.map((x) => x.名称)].join(','));
  const m = [
    ['料本', (s) => s.料本], ['冻力冗余折价', (s) => s.冻力冗余折价],
    ['指标冗余占容差', (s) => s.冗余合计占容差], ['批次数', (s) => s.批次数],
    ['消化难用料kg', (s) => s.消化难用料kg], ['耗时秒', (s) => s.耗时秒],
    ['全部达标', (s) => s.全部达标 ? '是' : '否'],
  ];
  for (const [k, g] of m) lines.push([k, ...ok.map((x) => g(x.评分))].join(','));
  lines.push('');
  lines.push(['批号', '冻力', ...ok.map((x) => x.名称)].join(','));
  for (const r of rows) {
    lines.push([r.id, r.bloom, ...ok.map((x) => r.by[x.方案] ?? '')].join(','));
  }
  const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = el('a', { href: url, download: `方案对比_${new Date().toISOString().slice(0, 10)}.csv` });
  document.body.append(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  toast('已导出 CSV');
  void last;
}
