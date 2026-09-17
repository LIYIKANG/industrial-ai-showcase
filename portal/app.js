'use strict';
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state, route = '', online = true, statusPending = false, toastTimer, pendingReload;
const pages = new Map(), selectedServices = new Map(), progress = new Map();
const wallIds = ['solver', 'bot', 'blend', 'mvr'];
const main = $('#main-content');
const agent = id => state.agents.find(a => a.id === id);
const service = id => state.services.find(s => s.id === id);
const currentService = id => selectedServices.get(id) || agent(id).services[0];
const label = s => esc(s);

function toast(message) {
  clearTimeout(toastTimer);
  $('#toast').textContent = message;
  $('#toast').classList.add('show');
  toastTimer = setTimeout(() => $('#toast').classList.remove('show'), 4000);
}
function stateTag(a) {
  if (!a.services.length) return '<span class="state-tag proposal">方案阶段</span>';
  const ready = a.services.filter(id => service(id).state === 'ready').length;
  const status = !online ? 'offline' : ready === a.services.length ? 'ready' : a.services.some(id => service(id).state === 'error') ? 'error' : 'starting';
  const text = !online ? '状态待确认' : ready === a.services.length ? '可操作' : ready ? '部分就绪' : status === 'error' ? '启动异常' : '准备中';
  return `<span class="state-tag" data-state="${status}" data-agent-status="${a.id}">${text}</span>`;
}
function renderOverview() {
  const el = document.createElement('div');
  const live = state.agents.filter(a => a.services.length);
  el.innerHTML = `<section class="hero"><div><div class="eyebrow">EXPLORE · TRY · UNDERSTAND</div><h1>从一个业务问题，<br>开始体验 Agent。</h1><p>选择您关心的功能，跟随操作指引，亲手完成一次计算或业务处理。<br>无需准备数据，每个可操作的 Agent 都有示例。</p><div class="hero-actions"><button class="primary" data-scroll-agents>选择一个 Agent ↓</button><button data-go="wall">多屏同时体验 ↗</button></div></div><div class="hero-art" aria-hidden="true"><div class="orbit"></div><div class="orbit two"></div><div class="hub-core"><b>Agent</b><small>HANDS-ON STUDIO</small></div><span class="sat s1">◈ 决策优化</span><span class="sat s2">▤ 单据处理</span><span class="sat s3">⌘ 配料分析</span><span class="sat s4">≈ 能耗诊断</span></div></section>
    <div class="experience-path"><div><b>01</b><span>选择业务功能<small>${live.length} 个可操作 Agent · 1 个方案</small></span></div><div><b>02</b><span>阅读专属指引<small>明确输入、动作与预期结果</small></span></div><div><b>03</b><span>在真实系统中操作<small>修改参数 · 检查结果 · 导出文件</small></span></div></div>
    <div class="section-heading" id="agent-list"><h2>您想解决什么问题？</h2><span>按能力分类 · 建议首次先看操作指引</span></div>
    <div class="agent-grid">${state.agents.map((a, i) => `<article class="agent-card" style="--accent:${a.color}"><div class="card-visual"><span>${a.en}</span><b>${a.icon}</b><small>AGENT / ${String(i+1).padStart(2,'0')}</small></div><div class="card-body"><div class="card-heading"><h3>${a.name}</h3>${stateTag(a)}</div><p>${a.description}</p><div class="tags">${a.tags.map(t => `<span>${t}</span>`).join('')}</div><div class="card-meta">${a.duration} · ${a.audience}</div><div class="card-actions"><button class="enter" data-go="agent/${a.id}">${a.services.length ? '查看操作指引' : '了解能力方案'} →</button>${a.services.length ? `<button data-go="work/${a.id}">直接操作 ↗</button>` : '<span>暂无运行程序</span>'}</div></div></article>`).join('')}</div>
    <footer>INDUSTRIAL AI STUDIO <span>功能体验 · 人工确认 · 结果可查</span></footer>`;
  return el;
}
function stepList(a) {
  return `<ol class="full-steps">${a.steps.map((s,i) => `<li><span class="step-number">${String(i+1).padStart(2,'0')}</span><div><h3>${s.title}</h3><p>${s.action}</p><div class="expected"><strong>您会看到</strong>${s.expected}</div></div></li>`).join('')}</ol>`;
}
function renderGuide(id) {
  const a = agent(id), el = document.createElement('div');
  el.style.setProperty('--accent', a.color);
  el.innerHTML = `<button class="back-link" data-go="overview">← 返回能力总览</button><header class="agent-header"><div><div class="eyebrow">${a.en} / ${a.services.length ? '操作指引' : '能力方案'}</div><h1>${a.name}</h1><p>${a.description}</p><div class="guide-meta">${stateTag(a)}<span>${a.duration}</span><span>适合：${a.audience}</span></div></div>${a.services.length ? `<button class="primary start-agent" data-go="work/${id}">进入操作工作台 ↗</button>` : ''}</header>
    <div class="client-brief"><article><span>为什么需要它</span><p>${a.problem}</p></article><article><span>最终得到什么</span><p>${a.outcome}</p></article><article><span>开始前准备</span><p>${a.prepare}</p></article></div>
    <div class="guide-layout"><section><div class="section-heading"><h2>${a.services.length ? '跟着这几步，完成第一次体验' : '能力建设路径'}</h2></div>${stepList(a)}</section><aside class="guide-aside"><div class="info-panel"><span class="eyebrow">${a.services.length ? 'QUICK START' : 'PROPOSAL'}</span><h3>${a.services.length ? '边看步骤，边操作' : '当前阶段：能力方案'}</h3><p>${a.services.length ? '进入工作台后，右侧会保留每一步的操作说明。完成后手动标记，再继续下一步。' : '此部分只有方案资料，尚无可运行程序。当前页面用于介绍规划能力与交付路径。'}</p>${a.services.length ? `<button class="primary" data-go="work/${id}">开始体验 →</button>` : ''}</div><div class="boundary"><h3>体验范围</h3><p>${a.boundary}</p></div></aside></div>`;
  return el;
}
function createFrame(id) {
  const box = document.createElement('div');
  box.className = 'frame-shell'; box.dataset.service = id;
  box.innerHTML = `<div class="frame-message"><strong>${esc(service(id).name)}</strong><p>正在准备系统…</p><button data-retry="${id}" hidden>重试启动</button></div>`;
  updateFrame(box);
  return box;
}
function updateFrame(box) {
  const s = service(box.dataset.service), overlay = $('.frame-message', box);
  if (s.state === 'ready') {
    overlay.hidden = true;
    if (!$('iframe', box)) {
      const frame = document.createElement('iframe');
      frame.title = s.name; frame.src = s.url; frame.allow = 'fullscreen; clipboard-write';
      box.appendChild(frame);
    }
  } else {
    overlay.hidden = false;
    $('p', overlay).textContent = s.message;
    $('button', overlay).hidden = s.state !== 'error';
  }
}
function getProgress(id) {
  // Each app variant has its own manual checklist; business results never mark it automatically.
  const key = id + '/' + currentService(id);
  if (!progress.has(key)) progress.set(key, {index: 0, completed: new Set()});
  return progress.get(key);
}
function renderStepRail(id) {
  const a = agent(id), p = getProgress(id), s = a.steps[p.index];
  const allDone = p.completed.size === a.steps.length;
  return `<div class="rail-heading"><h2>操作指引</h2><span>手动进度 ${p.completed.size} / ${a.steps.length}</span></div><p class="rail-caption">先在系统中操作，再确认完成。</p>
    <nav class="step-nav" aria-label="${a.name}操作步骤">${a.steps.map((s,i) => `<button data-step="${i}" data-agent="${id}" class="${i === p.index ? 'current' : ''} ${p.completed.has(i) ? 'done' : ''}" ${i === p.index ? 'aria-current="step"' : ''}><b>${p.completed.has(i) ? '✓' : i+1}</b><span>${s.title}</span>${p.completed.has(i) ? '<span class="sr-only">已手动完成</span>' : ''}</button>`).join('')}</nav>
    <div class="current-instruction" aria-live="polite"><small>第 ${p.index+1} 步 / ${a.steps.length}</small><h3>${s.title}</h3><p>${s.action}</p><div class="expected"><strong>预期结果</strong>${s.expected}</div></div>
    ${allDone ? '<p class="completion" role="status">本次步骤已全部手动标记完成。请在系统中确认结果。</p>' : ''}
    <div class="step-controls"><button data-previous="${id}" ${p.index === 0 ? 'disabled' : ''}>上一步</button><button class="primary" data-complete="${id}">${p.index === a.steps.length-1 ? (p.completed.has(p.index) ? '已标记完成' : '标记本步完成') : '我已完成，下一步'}</button></div>
    <button class="reset-steps" data-reset-steps="${id}">重置步骤标记</button><p class="rail-caption">仅重置指引，不清除系统输入。</p><a class="guide-link" href="#agent/${id}">查看完整介绍与操作顺序 ↗</a>`;
}
function renderWork(id) {
  const a = agent(id), el = document.createElement('div');
  el.className = 'workspace'; el.style.setProperty('--accent', a.color);
  const sid = currentService(id);
  el.innerHTML = `<header class="work-header"><div><button class="back-link" data-go="agent/${id}">← 查看介绍与完整指引</button><h1>${a.name} ${stateTag(a)}</h1></div><button data-toggle-guide="${id}" aria-expanded="true">收起操作指引</button></header>
    <div class="work-layout"><section class="application-panel" aria-label="${a.name}操作系统"><div class="detail-toolbar"><div class="tabs" role="group" aria-label="体验版本">${a.services.length > 1 ? a.services.map(s => `<button data-select-service="${s}" data-agent="${id}" class="${s === sid ? 'active' : ''}" aria-pressed="${s === sid}">${a.variants[s]}</button>`).join('') : '<span class="workspace-label">可操作系统</span>'}</div><div class="tools"><button data-focus-agent="${id}">展开窗口 ↗</button><button data-refresh-agent="${id}">重新加载</button><a class="standalone" href="${esc(service(sid).url)}" target="_blank" rel="noopener">独立打开 ↗</a></div></div>${a.variantNote ? `<p class="variant-note">${a.variantNote}</p>` : ''}<div class="project-frames"></div><div class="boundary compact"><strong>体验范围</strong><span>${a.boundary}</span></div></section><aside class="step-rail" aria-label="操作指引">${renderStepRail(id)}</aside></div>`;
  $('.project-frames', el).appendChild(createFrame(sid));
  return el;
}
function updateRail(id) {
  const el = pages.get('work/' + id);
  if (!el) return;
  const rail = $('.step-rail', el), focused = document.activeElement;
  const focusKey = rail.contains(focused) ? ['step','previous','complete','resetSteps'].find(k => focused.dataset[k] !== undefined) : null;
  const focusValue = focusKey ? focused.dataset[focusKey] : null;
  rail.innerHTML = renderStepRail(id);
  if (focusKey) {
    const target = $$('button',rail).find(b => b.dataset[focusKey] === focusValue && !b.disabled);
    (target || $('[data-complete]',rail))?.focus({preventScroll:true});
  }
}
function selectService(id, sid) {
  if (!agent(id).services.includes(sid)) return;
  selectedServices.set(id, sid);
  const el = pages.get('work/' + id), target = $('.project-frames', el);
  $$('.tabs button', el).forEach(b => {
    b.classList.toggle('active', b.dataset.selectService === sid);
    b.setAttribute('aria-pressed', String(b.dataset.selectService === sid));
  });
  $$('.frame-shell', target).forEach(f => { f.hidden = f.dataset.service !== sid; });
  let frame = $(`.frame-shell[data-service="${sid}"]`, target);
  if (!frame) { frame = createFrame(sid); target.appendChild(frame); }
  frame.hidden = false;
  $('.standalone', el).href = service(sid).url;
  updateRail(id);
}
let closeFocus;
function expandFrame(box) {
  if (closeFocus) closeFocus();
  const previous = document.activeElement;
  box.classList.add('frame-focus');
  const bar = document.createElement('div'); bar.className = 'focus-bar';
  bar.innerHTML = `<strong>${esc(service(box.dataset.service).name)}</strong><span>可滚动操作 · 完成后收起窗口</span><button class="focus-exit">收起窗口</button>`;
  box.appendChild(bar); document.body.classList.add('has-focus');
  const key = e => { if (e.key === 'Escape') closeFocus?.(); };
  closeFocus = () => {
    box.classList.remove('frame-focus'); bar.remove(); document.body.classList.remove('has-focus');
    document.removeEventListener('keydown', key); closeFocus = null; resizeWall(); previous?.focus();
  };
  $('.focus-exit', bar).onclick = closeFocus;
  document.addEventListener('keydown', key); $('.focus-exit', bar).focus(); resizeWall();
}
function renderWall() {
  const el = document.createElement('div'); el.id = 'wall-container';
  const apps = state.services.filter(s => !s.backup);
  el.innerHTML = `<div class="wall-top"><div><div class="eyebrow">MULTI-AGENT WORKSPACE</div><h1>多屏体验</h1><p>四个系统同时显示。精细输入时点击“展开”，操作完成后可收起。</p><p class="muted">各窗口独立保留操作；切换窗口中的 Agent 会保留此前页面。首次体验建议从专属指引开始。</p></div><button id="expand-wall">铺满展示 ⛶</button></div><div class="wall-grid">${wallIds.map((id,i) => `<section class="wall-cell" data-cell="${i}"><header><label class="sr-only" for="wall-select-${i}">窗口 ${i+1} 的 Agent</label><select id="wall-select-${i}" data-wall-select="${i}">${apps.map(s => `<option value="${s.id}" ${s.id === id ? 'selected' : ''}>${agent(s.group).name}</option>`).join('')}</select><a class="wall-guide" href="#agent/${service(id).group}">指引</a><button data-wall-open="${i}">展开 ↗</button></header><div class="wall-frames"></div></section>`).join('')}</div>`;
  $$('.wall-cell', el).forEach((cell,i) => $('.wall-frames', cell).appendChild(createFrame(wallIds[i])));
  return el;
}
function resizeWall() {
  $$('.wall-cell .frame-shell:not([hidden])').forEach(box => {
    const f = $('iframe', box); if (!f || !box.clientWidth) return;
    if (box.classList.contains('frame-focus')) {
      f.style.transform = 'none'; f.style.width = '100%'; f.style.height = 'calc(100dvh - 52px)';
    } else {
      const scale = box.clientWidth / 1280;
      f.style.transform = `scale(${scale})`; f.style.width = '1280px'; f.style.height = Math.max(740, box.clientHeight / scale) + 'px';
    }
  });
}
function renderMaterials() {
  const el = document.createElement('div');
  el.innerHTML = '<div class="page-title"><div class="eyebrow">DEMO INPUTS</div><h1>演示样例</h1><p>首次体验可直接使用系统内置示例；以下文件可下载后练习上传。</p></div><div class="materials-table"><p class="empty">正在加载样例…</p></div>';
  loadMaterials(el);
  return el;
}
async function loadMaterials(el) {
  try {
    const res = await fetch('/api/materials'); if (!res.ok) throw Error('materials');
    const files = await res.json();
    $('.materials-table', el).innerHTML = files.map(m => `<article class="material-row"><span class="file-badge">${esc(m.type)}</span><div><h3>${esc(m.name)}</h3><p>${esc(agent(m.agent).name)}</p></div><a href="/materials/${encodeURIComponent(m.path)}" download="${esc(m.name)}" data-download-sample>下载样例 ↓</a><a href="#agent/${m.agent}">操作指引 →</a></article>`).join('') || '<p class="empty">暂无需要下载的样例，请使用系统内置示例。</p>';
  } catch {
    $('.materials-table', el).innerHTML = '<div class="empty">样例暂时无法加载，不影响使用 Agent 内置示例。<button data-retry-materials>重试加载</button></div>';
  }
}
function navigate(next) {
  if (location.hash.slice(1) === next) showRoute(); else location.hash = next;
}
function showRoute() {
  if (!state) return;
  let next = location.hash.slice(1) || 'overview';
  const legacy = {'project/solver':'decision','project/anbicheng':'document','project/tianzheng':'formulation','project/mvr':'energy','project/pyrolysis':'maintenance'};
  if (legacy[next]) next = 'agent/' + legacy[next];
  if (!['overview','wall','materials'].includes(next) && !/^(agent|work)\/[^/]+$/.test(next)) next = 'overview';
  if (next.startsWith('agent/') || next.startsWith('work/')) {
    const a = agent(next.split('/')[1]);
    if (!a) next = 'overview'; else if (next.startsWith('work/') && !a.services.length) next = 'agent/' + a.id;
  }
  if (location.hash.slice(1) !== next) history.replaceState(null, '', '#' + next);
  closeFocus?.();
  const wall = $('#wall-container');
  if (wall) { wall.classList.remove('wall-expanded'); $('#expand-wall').textContent = '铺满展示 ⛶'; }
  route = next;
  pages.forEach(el => { el.hidden = true; });
  if (!pages.has(next)) {
    const [kind, id] = next.split('/');
    const el = next === 'overview' ? renderOverview() : next === 'wall' ? renderWall() : next === 'materials' ? renderMaterials() : kind === 'agent' ? renderGuide(id) : renderWork(id);
    pages.set(next, el); main.appendChild(el);
  }
  pages.get(next).hidden = false;
  $('#page-label').textContent = next.includes('/') ? agent(next.split('/')[1]).name + (next.startsWith('work/') ? ' / 操作' : ' / 指引') : {overview:'能力总览',wall:'多屏体验',materials:'演示样例'}[next];
  document.title = $('#page-label').textContent + ' · Agent 体验中心';
  $$('.nav-button').forEach(b => {
    const active = (b.dataset.page || b.dataset.go) === next || (next.startsWith('work/') && b.dataset.go === next.replace('work/','agent/'));
    b.classList.toggle('active', active);
    if (active) b.setAttribute('aria-current','page'); else b.removeAttribute('aria-current');
  });
  updateUI(); window.scrollTo(0,0); main.focus({preventScroll:true});
  if (next === 'wall') requestAnimationFrame(resizeWall);
}
function updateUI() {
  const ready = state.services.filter(s => s.state === 'ready').length, total = state.services.length;
  $('#global-status').textContent = !online ? '连接中断' : ready === total ? '● 全部系统已就绪' : `● ${ready} / ${total} 服务就绪`;
  $('#global-status').classList.toggle('warn', !online || ready !== total);
  $('#sidebar-count').textContent = !online ? '状态待确认' : `${ready} / ${total} 在线`;
  $('#connection-notice').hidden = online;
  $$('[data-agent-status]').forEach(tag => { const wrap = document.createElement('div'); wrap.innerHTML = stateTag(agent(tag.dataset.agentStatus)); tag.replaceWith(wrap.firstElementChild); });
  if (online) $$('.frame-shell').forEach(updateFrame);
  if (route === 'wall') resizeWall();
  if ($('#status-dialog').open) renderStatus();
}
function renderStatus() {
  $('#service-list').innerHTML = state.services.map(s => `<div class="service-row"><span>${esc(s.name)}</span><span class="state-tag" data-state="${online ? s.state : 'offline'}">${!online ? '状态待确认' : s.state === 'ready' ? '运行中' : s.state === 'error' ? '启动异常' : '启动中'}</span></div>`).join('');
}
async function refreshStatus() {
  if (statusPending) return;
  statusPending = true;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  try {
    const res = await fetch('/api/status', {cache:'no-store', signal:controller.signal});
    if (!res.ok) throw Error('status');
    const next = await res.json(); if (!Array.isArray(next.agents)) throw Error('catalog');
    state = next; online = true;
  } catch { online = false; }
  finally { clearTimeout(timeout); statusPending = false; if (state) updateUI(); }
}
async function restart(id, button) {
  button.disabled = true;
  try {
    const res = await fetch('/api/restart/' + id, {method:'POST', headers:{'X-Showcase-Token':state.token}});
    if (!res.ok) throw Error('restart');
    $$(`.frame-shell[data-service="${id}"] iframe`).forEach(f => f.remove());
    toast('已请求重新启动，请等待系统就绪'); await refreshStatus();
  } catch { toast('重试失败，请确认展示中心仍在运行'); }
  finally { button.disabled = false; }
}
async function downloadSample(link) {
  if (link.dataset.busy) return;
  link.dataset.busy = 'true';
  link.textContent = '正在下载…';
  try {
    const response = await fetch(link.href);
    if (!response.ok) throw new Error('download');
    const url = URL.createObjectURL(await response.blob());
    const save = document.createElement('a');
    save.href = url; save.download = link.download;
    document.body.appendChild(save); save.click(); save.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    toast('已发起下载：' + link.download);
  } catch { toast('样例下载失败，请重试'); }
  finally { delete link.dataset.busy; link.textContent = '下载样例 ↓'; }
}
document.addEventListener('click', e => {
  const sample = e.target.closest('a[data-download-sample]');
  if (sample) { e.preventDefault(); downloadSample(sample); return; }
  const b = e.target.closest('button'); if (!b) return;
  if (b.dataset.page || b.dataset.go) navigate(b.dataset.page || b.dataset.go);
  if (b.hasAttribute('data-scroll-agents')) $('#agent-list').scrollIntoView({behavior:'smooth'});
  if (b.dataset.selectService) selectService(b.dataset.agent, b.dataset.selectService);
  if (b.dataset.step !== undefined) { getProgress(b.dataset.agent).index = Number(b.dataset.step); updateRail(b.dataset.agent); }
  if (b.dataset.previous) { const p = getProgress(b.dataset.previous); p.index = Math.max(0,p.index-1); updateRail(b.dataset.previous); }
  if (b.dataset.complete) {
    const id = b.dataset.complete, p = getProgress(id); p.completed.add(p.index); p.index = Math.min(p.index+1,agent(id).steps.length-1); updateRail(id);
    $('.current-instruction', pages.get('work/'+id)).scrollIntoView({block:'nearest'});
  }
  if (b.dataset.resetSteps) { const p = getProgress(b.dataset.resetSteps); p.index = 0; p.completed.clear(); updateRail(b.dataset.resetSteps); toast('已重置步骤标记，系统输入保持不变'); }
  if (b.dataset.toggleGuide) {
    const el = pages.get('work/'+b.dataset.toggleGuide), rail = $('.step-rail',el); rail.hidden = !rail.hidden;
    el.classList.toggle('guide-collapsed',rail.hidden); b.setAttribute('aria-expanded',String(!rail.hidden)); b.textContent = rail.hidden ? '显示操作指引' : '收起操作指引';
  }
  if (b.dataset.focusAgent) expandFrame($(`.frame-shell[data-service="${currentService(b.dataset.focusAgent)}"]`, pages.get('work/'+b.dataset.focusAgent)));
  if (b.dataset.refreshAgent) {
    pendingReload = $(`.frame-shell[data-service="${currentService(b.dataset.refreshAgent)}"] iframe`,pages.get('work/'+b.dataset.refreshAgent));
    if (pendingReload) $('#confirm-dialog').showModal(); else toast('系统正在准备，请稍候');
  }
  if (b.dataset.wallOpen !== undefined) expandFrame($(`.wall-cell[data-cell="${b.dataset.wallOpen}"] .frame-shell:not([hidden])`));
  if (b.id === 'expand-wall') { const expanded = $('#wall-container').classList.toggle('wall-expanded'); b.textContent = expanded ? '收起展示' : '铺满展示 ⛶'; resizeWall(); }
  if (b.dataset.retry) restart(b.dataset.retry,b);
  if (b.hasAttribute('data-retry-materials')) loadMaterials(pages.get('materials'));
  if (b.id === 'retry-init') initialize();
});
document.addEventListener('change', e => {
  const index = e.target.dataset.wallSelect;
  if (index === undefined) return;
  const id = e.target.value, cell = $(`.wall-cell[data-cell="${index}"]`), target = $('.wall-frames',cell);
  wallIds[Number(index)] = id;
  $$('.frame-shell',target).forEach(f => { f.hidden = f.dataset.service !== id; });
  let frame = $(`.frame-shell[data-service="${id}"]`,target);
  if (!frame) { frame = createFrame(id); target.appendChild(frame); }
  frame.hidden = false; $('.wall-guide',cell).href = '#agent/' + service(id).group; resizeWall();
});
$('.skip-link').onclick = e => { e.preventDefault(); main.focus(); main.scrollIntoView({block:'start'}); };
$('#manage-button').onclick = () => { if (state) { renderStatus(); $('#status-dialog').showModal(); } };
$('#close-status').onclick = () => $('#status-dialog').close();
$('#cancel-reload').onclick = () => $('#confirm-dialog').close();
$('#confirm-reload').onclick = () => { if (pendingReload?.isConnected) pendingReload.src = pendingReload.src; $('#confirm-dialog').close(); };
$('#confirm-dialog').addEventListener('close', () => { pendingReload = null; });
$('#retry-connection').onclick = refreshStatus;
$('#fullscreen-button').onclick = async () => { try { if (document.fullscreenElement) await document.exitFullscreen(); else await document.documentElement.requestFullscreen(); } catch { toast('可使用浏览器菜单进入全屏'); } };
window.addEventListener('hashchange', showRoute); window.addEventListener('resize', resizeWall);
document.addEventListener('keydown', e => { if (e.key === 'Escape' && !closeFocus && $('#wall-container')?.classList.contains('wall-expanded')) { $('#wall-container').classList.remove('wall-expanded'); $('#expand-wall').textContent = '铺满展示 ⛶'; resizeWall(); } });
let initialized = false, initialRetryTimer;
async function initialize() {
  if (initialized || statusPending) return;
  clearTimeout(initialRetryTimer);
  main.innerHTML = '<p class="empty">正在连接 Agent…</p>';
  await refreshStatus();
  if (!state) {
    main.innerHTML = '<div class="empty"><h2>正在等待体验中心连接</h2><p>页面会自动重试，您也可以点击重新连接。若一直无法连接，请检查展示中心是否仍在运行。</p><button id="retry-init">重新连接</button></div>';
    initialRetryTimer = setTimeout(initialize, 3000);
    return;
  }
  initialized = true; main.innerHTML = '';
  $('#agent-nav').innerHTML = state.agents.map(a => `<button class="nav-button agent-nav-item" data-go="agent/${a.id}"><span class="dot" style="background:${a.color}"></span>${a.name}${a.services.length ? '' : '<small>方案</small>'}</button>`).join('');
  showRoute(); setInterval(refreshStatus, 4000);
}
initialize();
