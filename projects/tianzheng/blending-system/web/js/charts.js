// 轻量 SVG 图表工具。不引第三方库 —— 看板只需要这四种形态，
// 手写 SVG 比拉一个 200KB 的图表库更可控，也符合项目的零构建约定。
//
// 配色遵循 dataviz 规范：
//   · 量级用单一蓝色（顺序色），不用彩虹
//   · 有序分类用同色系明度阶梯，已过校验（浅/深两套）
//   · 状态色只用于「告警」，从不冒充数据系列
//   · 网格与坐标轴退到背景，数据是主角

const NS = 'http://www.w3.org/2000/svg';

export function svgEl(tag, attrs = {}, children = []) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    n.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c === null || c === undefined || c === false) continue;
    n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return n;
}

/** 读取当前主题下的图表色板。深色不是浅色的自动翻转，是各自选定的一组。 */
export function palette() {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === 'dark'
    : window.matchMedia('(prefers-color-scheme: dark)').matches;
  return dark
    ? { accent: '#3987e5', accentSoft: '#256abf', critical: '#d03b3b',
        // 深色背景上「更亮 = 更多」，方向与浅色相反才符合直觉
        ordinal: ['#256abf', '#3987e5', '#86b6ef', '#cde2fb'],
        grid: '#2a313a', track: '#2a313a', ink: '#e4e8ee', dim: '#9aa4b0', mute: '#6b7681', dark: true }
    : { accent: '#2a78d6', accentSoft: '#86b6ef', critical: '#d03b3b',
        ordinal: ['#86b6ef', '#3987e5', '#256abf', '#104281'],
        grid: '#e3e6ea', track: '#e9ecef', ink: '#1c2024', dim: '#646c76', mute: '#98a0aa', dark: false };
}

const fmtNum = (v, d = 1) =>
  (v ?? 0).toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d });

// ---------------------------------------------------------------- 提示气泡

function tipLayer(host) {
  let tip = host.querySelector('.viz-tip');
  if (!tip) {
    tip = document.createElement('div');
    tip.className = 'viz-tip';
    host.append(tip);
  }
  return tip;
}

function bindTip(host, target, html) {
  target.addEventListener('mouseenter', () => {
    const tip = tipLayer(host);
    tip.innerHTML = html;
    tip.classList.add('show');
  });
  target.addEventListener('mousemove', (e) => {
    const tip = tipLayer(host);
    const r = host.getBoundingClientRect();
    const x = e.clientX - r.left;
    tip.style.left = `${Math.min(Math.max(x, 70), r.width - 70)}px`;
    tip.style.top = `${e.clientY - r.top - 14}px`;
  });
  target.addEventListener('mouseleave', () => {
    host.querySelector('.viz-tip')?.classList.remove('show');
  });
}

// ---------------------------------------------------------------- 面积折线

/**
 * 产能曲线：单系列面积图 + 十字准星。
 * 单系列不需要图例 —— 标题已经说明了它是什么。
 */
export function areaChart(host, data, opts = {}) {
  const P = palette();
  const W = host.clientWidth || 900;
  const H = opts.height ?? 300;
  const m = { t: 14, r: 18, b: 34, l: 56 };
  const iw = W - m.l - m.r;
  const ih = H - m.t - m.b;

  const xs = data.map(opts.x);
  const ys = data.map(opts.y);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const y1 = Math.max(...ys) * 1.06 || 1;

  const sx = (v) => m.l + (v - x0) / (x1 - x0 || 1) * iw;
  const sy = (v) => m.t + ih - v / y1 * ih;

  const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, width: '100%', height: H,
    role: 'img', 'aria-label': opts.label ?? '' });

  // 网格与 y 轴刻度：退到背景，不与数据争视线
  const ticks = 4;
  for (let i = 0; i <= ticks; i++) {
    const v = y1 / ticks * i;
    svg.append(svgEl('line', { x1: m.l, x2: m.l + iw, y1: sy(v), y2: sy(v),
      stroke: P.grid, 'stroke-width': 1 }));
    svg.append(svgEl('text', { x: m.l - 8, y: sy(v) + 4, 'text-anchor': 'end',
      fill: P.mute, 'font-size': 11 }, fmtNum(v, 0)));
  }
  for (const t of opts.xTicks ?? []) {
    if (t < x0 || t > x1) continue;
    svg.append(svgEl('text', { x: sx(t), y: H - 10, 'text-anchor': 'middle',
      fill: P.mute, 'font-size': 11 }, String(t)));
  }

  const line = data.map((d, i) => `${i ? 'L' : 'M'}${sx(opts.x(d))},${sy(opts.y(d))}`).join('');
  const gid = `grad-${Math.random().toString(36).slice(2, 8)}`;
  svg.append(svgEl('defs', {}, svgEl('linearGradient',
    { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 }, [
      svgEl('stop', { offset: '0%', 'stop-color': P.accent, 'stop-opacity': 0.30 }),
      svgEl('stop', { offset: '100%', 'stop-color': P.accent, 'stop-opacity': 0.02 }),
    ])));
  svg.append(svgEl('path', { d: `${line}L${sx(x1)},${sy(0)}L${sx(x0)},${sy(0)}Z`, fill: `url(#${gid})` }));
  svg.append(svgEl('path', { d: line, fill: 'none', stroke: P.accent, 'stroke-width': 2,
    'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));

  // 标注：产能塌陷的位置才是这张图的重点
  for (const mk of opts.markers ?? []) {
    const px = sx(mk.at);
    svg.append(svgEl('line', { x1: px, x2: px, y1: m.t, y2: m.t + ih,
      stroke: P.critical, 'stroke-width': 1.5, 'stroke-dasharray': '4 4', opacity: 0.75 }));
    svg.append(svgEl('text', { x: px - 6, y: m.t + 14, 'text-anchor': 'end',
      fill: P.critical, 'font-size': 11, 'font-weight': 600 }, mk.text));
  }

  const focus = svgEl('g', { opacity: 0 });
  const vline = svgEl('line', { y1: m.t, y2: m.t + ih, stroke: P.dim, 'stroke-width': 1 });
  const dot = svgEl('circle', { r: 4.5, fill: P.accent, stroke: P.dark ? '#171b21' : '#fff', 'stroke-width': 2 });
  focus.append(vline, dot);
  svg.append(focus);

  const hit = svgEl('rect', { x: m.l, y: m.t, width: iw, height: ih, fill: 'transparent' });
  svg.append(hit);
  host.innerHTML = '';
  host.append(svg);

  const tip = tipLayer(host);
  hit.addEventListener('mousemove', (e) => {
    const r = svg.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width * W;
    const vx = x0 + (px - m.l) / iw * (x1 - x0);
    let best = data[0], bd = Infinity;
    for (const d of data) {
      const dd = Math.abs(opts.x(d) - vx);
      if (dd < bd) { bd = dd; best = d; }
    }
    focus.setAttribute('opacity', 1);
    vline.setAttribute('x1', sx(opts.x(best)));
    vline.setAttribute('x2', sx(opts.x(best)));
    dot.setAttribute('cx', sx(opts.x(best)));
    dot.setAttribute('cy', sy(opts.y(best)));
    tip.innerHTML = opts.tip(best);
    tip.classList.add('show');
    tip.style.left = `${Math.min(Math.max(sx(opts.x(best)) / W * r.width, 90), r.width - 90)}px`;
    tip.style.top = `${sy(opts.y(best)) / H * r.height - 12}px`;
  });
  hit.addEventListener('mouseleave', () => {
    focus.setAttribute('opacity', 0);
    tip.classList.remove('show');
  });
}

// ---------------------------------------------------------------- 柱状

/** 直方图：单一蓝色（量级是它的职责，不是身份），4px 圆角数据端锚在基线上。 */
export function barChart(host, data, opts = {}) {
  const P = palette();
  const W = host.clientWidth || 600;
  const H = opts.height ?? 260;
  const m = { t: 14, r: 14, b: 34, l: 50 };
  const iw = W - m.l - m.r;
  const ih = H - m.t - m.b;

  const ys = data.map(opts.y);
  const y1 = Math.max(...ys) * 1.08 || 1;
  const bw = iw / data.length;
  const sy = (v) => m.t + ih - v / y1 * ih;

  const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, width: '100%', height: H,
    role: 'img', 'aria-label': opts.label ?? '' });

  for (let i = 0; i <= 4; i++) {
    const v = y1 / 4 * i;
    svg.append(svgEl('line', { x1: m.l, x2: m.l + iw, y1: sy(v), y2: sy(v),
      stroke: P.grid, 'stroke-width': 1 }));
    svg.append(svgEl('text', { x: m.l - 8, y: sy(v) + 4, 'text-anchor': 'end',
      fill: P.mute, 'font-size': 11 }, fmtNum(v, 0)));
  }

  data.forEach((d, i) => {
    const h = Math.max(ih - (sy(opts.y(d)) - m.t), 0);
    // 2px 表面间隙，相邻柱不糊在一起
    const bar = svgEl('rect', {
      x: m.l + i * bw + 1, y: sy(opts.y(d)), width: Math.max(bw - 2, 1), height: h,
      fill: opts.color ? opts.color(d, P) : P.accent, rx: Math.min(4, bw / 2),
    });
    svg.append(bar);
    bindTip(host, bar, opts.tip(d));
  });

  for (const t of opts.xTicks ?? []) {
    const i = data.findIndex((d) => opts.xVal(d) >= t);
    if (i < 0) continue;
    svg.append(svgEl('text', { x: m.l + i * bw + bw / 2, y: H - 10, 'text-anchor': 'middle',
      fill: P.mute, 'font-size': 11 }, String(t)));
  }

  // 参考线：分段界线画在直方图上，比另开一张图说得清楚
  for (const r of opts.rules ?? []) {
    const i = data.findIndex((d) => opts.xVal(d) >= r.at);
    if (i < 0) continue;
    const px = m.l + i * bw;
    svg.append(svgEl('line', { x1: px, x2: px, y1: m.t, y2: m.t + ih,
      stroke: P.dim, 'stroke-width': 1.5, 'stroke-dasharray': '4 4' }));
    svg.append(svgEl('text', { x: px + 5, y: m.t + 12, fill: P.dim, 'font-size': 11 }, r.text));
  }

  host.innerHTML = '';
  host.append(svg);
  tipLayer(host);
}

// ---------------------------------------------------------------- 水平堆叠条

/** 部分-整体：有序分类用同色系明度阶梯，直接标注 + 图例双重编码。 */
export function stackBar(host, items, opts = {}) {
  const P = palette();
  const total = items.reduce((s, d) => s + opts.v(d), 0) || 1;

  const track = document.createElement('div');
  track.className = 'stack-track';
  items.forEach((d, i) => {
    const seg = document.createElement('div');
    seg.className = 'stack-seg';
    seg.style.width = `${opts.v(d) / total * 100}%`;
    seg.style.background = P.ordinal[i % P.ordinal.length];
    bindTip(host, seg, opts.tip(d));
    track.append(seg);
  });

  const legend = document.createElement('div');
  legend.className = 'viz-legend';
  items.forEach((d, i) => {
    const li = document.createElement('span');
    li.className = 'lg-item';
    li.innerHTML = `<i style="background:${P.ordinal[i % P.ordinal.length]}"></i>` +
      `${opts.name(d)}<b>${fmtNum(opts.v(d) / total * 100, 1)}%</b>` +
      `<span class="lg-sub">${opts.sub(d)}</span>`;
    legend.append(li);
  });

  host.innerHTML = '';
  host.append(track, legend);
  tipLayer(host);
}

// ---------------------------------------------------------------- 区间条

/** 子弹图：灰色轨道是规格区间，蓝条是库存 p5~p95 实际落点。 */
export function rangeBars(host, rows, opts = {}) {
  const P = palette();
  const box = document.createElement('div');
  box.className = 'range-list';

  for (const r of rows) {
    const span = opts.hi(r) - opts.lo(r) || 1;
    const pct = (v) => Math.max(0, Math.min(100, (v - opts.lo(r)) / span * 100));
    const a = pct(opts.p5(r));
    const b = pct(opts.p95(r));
    const tight = r['余量%'];
    // 余量 <10% 就是瓶颈，用状态色 + 文字标签双重提示（状态色从不单独承载含义）
    const risky = tight < 10;

    const row = document.createElement('div');
    row.className = 'range-row';
    row.innerHTML = `
      <div class="rr-name">${opts.name(r)}<span class="u">${opts.unit(r) || ''}</span></div>
      <div class="rr-track">
        <div class="rr-fill" style="left:${a}%;width:${Math.max(b - a, 1)}%;
             background:${risky ? P.critical : P.accent}"></div>
      </div>
      <div class="rr-lo">${opts.lo(r)}</div>
      <div class="rr-hi">${opts.hi(r)}</div>
      <div class="rr-tag">${risky
        ? `<span class="badge bad">距${r['瓶颈侧']}仅 ${fmtNum(tight, 0)}%</span>`
        : `<span class="muted">距${r['瓶颈侧']} ${fmtNum(tight, 0)}%</span>`}</div>`;
    bindTip(host, row, opts.tip(r));
    box.append(row);
  }

  host.innerHTML = '';
  host.append(box);
  tipLayer(host);
}
