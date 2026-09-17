// 配料求解页：多目标输入 → 求解 → 用料单与达成情况。

import { api } from './api.js';
import { $, el, clear, fmt, fmtAuto, toast } from './ui.js';

let meta = null;
let rowSeq = 0;
let inputRevision = 0;
function invalidateResult() {
  inputRevision++;
  const result = $("#solveResult");
  if (result.children.length) clear(result).append(el("div", {class:"banner warn",text:"输入已更改，请重新生成配料方案。"}));
}

export function init(paramsMeta) {
  meta = paramsMeta;

  $('#optMinTake').value    = meta.settings.min_take ?? 20;
  $('#optMaxBatches').value = meta.settings.max_batches ?? 0;
  $('#optTimeLimit').value  = meta.settings.time_limit ?? 15;
  $('#optWholeBatch').checked = meta.settings.whole_batch !== false;
  $('#optWeightTol').value  = meta.settings.weight_tol_pct ?? 5;
  $('#optWholeBatch').addEventListener('change', syncBatchMode);
  syncBatchMode();

  $('#btnAddOrder').addEventListener('click', () => { invalidateResult(); addRow(); });
  $('#btnDemoOrder').addEventListener('click', () => {
    invalidateResult(); clear($('#orderList')); rowSeq = 0;
    addRow({name:'演示订单 · 食用明胶',weight:3000,targets:{[meta.target_col]:210}});
    $('#optWholeBatch').checked = false; syncBatchMode();
    $('#optMinTake').value = 20; $('#optMaxBatches').value = 0; $('#optTimeLimit').value = 15;
    toast('已载入 3,000 kg / 210 Bloom 样例，可修改后点击生成配料方案');
  });
  $('#orderList').addEventListener('input', invalidateResult);
  $('#orderList').addEventListener('change', invalidateResult);
  $('#orderList').addEventListener('click', e => { if (e.target.closest('button')) invalidateResult(); });
  for (const id of ['optWholeBatch','optWeightTol','optMinTake','optMaxBatches','optTimeLimit']) $('#'+id).addEventListener('input', invalidateResult);
  $('#btnClearOrders').addEventListener('click', () => {
    invalidateResult();
    clear($('#orderList'));
    rowSeq = 0;
    addRow();
  });
  $('#btnSolve').addEventListener('click', solve);

  addRow();
}

/** 两个模式各自有意义的选项不同：整批取料下最小取用量无意义，总量容差才是关键。 */
function syncBatchMode() {
  const whole = $('#optWholeBatch').checked;
  $('#wrapWeightTol').style.display = whole ? '' : 'none';
  $('#wrapMinTake').style.display = whole ? 'none' : '';
}

// ---------------------------------------------------------------- 订单卡

/** 可作为目标的指标定义（按 config 声明顺序）。 */
function targetableParams() {
  return meta.targetable.map((k) => meta.params.find((p) => p.key === k)).filter(Boolean);
}

function defaultTol(key) {
  if (key === meta.target_col) return meta.settings.bloom_tolerance ?? meta.default_tol[key];
  return meta.default_tol[key];
}

function addRow(preset = {}) {
  rowSeq += 1;
  const n = rowSeq;
  // 默认只勾选冻力 —— 它是客户规格的主项，其余按需开启
  const on = preset.targets ?? { [meta.target_col]: '' };

  const grid = el('div', { class: 'target-grid' }, targetableParams().map((p) => {
    const checked = Object.hasOwn(on, p.key);
    const box = el('input', {
      type: 'checkbox', 'data-t': p.key,
      onchange: (e) => {
        const cell = e.currentTarget.closest('.target-item');
        cell.classList.toggle('on', e.currentTarget.checked);
        if (e.currentTarget.checked) cell.querySelector('[data-tv]').focus();
      },
    });
    if (checked) box.setAttribute('checked', '');

    // 约束方向 —— 对应客户规格单上的符号。冻力默认「≥」：配低了就不合格
    const dir = el('select', {
      class: 'tdir', 'data-td': p.key, title: '约束方向',
      onclick: (e) => e.stopPropagation(),
    }, [
      el('option', { value: 'min',  text: '≥' }),
      el('option', { value: 'both', text: '±' }),
      el('option', { value: 'max',  text: '≤' }),
    ]);
    dir.value = meta.default_dir[p.key] ?? 'both';

    return el('label', { class: `target-item ${checked ? 'on' : ''}` }, [
      box,
      el('span', { class: 'tname' }, [p.label, p.unit ? el('i', { text: ` ${p.unit}` }) : null]),
      dir,
      el('input', {
        type: 'number', step: 'any', class: 'tval', 'data-tv': p.key,
        placeholder: '目标值', value: on[p.key] ?? '',
      }),
      el('span', { class: 'pm', text: '±' }),
      el('input', {
        type: 'number', step: 'any', min: '0', class: 'ttol', 'data-tt': p.key,
        placeholder: String(defaultTol(p.key) ?? ''),
      }),
    ]);
  }));

  const card = el('div', { class: 'order-block', 'data-row': n }, [
    el('div', { class: 'order-line' }, [
      el('span', { class: 'order-no', text: `订单 ${n}` }),
      el('label', {}, ['单号 / 备注 ', el('input', {
        type: 'text', placeholder: `订单${n}`, value: preset.name ?? '', 'data-f': 'name',
      })]),
      el('label', {}, ['需求量 ', el('input', {
        type: 'number', placeholder: '例如 800', step: '1', min: '1',
        value: preset.weight ?? '', 'data-f': 'weight',
      }), ' kg']),
      el('label', { title: '同样达标的方案里，优先动用哪一端的库存' }, ['取料偏好 ',
        el('select', { 'data-f': 'pref' }, Object.entries(meta.material_prefs).map(([k, v]) =>
          el('option', { value: k, text: v }))),
      ]),
      el('button', {
        class: 'btn danger', title: '删除该订单', text: '✕',
        onclick: (e) => {
          if ($('#orderList').children.length <= 1) { toast('至少保留一张订单'); return; }
          e.currentTarget.closest('.order-block').remove();
        },
      }),
    ]),
    el('div', { class: 'target-title', text: '目标指标（勾选客户规格单上写明的项）' }),
    grid,
  ]);

  $('#orderList').append(card);
  return card;
}

function collectOrders() {
  const orders = [];
  const errors = [];

  for (const card of $('#orderList').children) {
    const get = (f) => card.querySelector(`[data-f="${f}"]`).value.trim();
    const weight = get('weight');
    const name = get('name');

    const targets = {};
    for (const box of card.querySelectorAll('[data-t]')) {
      if (!box.checked) continue;
      const key = box.dataset.t;
      const v = card.querySelector(`[data-tv="${key}"]`).value.trim();
      const t = card.querySelector(`[data-tt="${key}"]`).value.trim();
      if (v === '') {
        errors.push(`「${name || '未命名订单'}」勾选了${labelOf(key)}但没填目标值`);
        continue;
      }
      targets[key] = { value: +v, direction: card.querySelector(`[data-td="${key}"]`).value };
      if (t !== '') targets[key].tolerance = +t;
    }

    const hasAny = weight !== '' || Object.keys(targets).length > 0;
    if (!hasAny) continue;                       // 整张卡留空就跳过

    if (weight === '' || !(+weight > 0)) {
      errors.push(`「${name || '未命名订单'}」需求量必须填写且大于 0`);
      continue;
    }
    if (!Object.keys(targets).length) {
      errors.push(`「${name || '未命名订单'}」至少要勾选一项目标指标`);
      continue;
    }
    orders.push({
      name, weight: +weight, targets,
      material_pref: card.querySelector('[data-f="pref"]').value,
    });
  }
  return { orders, errors: [...new Set(errors)] };
}

/** 把「目标值 + 容差 + 方向」渲染成规格单上的写法。 */
function reqText(a) {
  if (a.类型 !== 'target') return `${a.下限 ?? '—'} ~ ${a.上限 ?? '—'}`;
  if (a.方向 === 'min') return `≥ ${fmtAuto(a.目标)}`;
  if (a.方向 === 'max') return `≤ ${fmtAuto(a.目标)}`;
  return `${fmtAuto(a.目标)} ±${fmtAuto(a.容差)}`;
}

const labelOf = (k) => meta.params.find((p) => p.key === k)?.label ?? k;
const unitOf  = (k) => meta.params.find((p) => p.key === k)?.unit ?? '';

// ---------------------------------------------------------------- 求解

async function solve() {
  const revision = inputRevision;
  const { orders, errors } = collectOrders();
  const box = clear($('#solveResult'));

  if (errors.length) { toast(errors[0], true); return; }
  if (!orders.length) { toast('请至少填写一个目标冻力', true); return; }

  const btn = $('#btnSolve');
  btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = '求解中…';
  box.append(el('div', { class: 'banner warn' }, [el('span', { class: 'spinner' }), '正在求解，多订单争抢同一冻力段时可能需要十几秒…']));

  const whole = $('#optWholeBatch').checked;
  const settings = {
    whole_batch:    whole,
    weight_tol_pct: +$('#optWeightTol').value || 0,
    min_take:       +$('#optMinTake').value || 0,
    max_batches:    +$('#optMaxBatches').value || 0,
    time_limit:     +$('#optTimeLimit').value || 15,
  };

  try {
    const r = await api.blend({ orders, settings });
    if (revision !== inputRevision) return;
    clear(box);
    if (r.status === 'INFEASIBLE') renderInfeasible(box, r);
    else renderResult(box, r);
  } catch (e) {
    clear(box).append(el('div', { class: 'banner err', text: `求解失败：${e.message}` }));
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}

function renderInfeasible(box, r) {
  box.append(el('div', { class: 'banner err' }, [
    el('div', {}, [
      el('b', { text: '无可行方案　' }),
      el('div', { style: { marginTop: '4px' }, text: r.reason }),
      el('div', { class: 'muted', style: { marginTop: '6px', fontSize: '13px' },
        text: '可尝试：放宽「参数设定」页的上下限、加大冻力容差、或降低需求量。' }),
    ]),
  ]));
}

// ---------------------------------------------------------------- 结果渲染

function renderResult(box, r) {
  box.append(el('div', { class: `banner ${r.全部达标 ? 'ok' : 'err'}` }, [
    el('div', {}, [
      el('b', { text: r.全部达标 ? '求解成功，全部订单达标' : '已求出方案，但存在超差项' }),
      el('div', { style: { marginTop: '4px' } , text:
        `${r.订单数} 张订单 · 共用 ${r.总批次数} 批物料 · 合计 ${fmt(r.总配料量kg, 2)} kg · ` +
        `耗时 ${r.耗时秒}s · 候选池 ${r.候选池.toLocaleString('zh-CN')} 批` }),
      ...r.warnings.map((w) => el('div', { class: 'muted', style: { marginTop: '4px' }, text: `⚠ ${w}` })),
    ]),
  ]));

  if (r.共用批次?.length) {
    box.append(el('div', { class: 'banner warn' }, [
      el('div', {}, [
        el('b', { text: `${r.共用批次.length} 批物料被多张订单同时取用` }),
        el('div', { style: { marginTop: '4px' }, text:
          r.共用批次.map((s) => `#${s.数据编号}（${s.被订单取用.join('、')}，共 ${fmt(s.合计取用kg, 1)}kg / 库存 ${fmt(s.库存kg, 1)}kg）`).join('；') }),
        el('div', { class: 'muted', style: { marginTop: '4px', fontSize: '13px' },
          text: '总量未超库存，但车间分料时需注意先后顺序。' }),
      ]),
    ]));
  }

  for (const o of r.订单) box.append(orderCard(o));
}

function orderCard(o) {
  // 摘要里只列该单实际指定的目标项，没指定的指标不占位置
  const targets = o.达成.filter((a) => a.类型 === 'target');

  const head = el('div', { class: 'card-head' }, [
    el('h2', { text: o.单号 }),
    el('span', { class: `badge ${o.全部达标 ? 'ok' : 'bad'}`, text: o.全部达标 ? '达标' : '超差' }),
    el('div', { class: 'order-meta' }, [
      ...targets.map((a) => el('span', {}, [
        `${labelOf(a.参数)} `, el('b', { text: reqText(a) }),
      ])),
      o.取料偏好 && o.取料偏好 !== 'balanced'
        ? el('span', { class: 'muted', text: meta.material_prefs[o.取料偏好] })
        : null,
      // 整批取料下实配量不会精确等于需求量，差多少必须显在脸上 ——
      // 车间照单投料、销售按实配量开票，这个数字藏起来会出事。
      el('span', {}, ['配料量 ', el('b', { text: `${fmt(o.实配量kg, 2)} kg` }),
        ...(Math.abs(o.实配量kg - o.需求量kg) > 0.01 ? [
          el('span', {
            class: `qty-dev ${Math.abs(o.实配量kg / o.需求量kg - 1) > 0.02 ? 'warn' : ''}`,
            title: `需求 ${fmt(o.需求量kg, 2)} kg`,
            text: ` ${o.实配量kg > o.需求量kg ? '+' : ''}${fmt(o.实配量kg - o.需求量kg, 1)}`
              + ` (${o.实配量kg > o.需求量kg ? '+' : ''}${fmt((o.实配量kg / o.需求量kg - 1) * 100, 2)}%)`,
          }),
        ] : []),
      ]),
      el('span', {}, ['批次数 ', el('b', { text: String(o.批次数) })]),
    ]),
    el('div', { class: 'toolbar' }, [
      el('button', { class: 'btn ghost', text: '导出 CSV', onclick: () => exportCsv(o) }),
    ]),
  ]);

  return el('section', { class: `card order-card ${o.全部达标 ? '' : 'bad'}` }, [
    head,
    el('div', { class: 'split' }, [
      el('div', {}, [el('div', { class: 'sec-title', text: '用料单' }), pickTable(o)]),
      el('div', {}, [el('div', { class: 'sec-title', text: '规格达成情况' }), achieveTable(o)]),
    ]),
  ]);
}

function pickTable(o) {
  // 明细列只展示该单的目标指标 —— 全部限制项都列出来表会宽到没法看，
  // 右侧「规格达成情况」已经覆盖了限制项。
  const extra = o.达成.filter((a) => a.类型 === 'target').map((a) => a.参数);

  const thead = el('thead', {}, el('tr', {}, [
    el('th', { class: 'left', text: '编号' }),
    el('th', { text: '取用kg' }),
    el('th', { text: '库存kg' }),
    el('th', { text: '占比%' }),
    ...extra.map((c) => el('th', { text: labelOf(c) })),
  ]));

  const tbody = el('tbody', {}, o.用料.map((p) =>
    el('tr', {}, [
      el('td', { class: 'left', text: `#${p.数据编号}` }),
      el('td', { class: 'num', text: fmt(p.取用kg, 2) }),
      // 整批取料时取用量 == 库存量，标出来让车间一眼确认「这批清空」
      el('td', { class: 'num muted' }, [
        fmt(p.库存kg, 1),
        Math.abs(p.取用kg - p.库存kg) < 0.02
          ? el('span', { class: 'whole-tag', title: '整批用完', text: ' 清空' }) : null,
      ]),
      el('td', { class: 'num', text: fmt(p.占比, 1) }),
      ...extra.map((c) => el('td', { class: 'num', text: fmtAuto(p[c]) })),
    ])));

  tbody.append(el('tr', {}, [
    el('td', { class: 'left', html: '<b>合计</b>' }),
    el('td', { class: 'num', html: `<b>${fmt(o.实配量kg, 2)}</b>` }),
    el('td', {}),
    el('td', { class: 'num', html: '<b>100.0</b>' }),
    ...extra.map(() => el('td', {})),
  ]));

  return el('div', { class: 'table-wrap' }, el('table', { class: 'data-table' }, [thead, tbody]));
}

function achieveTable(o) {
  const thead = el('thead', {}, el('tr', {}, [
    el('th', { class: 'left', text: '指标' }),
    el('th', { text: '实际值' }),
    el('th', { text: '要求' }),
    el('th', { class: 'left', text: '达成' }),
  ]));

  const tbody = el('tbody', {}, o.达成.map((a) => {
    const isTarget = a.类型 === 'target';
    const req = reqText(a);

    let cell;
    if (isTarget) {
      const ratio = Math.min(a.占容差 ?? 0, 1);
      const cls = !a.达标 ? 'bad' : ratio > 0.75 ? 'warn' : '';
      cell = el('div', { style: { display: 'flex', alignItems: 'center', gap: '8px' } }, [
        el('div', { class: `bar ${cls}`, style: { flex: '1' } }, el('i', { style: { width: `${ratio * 100}%` } })),
        el('span', { class: 'muted', style: { fontSize: '12px', whiteSpace: 'nowrap' },
          text: `偏差 ${a.偏差 > 0 ? '+' : ''}${fmtAuto(a.偏差)}（用掉 ${Math.round((a.占容差 ?? 0) * 100)}% 容差）` }),
      ]);
    } else {
      cell = el('span', { class: a.达标 ? '' : 'badge bad', text: a.达标 ? '在区间内' : '超出区间' });
    }

    return el('tr', { class: a.达标 ? '' : 'row-bad' }, [
      el('td', { class: 'left' }, [
        el('span', { class: `tag ${isTarget ? 'tgt' : 'lim'}`, text: isTarget ? '目标' : '限制' }),
        labelOf(a.参数),
        unitOf(a.参数) ? el('span', { class: 'muted', text: ` ${unitOf(a.参数)}` }) : null,
      ]),
      el('td', { class: 'num', html: `<b>${fmtAuto(a.实际)}</b>` }),
      el('td', { class: 'num muted', text: req }),
      el('td', { class: 'left' }, cell),
    ]);
  }));

  return el('div', { class: 'table-wrap' }, el('table', { class: 'data-table' }, [thead, tbody]));
}

// ---------------------------------------------------------------- 导出

function exportCsv(o) {
  const targets = o.达成.filter((a) => a.类型 === 'target');
  const cols = targets.map((a) => a.参数);
  const lines = [];

  lines.push(`配料单,${o.单号}`);
  lines.push(`配料量kg,${o.实配量kg},批次数,${o.批次数}`);
  for (const a of targets) {
    lines.push(`目标 ${a.参数},${reqText(a)},区间,${a.下限} ~ ${a.上限}`);
  }
  if (o.取料偏好 && o.取料偏好 !== 'balanced') {
    lines.push(`取料偏好,${meta.material_prefs[o.取料偏好]}`);
  }
  lines.push('');
  lines.push(['数据编号', '取用kg', '库存kg', '占比%', ...cols].join(','));
  for (const p of o.用料) {
    lines.push([p.数据编号, p.取用kg, p.库存kg, p.占比, ...cols.map((c) => p[c] ?? '')].join(','));
  }
  lines.push('');
  lines.push('指标,类型,实际值,下限,上限,达标');
  for (const a of o.达成) {
    lines.push([
      a.参数, a.类型 === 'target' ? '目标' : '限制',
      a.实际, a.下限 ?? '', a.上限 ?? '', a.达标 ? '是' : '否',
    ].join(','));
  }

  // BOM 保证 Excel 打开中文不乱码
  const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = el('a', { href: url, download: `配料单_${o.单号}_${new Date().toISOString().slice(0, 10)}.csv` });
  document.body.append(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  toast('已导出 CSV');
}
