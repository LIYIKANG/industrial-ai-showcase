// 历史回溯：2025 年人工实际配法 vs 算法重配。
//
// 页面要回答的只有一个问题：同样的料、同样的单，换个配法能好多少。
// 所以主图不画"算法有多好"，画的是**每张单离客户要求还差多少** ——
// 人工那一行散得很开（左边欠标、右边白送），算法那一行贴着 0 线。
// 一眼看到的是分布形状的差别，不是两个孤立的汇总数字。

import { api } from './api.js';
import { $, clear, el, empty, fmt, toast } from './ui.js';
import { palette, svgEl } from './charts.js';

const SPECS = [
  { key: 'bloom', label: '冻力', d: 1 },
  { key: 'viscosity', label: '粘度', d: 2 },
  { key: 't450', label: 'T450', d: 1 },
  { key: 't620', label: 'T620', d: 1 },
];

const CACHE = {};
let mode = 'pool';
let rowFilter = 'all';

// ---------------------------------------------------------------- 入口

export function init() {
  for (const b of document.querySelectorAll('#btMode .seg-btn')) {
    b.addEventListener('click', () => {
      if (b.dataset.mode === mode) return;
      for (const x of document.querySelectorAll('#btMode .seg-btn')) {
        x.classList.toggle('active', x === b);
      }
      mode = b.dataset.mode;
      load();
    });
  }
  $('#btFilter').addEventListener('change', (e) => {
    rowFilter = e.target.value;
    const d = CACHE[mode];
    if (d) renderTable(d);
  });
}

export async function load() {
  const host = $('#view-backtest');
  if (!CACHE[mode]) {
    $('#btKpi').replaceChildren(empty('正在求解 12 个月的分配问题，首次约 20 秒…'));
    try {
      CACHE[mode] = await api.backtest({ mode });
    } catch (e) {
      $('#btKpi').replaceChildren(el('div', { class: 'banner err', text: `回溯失败：${e.message}` }));
      return;
    }
  }
  const d = CACHE[mode];
  $('#btNote').textContent = d.口径说明;
  renderKpi(d);
  renderStrip(d);
  renderGrade(d);
  renderMonth(d);
  renderTable(d);
  host.dataset.ready = '1';
}

// ---------------------------------------------------------------- KPI

function renderKpi(d) {
  const t = d.总计;
  const box = clear($('#btKpi'));
  // 「好」由指标本身决定：达标数增加是好，欠标/过剩减少是好
  const delta = (v, betterWhenUp) => el('div', {
    class: `delta ${(v > 0) === betterWhenUp ? 'good' : 'bad'}`,
    text: `${v > 0 ? '+' : ''}${fmt(v, Number.isInteger(v) ? 0 : 1)}`,
  });

  const cards = [
    { k: '达标单数', v: `${t.人工达标单} → ${t.算法达标单}`, u: `/ ${t.单数} 单`,
      d: delta(t.算法达标单 - t.人工达标单, true) },
    { k: '达标吨位', v: `${fmt(t.人工达标吨, 0)} → ${fmt(t.算法达标吨, 0)}`, u: '吨',
      d: delta(t.算法达标吨 - t.人工达标吨, true) },
    { k: '欠标量（配不到客户要求的部分）', v: `${fmt(t.人工欠标, 0)} → ${fmt(t.算法欠标, 0)}`,
      u: 'bloom·吨', d: delta(t.算法欠标 - t.人工欠标, false) },
    { k: '过剩量（白送给客户的品质）', v: `${fmt(t.人工过剩, 0)} → ${fmt(t.算法过剩, 0)}`,
      u: 'bloom·吨', d: delta(t.算法过剩 - t.人工过剩, false) },
  ];
  for (const c of cards) {
    box.append(el('div', { class: 'stat' }, [
      el('div', { class: 'k', text: c.k }),
      el('div', { class: 'v' }, [c.v, el('span', { class: 'u', text: ` ${c.u}` })]),
      c.d,
    ]));
  }
  box.append(el('div', { class: 'stat' }, [
    el('div', { class: 'k', text: '省下的过剩品质（按占位价折算）' }),
    el('div', { class: 'v' }, [fmt(d.省下过剩bloom吨, 0),
      el('span', { class: 'u', text: ' bloom·吨' })]),
    el('div', { class: 'delta good', text: `≈ ${fmt(d.折合金额, 0)} ${d.币种.split('/')[0]}（价格为占位值）` }),
  ]));
}

// ---------------------------------------------------------------- 主图：余量分布

// 每张单一个点，横轴 = 实配冻力 − 客户要求。0 线左边是欠标，右边是白送。
// 点面积正比于吨位 —— 大单错得更贵，视觉上就该更重。
function renderStrip(d) {
  const host = clear($('#btStrip'));
  const P = palette();
  const rows = d.明细.filter((r) => r.req_src === '客户标准');
  if (!rows.length) return host.append(empty('无数据'));

  const W = host.clientWidth || 900;
  const H = 244;
  const m = { t: 30, r: 20, b: 40, l: 76 };
  const iw = W - m.l - m.r;

  // 少数被算法「主动放弃」的单能到 −100 以上，直接用全域会把主分布压成一条线。
  // 截断到稳健区间，越界的点钉在边缘画成三角，并在图上写明有几张。
  const vals = rows.flatMap((r) => [r.human.bloom - r.req.bloom, r.algo.bloom - r.req.bloom]);
  const sorted = [...vals].sort((a, b) => a - b);
  const q = (p) => sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * p))];
  const lo = Math.min(Math.max(q(0.01), -50), -12);
  const hi = Math.max(Math.min(q(0.995), 60), 20);
  const pad = (hi - lo) * 0.04;
  const sxRaw = (v) => m.l + (v - lo + pad) / (hi - lo + pad * 2) * iw;   // 刻度用，不截断
  const sx = (v) => sxRaw(Math.max(lo, Math.min(hi, v)));                 // 数据点用，越界钉边
  const nOut = vals.filter((v) => v < lo || v > hi).length;
  const maxW = Math.max(...rows.map((r) => r.W));
  const rad = (w) => 2.5 + Math.sqrt(w / maxW) * 7;

  const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, width: '100%', height: H,
    role: 'img', 'aria-label': '每张配料单的冻力余量分布：人工 vs 算法' });

  // 背景：欠标区染色，一眼分出左右
  svg.append(svgEl('rect', { x: m.l, y: m.t - 14, width: Math.max(sx(0) - m.l, 0),
    height: H - m.t - m.b + 26, fill: P.critical, opacity: 0.06 }));
  svg.append(svgEl('line', { x1: sx(0), x2: sx(0), y1: m.t - 14, y2: H - m.b + 12,
    stroke: P.ink, 'stroke-width': 1.5, 'stroke-dasharray': '3 3' }));
  svg.append(svgEl('text', { x: sx(0), y: m.t - 20, 'text-anchor': 'middle',
    fill: P.ink, 'font-size': 11, 'font-weight': 600 }, '客户要求线'));

  const lanes = [
    { key: 'human', label: '人工', y: m.t + 34 },
    { key: 'algo', label: '算法', y: m.t + 104 },
  ];
  for (const ln of lanes) {
    svg.append(svgEl('text', { x: m.l - 12, y: ln.y + 4, 'text-anchor': 'end',
      fill: P.ink, 'font-size': 13, 'font-weight': 600 }, ln.label));
    svg.append(svgEl('line', { x1: m.l, x2: m.l + iw, y1: ln.y, y2: ln.y,
      stroke: P.grid, 'stroke-width': 1 }));

    const ok = rows.filter((r) => r[`${ln.key}_ok`]).length;
    svg.append(svgEl('text', { x: m.l - 12, y: ln.y + 20, 'text-anchor': 'end',
      fill: P.mute, 'font-size': 11 }, `${ok}/${rows.length} 达标`));

    // 同一横坐标的点上下错开，避免重叠成一坨
    const buckets = new Map();
    for (const r of rows) {
      const v = r[ln.key].bloom - r.req.bloom;
      const x = sx(v);
      const b = Math.round(x / 7);
      const n = buckets.get(b) ?? 0;
      buckets.set(b, n + 1);
      const dy = (n % 2 ? 1 : -1) * Math.ceil(n / 2) * 5.5;
      const fail = !r[`${ln.key}_ok`];
      const out = v < lo || v > hi;
      const rr = rad(r.W);
      const cy = ln.y + dy;
      const c = out
        ? svgEl('path', {   // 越界：画成指向界外的三角，不冒充真实位置
            d: v < lo ? `M${x - rr - 2},${cy} L${x + rr},${cy - rr} L${x + rr},${cy + rr} Z`
                      : `M${x + rr + 2},${cy} L${x - rr},${cy - rr} L${x - rr},${cy + rr} Z`,
            fill: P.critical, 'fill-opacity': 0.85 })
        : svgEl('circle', {
            cx: x, cy, r: rr,
            fill: fail ? P.critical : P.accent,
            'fill-opacity': fail ? 0.75 : 0.5,
            stroke: fail ? P.critical : P.accent, 'stroke-width': 1, 'stroke-opacity': 0.9 });
      c.append(svgEl('title', {}, `${r.id}　规格${r.grade}　${fmt(r.W / 1000, 2)} 吨
要求冻力 ≥ ${r.req.bloom}
${ln.label}实配 ${fmt(r[ln.key].bloom, 1)}（${v >= 0 ? '+' : ''}${fmt(v, 1)}）
${r[`${ln.key}_fail`].length ? '不达标：' + r[`${ln.key}_fail`].join('、') : '达标'}`));
      svg.append(c);
    }
  }

  // 横轴
  const ticks = 7;
  for (let i = 0; i <= ticks; i++) {
    const v = lo - pad + (hi - lo + pad * 2) * i / ticks;
    svg.append(svgEl('text', { x: sxRaw(v), y: H - m.b + 17, 'text-anchor': 'middle',
      fill: P.mute, 'font-size': 11 }, (v > 0 ? '+' : '') + Math.round(v)));
  }
  svg.append(svgEl('text', { x: m.l + iw / 2, y: H - 6, 'text-anchor': 'middle',
    fill: P.dim, 'font-size': 11 },
    '实配冻力 − 客户要求（Bloom）　←　欠标　|　白送　→'
    + (nOut ? `　·　${nOut} 张超出显示范围，钉在两端画成三角` : '')));

  host.append(svg);
}

// ---------------------------------------------------------------- 按规格

// 人工把盈余堆在哪、又从哪抽走了 —— 这是整件事的机理，比总数更有说服力。
function renderGrade(d) {
  const host = clear($('#btGrade'));
  const P = palette();
  const gs = d.按规格;
  if (!gs.length) return host.append(empty('无数据'));

  const W = host.clientWidth || 600;
  const rowH = 42;
  const H = gs.length * rowH + 46;
  const m = { t: 26, r: 64, b: 20, l: 58 };
  const iw = W - m.l - m.r;

  const per = gs.map((g) => ({
    grade: g.规格,
    吨: g.吨位,
    人工: (g.人工过剩 - g.人工欠标) / g.吨位,
    算法: (g.算法过剩 - g.算法欠标) / g.吨位,
  }));
  const mx = Math.max(...per.flatMap((p) => [Math.abs(p.人工), Math.abs(p.算法)]), 5) * 1.15;
  const sx = (v) => m.l + iw / 2 + v / mx * (iw / 2);

  const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, width: '100%', height: H,
    role: 'img', 'aria-label': '各规格的平均冻力余量：人工 vs 算法' });
  svg.append(svgEl('line', { x1: sx(0), x2: sx(0), y1: m.t - 12, y2: H - m.b,
    stroke: P.ink, 'stroke-width': 1.5, 'stroke-dasharray': '3 3' }));
  svg.append(svgEl('text', { x: sx(0), y: m.t - 16, 'text-anchor': 'middle',
    fill: P.ink, 'font-size': 11, 'font-weight': 600 }, '恰好达标'));

  per.forEach((p, i) => {
    const y = m.t + i * rowH;
    svg.append(svgEl('text', { x: m.l - 10, y: y + 18, 'text-anchor': 'end',
      fill: P.ink, 'font-size': 12, 'font-weight': 600 }, `${p.grade}型`));
    [['人工', 0, P.dim], ['算法', 13, P.accent]].forEach(([k, dy, col]) => {
      const v = p[k];
      const x0 = Math.min(sx(0), sx(v));
      const w = Math.abs(sx(v) - sx(0));
      const bad = v < 0;
      const bar = svgEl('rect', { x: x0, y: y + dy, width: Math.max(w, 1), height: 11, rx: 2,
        fill: bad ? P.critical : col, 'fill-opacity': k === '人工' ? 0.55 : 0.9 });
      bar.append(svgEl('title', {}, `${p.grade}型　${k}　平均余量 ${v >= 0 ? '+' : ''}${fmt(v, 1)} Bloom　(${fmt(p.吨, 1)} 吨)`));
      svg.append(bar);
      svg.append(svgEl('text', { x: sx(v) + (v >= 0 ? 5 : -5), y: y + dy + 10,
        'text-anchor': v >= 0 ? 'start' : 'end', fill: P.mute, 'font-size': 10 },
        `${k} ${v >= 0 ? '+' : ''}${fmt(v, 1)}`));
    });
  });
  host.append(svg);
}

// ---------------------------------------------------------------- 按月

function renderMonth(d) {
  const host = clear($('#btMonth'));
  const P = palette();
  const ms = d.按月;
  const W = host.clientWidth || 700;
  const H = 220;
  const m = { t: 16, r: 14, b: 44, l: 40 };
  const iw = W - m.l - m.r;
  const ih = H - m.t - m.b;
  const y1 = Math.max(...ms.map((x) => x.单数)) * 1.12 || 1;
  const sy = (v) => m.t + ih - v / y1 * ih;
  const bw = iw / ms.length;

  const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, width: '100%', height: H,
    role: 'img', 'aria-label': '各月达标单数：人工 vs 算法' });
  for (let i = 0; i <= 4; i++) {
    const v = y1 / 4 * i;
    svg.append(svgEl('line', { x1: m.l, x2: m.l + iw, y1: sy(v), y2: sy(v),
      stroke: P.grid, 'stroke-width': 1 }));
    svg.append(svgEl('text', { x: m.l - 7, y: sy(v) + 4, 'text-anchor': 'end',
      fill: P.mute, 'font-size': 11 }, Math.round(v)));
  }
  ms.forEach((x, i) => {
    const x0 = m.l + i * bw;
    // 底色柱 = 当月总单数，前景两根 = 各自达标数，比例关系直接可读
    svg.append(svgEl('rect', { x: x0 + bw * 0.14, y: sy(x.单数), width: bw * 0.72,
      height: ih - (sy(x.单数) - m.t), rx: 2, fill: P.track }));
    [['人工达标单', 0.18, P.dim, 0.7], ['算法达标单', 0.52, P.accent, 1]].forEach(
      ([k, off, col, op]) => {
        const b = svgEl('rect', { x: x0 + bw * off, y: sy(x[k]), width: bw * 0.3,
          height: Math.max(ih - (sy(x[k]) - m.t), 0), rx: 2, fill: col, 'fill-opacity': op });
        b.append(svgEl('title', {}, `${x.月}　${k.slice(0, 2)} ${x[k]}/${x.单数} 单　${fmt(x.吨位, 1)} 吨`));
        svg.append(b);
      });
    svg.append(svgEl('text', { x: x0 + bw / 2, y: H - m.b + 16, 'text-anchor': 'middle',
      fill: P.mute, 'font-size': 11 }, typeof x.月 === 'number' ? `${x.月}月` : x.月));
  });
  // 图例
  [['人工', P.dim, 0.7], ['算法', P.accent, 1]].forEach(([t, c, o], i) => {
    const x = m.l + i * 70;
    svg.append(svgEl('rect', { x, y: H - 14, width: 11, height: 11, rx: 2, fill: c, 'fill-opacity': o }));
    svg.append(svgEl('text', { x: x + 16, y: H - 5, fill: P.dim, 'font-size': 11 }, t));
  });
  host.append(svg);
}

// ---------------------------------------------------------------- 明细表

function renderTable(d) {
  const host = clear($('#btTable'));
  let rows = d.明细;
  if (rowFilter === 'fixed') rows = rows.filter((r) => !r.human_ok && r.algo_ok);
  else if (rowFilter === 'still') rows = rows.filter((r) => !r.algo_ok);
  else if (rowFilter === 'std') rows = rows.filter((r) => r.req_src === '客户标准');

  $('#btCount').textContent = `${rows.length} 张`;
  if (!rows.length) return host.append(empty('没有符合条件的配料单'));

  const head = ['', '单号', '月/日', '规格', '规格来源', '吨位',
    ...SPECS.map((s) => `要求${s.label}`),
    ...SPECS.map((s) => `人工${s.label}`), '人工',
    ...SPECS.map((s) => `算法${s.label}`), '算法'];

  const table = el('table', { class: 'data-table' });
  table.append(el('thead', {}, el('tr', {}, head.map((h) => el('th', { text: h })))));
  const tb = el('tbody');
  for (const r of rows) {
    const cells = [
      el('td', { class: 'left chev', text: '▸' }),
      el('td', { class: 'left', text: r.id }),
      el('td', { class: 'left', text: r.day ? `${r.day}日` : '—' }),
      el('td', { class: 'num', text: r.grade ?? '—' }),
      el('td', { class: 'left' }, el('span', {
        class: `pill ${r.grade_src === '推测' ? 'warn' : ''}`, text: r.grade_src })),
      el('td', { class: 'num', text: fmt(r.W / 1000, 2) }),
      ...SPECS.map((s) => el('td', { class: 'num muted', text: fmt(r.req[s.key], s.d) })),
      ...SPECS.map((s) => el('td', {
        class: `num ${r.human[s.key] < r.req[s.key] ? 'bad' : ''}`,
        text: fmt(r.human[s.key], s.d) })),
      el('td', {}, el('span', { class: `pill ${r.human_ok ? 'ok' : 'no'}`,
        text: r.human_ok ? '达标' : (r.human_fail.join('·') || '不达标') })),
      ...SPECS.map((s) => el('td', {
        class: `num ${r.algo[s.key] < r.req[s.key] ? 'bad' : ''}`,
        text: fmt(r.algo[s.key], s.d) })),
      el('td', {}, el('span', { class: `pill ${r.algo_ok ? 'ok' : 'no'}`,
        text: r.algo_ok ? '达标' : (r.algo_fail.join('·') || '不达标') })),
    ];
    const tr = el('tr', { class: 'mix-row' }, cells);
    // 点开就是这张单的两份配方 —— 「算法配出 240.0」不解释它抓了哪几批就没法信
    let detail = null;
    tr.addEventListener('click', () => {
      if (detail) { detail.remove(); detail = null; tr.querySelector('.chev').textContent = '▸'; return; }
      tr.querySelector('.chev').textContent = '▾';
      detail = el('tr', {}, el('td', { class: 'left mix-cell', colspan: head.length },
        mixPanels(r)));
      tr.after(detail);
    });
    tb.append(tr);
  }
  table.append(tb);
  host.append(el('div', { class: 'table-wrap' }, table));
}

// ---------------------------------------------------------------- 配方对照

function mixPanels(r) {
  const hb = new Set(r.human_mix.map((x) => x.b));
  const ab = new Set(r.algo_mix.map((x) => x.b));
  return el('div', { class: 'mix-wrap' }, [
    mixPanel('人工实际配方', r.human_mix, r, ab, '换出'),
    mixPanel('算法重配方案', r.algo_mix, r, hb, '换入'),
  ]);
}

// other = 对方用到的批号集合；不在其中的就是这一侧独有的，标出来才看得见「换了哪几批」
function mixPanel(title, mix, r, other, tag) {
  const isAlgo = tag === '换入';
  const got = isAlgo ? r.algo : r.human;
  const ok = isAlgo ? r.algo_ok : r.human_ok;
  const swapped = mix.filter((x) => !other.has(x.b));
  const swapKg = swapped.reduce((s, x) => s + x.kg, 0);

  const rows = mix.map((x) => el('tr', {}, [
    el('td', { class: 'left' }, [
      x.b,
      !other.has(x.b) && el('span', { class: `pill ${isAlgo ? 'ok' : 'warn'} tiny`, text: tag }),
    ]),
    el('td', { class: 'num', text: fmt(x.kg, 1) }),
    el('td', { class: 'num', text: x.r === null ? '—' : `${fmt(x.r * 100, 1)}%` }),
    // 算法可能只取一批料的一部分（剩下的分给别的单），把整批量写出来才不误会
    el('td', { class: 'num muted',
      text: x.cap && x.kg < x.cap - 0.5 ? `${fmt(x.cap, 0)} 中取` : '整批' }),
    ...SPECS.map((s) => el('td', { class: 'num', text: fmt(x[s.key], s.d) })),
  ]));

  const table = el('table', { class: 'data-table mix-table' }, [
    el('thead', {}, el('tr', {}, ['原料批号', '投料kg', '配比', '取法', ...SPECS.map((s) => s.label)]
      .map((h) => el('th', { text: h })))),
    el('tbody', {}, rows),
    el('tfoot', {}, el('tr', {}, [
      el('td', { class: 'left', text: '加权平均' }),
      el('td', { class: 'num', text: fmt(r.W, 1) }),
      el('td', { class: 'num', text: '100%' }),
      el('td', {}),
      ...SPECS.map((s) => el('td', {
        class: `num ${got[s.key] < r.req[s.key] ? 'bad' : 'good-n'}`,
        text: fmt(got[s.key], s.d) })),
    ])),
  ]);

  return el('div', { class: 'mix-panel' }, [
    el('div', { class: 'mix-head' }, [
      el('b', { text: title }),
      el('span', { class: `pill ${ok ? 'ok' : 'no'}`, text: ok ? '达标' : '不达标' }),
      el('span', { class: 'muted',
        text: `${mix.length} 批 · ${tag}${swapped.length} 批 / ${fmt(swapKg, 0)} kg` }),
    ]),
    el('div', { class: 'mix-scroll' }, table),
    el('div', { class: 'mix-foot muted' },
      `客户要求　${SPECS.map((s) => `${s.label} ≥ ${fmt(r.req[s.key], s.d)}`).join('　')}`),
  ]);
}
