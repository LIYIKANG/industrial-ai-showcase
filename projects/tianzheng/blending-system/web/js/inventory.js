// 库存数据页：总览统计 + 分页明细表 + 指标分布。

import { api } from './api.js';
import { $, el, clear, fmt, fmtAuto, toast, empty } from './ui.js';

let meta = null;          // /api/params 返回的参数定义
let state = {
  page: 1, size: 50, sort_by: null, desc: false,
  grade: '', keyword: '', bloom_min: '', bloom_max: '', weight_min: '',
};

export function init(paramsMeta) {
  meta = paramsMeta;

  const gradeSel = $('#fGrade');
  for (const g of meta.grades) gradeSel.append(el('option', { value: g, text: g }));

  $('#btnFilter').addEventListener('click', () => {
    state.page = 1;
    state.bloom_min  = $('#fBloomMin').value;
    state.bloom_max  = $('#fBloomMax').value;
    state.weight_min = $('#fWeightMin').value;
    state.grade      = $('#fGrade').value;
    state.keyword    = $('#fKeyword').value;
    loadTable();
  });

  $('#btnResetFilter').addEventListener('click', () => {
    for (const id of ['#fBloomMin', '#fBloomMax', '#fWeightMin', '#fKeyword']) $(id).value = '';
    $('#fGrade').value = '';
    state = { ...state, page: 1, grade: '', keyword: '', bloom_min: '', bloom_max: '', weight_min: '' };
    loadTable();
  });

  $('#btnReload').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = '导入中…';
    try {
      const r = await api.reloadData();
      toast(`已重新导入 ${r.批次数.toLocaleString('zh-CN')} 批`);
      await load();
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
      btn.textContent = '重新导入';
    }
  });
}

export async function load() {
  await Promise.all([loadStats(), loadTable()]);
}

// ---------------------------------------------------------------- 统计

async function loadStats() {
  const box = $('#invStats');
  try {
    const s = await api.stats();
    clear(box);

    const numeric = s.指标.filter((p) => p.type === 'numeric');
    const bloom = numeric.find((p) => p.key === meta.target_col);

    const cards = [
      { k: '库存批次', v: s.批次数.toLocaleString('zh-CN'), u: '批' },
      { k: '库存总量', v: Math.round(s.总重量kg).toLocaleString('zh-CN'), u: 'kg' },
      { k: '冻力范围', v: `${fmtAuto(bloom?.最小)} ~ ${fmtAuto(bloom?.最大)}`, u: 'Bloom g' },
      { k: '冻力中位数', v: fmtAuto(bloom?.中位), u: 'Bloom g' },
    ];
    for (const c of cards) {
      box.append(el('div', { class: 'stat' }, [
        el('div', { class: 'k', text: c.k }),
        el('div', { class: 'v' }, [c.v, el('span', { class: 'u', text: c.u })]),
      ]));
    }
    renderStatTable(s);
  } catch (e) {
    clear(box).append(el('div', { class: 'banner err', text: `加载统计失败：${e.message}` }));
  }
}

function renderStatTable(s) {
  const table = $('#statTable');
  const thead = clear(table.tHead);
  const tbody = clear(table.tBodies[0]);

  thead.append(el('tr', {}, [
    el('th', { class: 'left', text: '指标' }),
    el('th', { class: 'left', text: '单位' }),
    ...['最小', 'p5', '中位', '均值', 'p95', '最大', '缺失'].map((h) => el('th', { text: h })),
  ]));

  for (const p of s.指标) {
    if (p.type === 'grade') {
      const dist = p.分布.map((d) => `${d.值 ?? '空'} × ${d.数量.toLocaleString('zh-CN')}`).join('　');
      tbody.append(el('tr', {}, [
        el('td', { class: 'left', text: p.label }),
        el('td', { class: 'left', text: p.unit }),
        el('td', { class: 'left muted', colspan: 6, text: dist }),
        el('td', { class: 'num', text: p.缺失.toLocaleString('zh-CN') }),
      ]));
      continue;
    }
    tbody.append(el('tr', {}, [
      el('td', { class: 'left', text: p.label }),
      el('td', { class: 'left muted', text: p.unit }),
      ...['最小', 'p5', '中位', '均值', 'p95', '最大'].map((k) =>
        el('td', { class: 'num', text: fmtAuto(p[k]) })),
      el('td', { class: 'num', text: p.缺失.toLocaleString('zh-CN') }),
    ]));
  }
}

// ---------------------------------------------------------------- 明细表

async function loadTable() {
  const table = $('#invTable');
  const tbody = clear(table.tBodies[0]);
  tbody.append(el('tr', {}, el('td', { colspan: 14, class: 'empty left' },
    [el('span', { class: 'spinner' }), '加载中…'])));

  let data;
  try {
    data = await api.inventory(state);
  } catch (e) {
    clear(tbody).append(el('tr', {}, el('td', { colspan: 14, class: 'empty left', text: `加载失败：${e.message}` })));
    return;
  }

  const cols = data.columns;
  const labelOf = (key) => meta.params.find((p) => p.key === key)?.label ?? key;
  const unitOf  = (key) => meta.params.find((p) => p.key === key)?.unit ?? '';
  const decOf   = (key) => meta.params.find((p) => p.key === key)?.decimals ?? 2;

  const thead = clear(table.tHead);
  thead.append(el('tr', {}, cols.map((c) => {
    const isSorted = state.sort_by === c;
    return el('th', {
      class: c === meta.id_col ? 'left' : '',
      onclick: () => {
        state.desc = isSorted ? !state.desc : true;
        state.sort_by = c;
        state.page = 1;
        loadTable();
      },
      title: c,
    }, [
      c === meta.id_col ? '编号' : labelOf(c) + (unitOf(c) ? ` (${unitOf(c)})` : ''),
      isSorted ? el('span', { class: 'arrow', text: state.desc ? '↓' : '↑' }) : null,
    ]);
  })));

  clear(tbody);
  if (!data.rows.length) {
    tbody.append(el('tr', {}, el('td', { colspan: cols.length, class: 'empty left', text: '没有符合条件的批次' })));
  }
  for (const row of data.rows) {
    tbody.append(el('tr', {}, cols.map((c) => {
      const v = row[c];
      if (v === null || v === undefined) return el('td', { class: 'null', text: '—' });
      if (typeof v === 'number') {
        return el('td', { class: 'num', text: c === meta.id_col ? String(v) : fmt(v, decOf(c)) });
      }
      return el('td', { class: 'left', text: String(v) });
    })));
  }

  renderPager(data);
}

function renderPager(data) {
  const box = clear($('#invPager'));
  const go = (p) => { state.page = p; loadTable(); };

  box.append(el('span', {
    text: `共 ${data.total.toLocaleString('zh-CN')} 批 · 第 ${data.page} / ${data.pages} 页`,
  }));

  const sizeSel = el('select', {
    class: 'mini',
    onchange: (e) => { state.size = +e.target.value; state.page = 1; loadTable(); },
  }, [20, 50, 100, 200].map((n) =>
    el('option', { value: n, text: `${n} 条/页`, selected: n === state.size })));
  box.append(sizeSel);

  box.append(el('button', { class: 'btn ghost', disabled: data.page <= 1, onclick: () => go(1), text: '«' }));
  box.append(el('button', { class: 'btn ghost', disabled: data.page <= 1, onclick: () => go(data.page - 1), text: '上一页' }));
  box.append(el('button', { class: 'btn ghost', disabled: data.page >= data.pages, onclick: () => go(data.page + 1), text: '下一页' }));
  box.append(el('button', { class: 'btn ghost', disabled: data.page >= data.pages, onclick: () => go(data.pages), text: '»' }));
}
