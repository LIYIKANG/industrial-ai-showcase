// 应用入口：加载参数元数据 → 初始化三个页面 → 简单的 tab 路由。

import { api } from './api.js';
import { $, el, toast } from './ui.js';
import * as dashboard from './dashboard.js';
import * as inventory from './inventory.js';
import * as blend from './blend.js';
import * as compare from './compare.js?v=20260917';
import * as backtest from './backtest.js';
import * as params from './params.js';

const VIEWS = ['dashboard', 'inventory', 'blend', 'compare', 'backtest', 'params'];
const loaded = { dashboard: false, inventory: false, blend: false, compare: false, backtest: false, params: false };

async function boot() {
  initTheme();

  let meta;
  try {
    meta = await api.params();
  } catch (e) {
    document.getElementById('main').prepend(
      el('div', { class: 'banner err', text: `无法连接后端：${e.message}` }));
    setHealth(false, '后端未响应');
    return;
  }

  try {
    const h = await api.health();
    setHealth(h.ok, h.ok ? `库存 ${h.批次数.toLocaleString('zh-CN')} 批` : h.error);
  } catch {
    setHealth(false, '数据未就绪');
  }

  dashboard.init(meta);
  inventory.init(meta);
  blend.init(meta);
  compare.init(meta);
  backtest.init(meta);
  params.init(meta);

  initTabs();
}

// ---------------------------------------------------------------- 路由

function initTabs() {
  for (const btn of document.querySelectorAll('#tabs .tab')) {
    btn.addEventListener('click', () => show(btn.dataset.view));
  }
  window.addEventListener('hashchange', () => {
    const v = location.hash.slice(1);
    if (VIEWS.includes(v)) show(v);
  });
  const initial = location.hash.slice(1);
  show(VIEWS.includes(initial) ? initial : 'dashboard');
}

function show(view) {
  for (const b of document.querySelectorAll('#tabs .tab')) {
    b.classList.toggle('active', b.dataset.view === view);
  }
  for (const s of document.querySelectorAll('.view')) {
    s.classList.toggle('active', s.id === `view-${view}`);
  }
  if (location.hash.slice(1) !== view) history.replaceState(null, '', `#${view}`);

  // 首次进入才拉数据；参数页每次都刷新，保证看到的是最新落盘配置
  if (view === 'dashboard' && !loaded.dashboard) { loaded.dashboard = true; dashboard.load(); }
  if (view === 'inventory' && !loaded.inventory) { loaded.inventory = true; inventory.load(); }
  if (view === 'compare' && !loaded.compare) { loaded.compare = true; compare.load(); }
  if (view === 'backtest' && !loaded.backtest) { loaded.backtest = true; backtest.load(); }
  if (view === 'params') params.load();
}

// ---------------------------------------------------------------- 其他

function setHealth(ok, text) {
  const node = $('#health');
  node.className = `health ${ok ? 'ok' : 'err'}`;
  node.textContent = text;
}

function initTheme() {
  const saved = localStorage.getItem('theme');
  if (saved) document.documentElement.dataset.theme = saved;

  $('#themeBtn').addEventListener('click', () => {
    const cur = document.documentElement.dataset.theme;
    const isDark = cur
      ? cur === 'dark'
      : window.matchMedia('(prefers-color-scheme: dark)').matches;
    const next = isDark ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    localStorage.setItem('theme', next);
  });
}

boot().catch((e) => toast(`初始化失败：${e.message}`, true));
