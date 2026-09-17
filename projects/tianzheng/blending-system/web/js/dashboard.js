// 数据看板：把库存数据变成「能不能接单」的判断依据。

import { api } from './api.js';
import { $, el, clear, fmt, toast } from './ui.js';
import { areaChart, barChart, stackBar, rangeBars, palette } from './charts.js';

let meta = null;
let snapshot = null;   // 缓存一份，主题切换和窗口缩放时重绘不用再拉一次

export function init(paramsMeta) {
  meta = paramsMeta;

  // 图表尺寸依赖容器宽度，窗口变化必须重画
  let t = null;
  window.addEventListener('resize', () => {
    clearTimeout(t);
    t = setTimeout(() => snapshot && draw(snapshot), 200);
  });
  // 主题切换后颜色整套变，同样要重画
  new MutationObserver(() => snapshot && draw(snapshot))
    .observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
}

export async function load() {
  const host = $('#chartCapacity');
  host.innerHTML = '<div class="empty"><span class="spinner"></span>正在汇总…</div>';
  try {
    snapshot = await api.dashboard();
    draw(snapshot);
  } catch (e) {
    host.innerHTML = '';
    host.append(el('div', { class: 'banner err', text: `看板加载失败：${e.message}` }));
    toast(e.message, true);
  }
}

// ---------------------------------------------------------------- 绘制

function draw(d) {
  drawKpi(d);
  drawCapacity(d);
  drawHist(d);
  drawBands(d);
  drawHeadroom(d);
  drawGrades(d);
}

function drawKpi(d) {
  const box = clear($('#dashKpi'));
  const k = d.kpi;

  // 产能塌陷点：可配量跌破总量 10% 的第一个冻力值。比"最大冻力"有用得多 ——
  // 最大值只有几批，真正的接单上限是这条线。
  const cliff = d.曲线.find((p) => p.最大可配吨 < k.总吨位 * 0.1);

  const cards = [
    { k: '库存总量', v: fmt(k.总吨位, 1), u: '吨' },
    { k: '库存批次', v: k.总批次.toLocaleString('zh-CN'), u: `批 · 均重 ${k.平均批重kg}kg` },
    { k: '冻力中位数', v: k.冻力中位, u: `Bloom g · ${k.冻力范围[0]}~${k.冻力范围[1]}` },
    { k: '高冻力料储备', v: fmt(d.分段.高冻力料.吨位, 1), u: `吨 · 占 ${d.分段.高冻力料.占比}%` },
    cliff
      ? { k: '接单能力上限', v: cliff.冻力, u: 'Bloom g 以上产能不足一成', warn: true }
      : { k: '接单能力上限', v: k.冻力范围[1], u: 'Bloom g' },
  ];

  for (const c of cards) {
    box.append(el('div', { class: `stat ${c.warn ? 'warn' : ''}` }, [
      el('div', { class: 'k', text: c.k }),
      el('div', { class: 'v' }, [String(c.v), el('span', { class: 'u', text: ` ${c.u}` })]),
    ]));
  }
}

function drawCapacity(d) {
  const total = d.kpi.总吨位;
  // 标注产能腰斩和跌破一成的位置 —— 这两条线是接单谈判的实际边界
  const half = d.曲线.find((p) => p.最大可配吨 < total * 0.5);
  const tenth = d.曲线.find((p) => p.最大可配吨 < total * 0.1);

  areaChart($('#chartCapacity'), d.曲线, {
    height: 320,
    x: (p) => p.冻力,
    y: (p) => p.最大可配吨,
    xTicks: [60, 100, 140, 180, 220, 260, 300],
    label: '目标冻力与最大可配产量',
    markers: [
      half && { at: half.冻力, text: `${half.冻力} 起产能腰斩` },
      tenth && { at: tenth.冻力, text: `${tenth.冻力} 起不足一成` },
    ].filter(Boolean),
    tip: (p) => `
      <div class="tip-t">目标冻力 ≥ ${p.冻力}</div>
      <div class="tip-r"><span>最大可配</span><b>${fmt(p.最大可配吨, 1)} 吨</b></div>
      <div class="tip-r"><span>其中高端料</span><b>${fmt(p.高端料吨, 1)} 吨</b></div>
      <div class="tip-r"><span>可带动低端料</span><b>${fmt(p.可带动吨, 1)} 吨</b></div>
      <div class="tip-r"><span>占总库存</span><b>${fmt(p.最大可配吨 / total * 100, 0)}%</b></div>`,
  });
}

function drawHist(d) {
  barChart($('#chartHist'), d.直方图, {
    height: 250,
    y: (b) => b.吨位,
    xVal: (b) => b.起,
    xTicks: [80, 140, 200, 260, 300],
    label: '库存冻力分布',
    rules: [
      { at: d.界线.低, text: `低 ${d.界线.低}` },
      { at: d.界线.高, text: `高 ${d.界线.高}` },
    ],
    tip: (b) => `
      <div class="tip-t">冻力 ${b.起} ~ ${b.止}</div>
      <div class="tip-r"><span>吨位</span><b>${fmt(b.吨位, 1)} 吨</b></div>
      <div class="tip-r"><span>批次</span><b>${b.批次} 批</b></div>`,
  });
}

function drawBands(d) {
  const P = palette();
  const items = [
    { name: `低冻力料 ≤ ${d.界线.低}`, ...d.分段.低冻力料 },
    { name: `中间料 ${d.界线.低} ~ ${d.界线.高}`, ...d.分段.中间料 },
    { name: `高冻力料 ≥ ${d.界线.高}`, ...d.分段.高冻力料 },
  ];
  stackBar($('#chartBands'), items, {
    v: (x) => x.吨位,
    name: (x) => x.name,
    sub: (x) => `${fmt(x.吨位, 1)} 吨 / ${x.批次} 批`,
    tip: (x) => `
      <div class="tip-t">${x.name}</div>
      <div class="tip-r"><span>吨位</span><b>${fmt(x.吨位, 1)} 吨</b></div>
      <div class="tip-r"><span>批次</span><b>${x.批次} 批</b></div>
      <div class="tip-r"><span>占库存</span><b>${x.占比}%</b></div>`,
  });
  void P;
}

function drawHeadroom(d) {
  rangeBars($('#chartHeadroom'), d.余量, {
    name: (r) => r.名称,
    unit: (r) => r.单位,
    lo: (r) => r.规格下限,
    hi: (r) => r.规格上限,
    p5: (r) => r.p5,
    p95: (r) => r.p95,
    tip: (r) => `
      <div class="tip-t">${r.名称} ${r.单位}</div>
      <div class="tip-r"><span>规格区间</span><b>${r.规格下限} ~ ${r.规格上限}</b></div>
      <div class="tip-r"><span>库存 p5~p95</span><b>${r.p5} ~ ${r.p95}</b></div>
      <div class="tip-r"><span>库存极值</span><b>${r.库存最小} ~ ${r.库存最大}</b></div>
      <div class="tip-r"><span>单批超限</span><b>${r.超限批次} 批</b></div>`,
  });
}

function drawGrades(d) {
  stackBar($('#chartGrades'), d.等级, {
    v: (x) => x.吨位,
    name: (x) => x.等级,
    sub: (x) => `${fmt(x.吨位, 1)} 吨 / ${x.批次} 批`,
    tip: (x) => `
      <div class="tip-t">水不溶物 ${x.等级}</div>
      <div class="tip-r"><span>吨位</span><b>${fmt(x.吨位, 1)} 吨</b></div>
      <div class="tip-r"><span>批次</span><b>${x.批次} 批</b></div>`,
  });
}
