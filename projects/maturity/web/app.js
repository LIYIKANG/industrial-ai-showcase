import { CORE_QUESTIONS, COPY, DIMS, DIM_WEIGHTS, INDUSTRIES, INDUSTRY_NAME, MAX_PAINS, PAINS, QUICK_WIN_QUESTION, SIZES } from './engine/config.js';
import { activeQuestions } from './engine/scoring.js';
import { classifyIndustry } from './engine/rules.js';
import { STORAGE_KEY, initialState, sanitizeState, flowSteps, stepReady, missingSteps, updatePains, toggleQuickWin, buildReport, demoState } from './flow.js';
const $=s=>document.querySelector(s), app=$('#app');
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state=initialState(),storeAvailable=true,toastTimer;
try { state=sanitizeState(JSON.parse(sessionStorage.getItem(STORAGE_KEY)||'null')); } catch { storeAvailable=false; }
function save() { try { sessionStorage.setItem(STORAGE_KEY,JSON.stringify(state)); } catch { storeAvailable=false; } }
function toast(text) { clearTimeout(toastTimer);$('#toast').textContent=text;$('#toast').classList.add('show');toastTimer=setTimeout(()=>$('#toast').classList.remove('show'),3500); }
function go(step) {state.step=step;save();render();window.scrollTo({top:0});app.focus({preventScroll:true});}
function statusNote() {return `<p class="session-note">${state.demo?'虚构示例答卷 · 可修改后重新计算。':'仅在本浏览器标签页暂存，不上传答案或联系方式。'}${storeAvailable?' 刷新可继续；关闭标签页后不保证保留。':' 浏览器暂存不可用，请完成后下载报告。'}</p>`;}
function heading(kicker,title,description='') {return `<div class="eyebrow">${kicker}</div><h1>${title}</h1>${description?`<p class="description">${description}</p>`:''}`;}
function choiceList(items,field,value,multiple=false) {
  return `<div class="choices" role="group" aria-label="${field==='answers'?'选择符合当前情况的答案':'选择选项'}">${items.map((o,i)=>{
    const key=String(o.id??i),selected=multiple?value.includes(key):String(value)===key;
    const limited=field==='pain'&&!selected&&state.painIds.length>=MAX_PAINS;
    return `<button type="button" class="choice ${selected?'selected':''}" data-field="${field}" data-value="${esc(key)}" aria-pressed="${selected}" ${limited?'disabled':''}><span class="choice-symbol">${selected?'✓':multiple?'+':String(i+1).padStart(2,'0')}</span><span>${esc(o.label??o.name)}</span></button>`;
  }).join('')}</div>`;
}
function progress() {
  const steps=flowSteps(state).filter(x=>x!=='report'), index=steps.indexOf(state.step),count=steps.length;
  return `<div class="progress"><div><span>答题与核对进度</span><strong>第 ${index+1} / ${count} 步</strong></div><progress value="${index+1}" max="${count}" aria-label="评估进度"></progress><nav class="stage-nav" aria-label="评估阶段"><button data-go="industry" class="${['industry','size','pains'].includes(state.step)?'current':''}">01 企业画像</button><button data-jump-questions class="${activeQuestions(state.painIds).some(q=>q.id===state.step)?'current':''}">02 现状评估</button><button data-go="quickwin" class="${state.step==='quickwin'?'current':''}">03 优先场景</button><button data-go="review" class="${state.step==='review'?'current':''}">04 核对与报告</button></nav></div>`;
}
function actions() {
  const missing=missingSteps(state);
  return `<div class="step-actions"><button data-back>← 上一步</button><span>${state.step==='review'?(missing.length?`还有 ${missing.length} 项待完成`:'必答项已完成，可生成报告'):stepReady(state)?'已选择，可继续':'请选择后继续'}</span><button class="primary" ${state.step==='review'?'data-generate':'data-next'} ${(!stepReady(state)||(state.step==='review'&&missing.length))?'disabled':''}>${state.step==='review'?'生成诊断报告':'下一步 →'}</button></div>`;
}
function review() {
  const qs=activeQuestions(state.painIds), industry=INDUSTRIES.find(i=>i.id===state.industryChoice)?.name||'未填写';
  const size=SIZES.find(s=>s.id===state.sizeId)?.label||'未填写';
  return `${heading('REVIEW YOUR ANSWERS','核对本次答卷','报告只依据以下作答生成。请确认它反映企业当前情况。')}${state.demo?'<div class="notice">当前为虚构示例，用于体验报告。正式评估请按企业实际情况修改。</div>':''}<div class="review-summary"><div><small>行业</small><strong>${esc(industry)}${state.industryChoice==='other'&&state.industryFreeText?' · '+esc(state.industryFreeText):''}</strong><button data-go="industry">修改行业</button></div><div><small>团队规模</small><strong>${esc(size)}</strong><button data-go="size">修改规模</button></div></div>
  <section class="review-group"><header><h2>业务痛点</h2><button data-go="pains">修改痛点</button></header><p>${PAINS.filter(p=>state.painIds.includes(p.id)).map(p=>esc(p.label)).join('；')||'未填写'}</p></section>
  <section class="review-group"><header><h2>现状作答 <small>${qs.length} 题</small></h2></header><ol class="answer-list">${qs.map(q=>`<li><div><span>${esc(q.text)}</span><strong class="${state.answers[q.id]===undefined?'missing':''}">${esc(q.options[state.answers[q.id]]?.label||'未作答')}</strong></div><button data-go="${q.id}">修改<span class="sr-only">${esc(q.text)}</span></button></li>`).join('')}</ol></section>
  <section class="review-group"><header><h2>优先改善环节</h2><button data-go="quickwin">修改场景</button></header><p>${QUICK_WIN_QUESTION.options.filter(o=>state.quickWins.includes(o.id)).map(o=>esc(o.label)).join('；')||'未填写'}</p></section>`;
}
function render() {
  let content='';
  if(state.step==='intro') {
    app.innerHTML=`<section class="intro">${heading('ENTERPRISE AI READINESS','五分钟，看清企业的 AI 就绪度','从数据、系统、管理与组织出发，梳理当前基础，找到适合优先尝试的 AI 场景。')}<div class="intro-art" aria-hidden="true"><span>数据基础</span><span>系统能力</span><b>AI<br><small>READINESS</small></b><span>管理成熟度</span><span>组织与人</span></div><div class="intro-points"><div><b>01</b><h2>描述企业现状</h2><p>选择行业、规模与最多 3 项痛点。</p></div><div><b>02</b><h2>完成针对性问答</h2><p>9 道基础题，加上所选痛点的追问。</p></div><div><b>03</b><h2>查看报告与建议</h2><p>得分、雷达图、短板及推荐场景。</p></div></div><div class="intro-actions"><button class="primary" data-go="industry">开始企业评估 →</button><button data-demo>载入示例答卷</button></div><p class="hint">也可以先载入虚构示例，查看完整答卷后生成报告。</p><div class="scope-note">沿用官网 AI Readiness 题库和本地评分规则。行业参考值为原方案的经验估计，报告不代表认证、真实行业排名或专家现场诊断。</div>${statusNote()}</section>`;
    return;
  }
  if(state.step==='report') {
    if(missingSteps(state).length){go('review');return;}
    app.innerHTML=renderReport(buildReport(state));return;
  }
  if(!flowSteps(state).includes(state.step)){go('intro');return;}
  if(state.step==='industry') {
    content=heading('COMPANY PROFILE · 01','企业主要属于哪个行业？','按主要业务选择。如果不在列表中，可选“其他行业”补充说明。')+choiceList(INDUSTRIES,'industry',state.industryChoice);
    if(state.industryChoice==='other') {
      const classification=classifyIndustry(state.industryFreeText);
      content+=`<div class="free-industry"><label for="industry-text">行业描述 <span>选填，只填写行业，不需要公司名称</span></label><input id="industry-text" maxlength="80" value="${esc(state.industryFreeText)}" placeholder="例如：注塑模具加工"><p id="industry-match">${classification.method==='keyword'?`依据关键词，将采用“${esc(classification.industryName)}”的参考值。`:'未匹配具体行业，使用“其他行业”参考值。'}</p></div>`;
    }
  } else if(state.step==='size') {
    content=heading('COMPANY PROFILE · 02','企业的团队规模大概是？','用于描述企业背景，不直接增加或扣减成熟度分数。')+choiceList(SIZES,'size',state.sizeId);
  } else if(state.step==='pains') {
    content=heading('COMPANY PROFILE · 03','哪些问题最需要改善？',`至少选择 1 项，最多 ${MAX_PAINS} 项。每个痛点会增加一道针对性追问。`)+`<p class="selection-count" role="status">已选择 ${state.painIds.length} / ${MAX_PAINS} 项${state.painIds.length===MAX_PAINS?' · 如需更换，请先取消一项':''}</p>`+choiceList(PAINS,'pain',state.painIds,true);
  } else if(state.step==='quickwin') {
    content=heading('PRIORITY SCENARIOS',QUICK_WIN_QUESTION.text,'可以多选。“说不上来”与其他选项互斥。')+choiceList(QUICK_WIN_QUESTION.options,'quickwin',state.quickWins,true);
  } else if(state.step==='review') {
    content=review();
  } else {
    const q=activeQuestions(state.painIds).find(q=>q.id===state.step);
    const follow=PAINS.some(p=>p.followUp.id===q.id),list=follow?activeQuestions(state.painIds).filter(q=>!CORE_QUESTIONS.includes(q)):CORE_QUESTIONS;
    content=heading(`${follow?'痛点追问':'基础评估'} · ${list.findIndex(item=>item.id===q.id)+1} / ${list.length} · ${DIMS[q.dim].name}`,esc(q.text),'选择最接近当前情况的一项；选择后点击下一步，可以随时返回修改。')+choiceList(q.options,'answers',state.answers[q.id]);
  }
  app.innerHTML=`${progress()}<section class="question-card" data-step="${state.step}">${content}${actions()}</section><div class="bottom-tools"><button data-reset>清空本次评估</button><span>${CORE_QUESTIONS.length} 道基础题 · ${state.painIds.length} 道痛点追问</span></div>${statusNote()}`;
}
function radar(axes) {
  const cx=190,cy=162,r=102;
  const point=(i,v)=>{const a=-Math.PI/2+i*Math.PI*2/axes.length;return [cx+Math.cos(a)*r*v/100,cy+Math.sin(a)*r*v/100];};
  const polygon=values=>values.map((v,i)=>point(i,v).join(',')).join(' ');
  return `<figure class="radar"><svg viewBox="0 0 380 330" role="img" aria-label="五维雷达图：${axes.map(a=>esc(a.label)+' '+a.value+' 分').join('；')}">${[25,50,75,100].map(v=>`<polygon points="${polygon(axes.map(()=>v))}" fill="none" stroke="#dce5ef"/>`).join('')}${axes.map((a,i)=>`<line x1="${cx}" y1="${cy}" x2="${point(i,100)[0]}" y2="${point(i,100)[1]}" stroke="#e4eaf2"/>`).join('')}<polygon points="${polygon(axes.map(a=>a.bench))}" fill="none" stroke="#bf8546" stroke-width="2" stroke-dasharray="5 5"/><polygon points="${polygon(axes.map(a=>a.value))}" fill="#3b6bbf22" stroke="#3b6bbf" stroke-width="2.5"/>${axes.map((a,i)=>{const [x,y]=point(i,121);return `<text x="${x}" y="${y}" text-anchor="${Math.abs(x-cx)<8?'middle':x>cx?'start':'end'}" dominant-baseline="middle" fill="#435a75" font-size="11">${esc(a.label)} ${a.value}</text>`;}).join('')}</svg><figcaption><span>— 本次作答</span><span>┄ 框架参考值（估计）</span></figcaption></figure>`;
}
function renderReport(report) {
  const {result:r,input,cards,flags}=report;
  return `<section class="report" id="report"><header class="report-header"><div>${heading('YOUR AI READINESS REPORT','企业 AI 成熟度诊断报告',`${INDUSTRY_NAME[input.industryId]} · ${SIZES.find(s=>s.id===input.sizeId).label} · ${new Date(report.generatedAt).toLocaleString('zh-CN')}`)}${report.demo?'<span class="demo-badge">虚构示例报告</span>':''}</div><div class="report-actions"><button data-edit>修改答卷</button><button class="primary" data-export>下载完整报告</button><button data-print>打印 / 保存 PDF</button></div></header>
    <div class="score-grid"><div class="score-card"><small>综合成熟度</small><div><strong id="total-score">${r.totalScore}</strong><span>/ 100</span></div><h2>${r.tier.name}</h2><p>${r.tier.line}</p><small>原框架行业参考总分：${r.benchmark.total}（经验估计）</small></div><div class="radar-card">${radar(r.radar)}</div></div>
    <div class="scope-note">${esc(COPY.disclaimer)} 本页为本地规则计算，没有调用大模型或人工专家。“AI 行动力”由组织维度中的两道 AI 相关题单独展示，不额外计入总分。</div>
    <section class="report-section"><h2>各维度得分与计算依据</h2><div class="table-scroll"><table><thead><tr><th>评估维度</th><th>本次得分</th><th>参考值（估计）</th><th>总分权重</th></tr></thead><tbody>${Object.entries(DIMS).map(([id,d])=>`<tr><th>${d.name}</th><td>${r.dims[id]}</td><td>${r.benchmark.dims[id]}</td><td>${Math.round(DIM_WEIGHTS[id]*100)}%</td></tr>`).join('')}<tr><th>AI 行动力</th><td>${r.aiAction}</td><td>${r.benchmark.ai}</td><td>组织维度的子项</td></tr></tbody></table></div><p class="hint">每题 0–3 分；维度分为该维度已完成题目的得分率 × 100。四个维度分别取整后加权，总分再次取整。基础题和当前所选痛点追问均参与计算。</p></section>
    ${flags.length?`<section class="report-section flags"><h2>需要进一步核对</h2><ul>${flags.map(f=>`<li>${esc(f)}</li>`).join('')}</ul></section>`:''}
    <section class="report-section"><h2>建议优先尝试的场景</h2><p class="hint">根据所选痛点、期望改善环节和现状答案匹配；先确认前置条件，再开展小范围试点。</p><div class="recommendations">${cards.map((c,i)=>`<article><div class="recommendation-heading"><span>${String(i+1).padStart(2,'0')}</span><h3>${esc(c.title)}</h3>${i===0?'<b>优先考虑</b>':''}</div><p>${esc(c.desc)}</p><dl><div><dt>前置条件</dt><dd>${esc(c.prereq)}<small>${c.prereqMet?'根据当前作答初步符合，仍需结合实际资料确认。':esc(c.prereqHint)}</small></dd></div><div><dt>原方案参考周期</dt><dd>${esc(c.cycle)}<small>实际周期需结合系统接口、数据质量和实施范围确认。</small></dd></div><div><dt>原方案估算目标</dt><dd>${esc(c.effect)}<small>需要试点验证，不作为收益承诺。</small></dd></div></dl></article>`).join('')}</div></section>
    <section class="report-section"><h2>本次快评建议</h2>${report.summary.split('\n').map(t=>`<p>${esc(t)}</p>`).join('')}<p class="hint">以上文字由题库规则组合生成，供后续访谈和方案讨论使用。</p></section>
    <details class="report-section answer-details"><summary>查看本次完整答卷（${activeQuestions(input.painIds).length} 道计分题）</summary><dl class="answer-context"><dt>业务痛点</dt><dd>${PAINS.filter(p=>input.painIds.includes(p.id)).map(p=>esc(p.label)).join('；')}</dd><dt>优先改善环节</dt><dd>${QUICK_WIN_QUESTION.options.filter(o=>input.quickWins.includes(o.id)).map(o=>esc(o.label)).join('；')}</dd>${input.industryFreeText?`<dt>补充的行业描述</dt><dd>${esc(input.industryFreeText)}</dd>`:''}</dl><ol>${activeQuestions(input.painIds).map(q=>`<li><p>${esc(q.text)}</p><strong>${esc(q.options[input.answers[q.id]].label)}</strong></li>`).join('')}</ol></details>
    <footer class="report-footer"><button data-edit>返回修改答卷</button><button data-reset>清空并重新评估</button><p>未收集公司名、联系人或手机号。完成体验后，可清空本次评估再交给下一位客户。</p></footer></section>`;
}
function exportReport() {
  const report=buildReport(state), content=renderReport(report);
  // Self-contained offline report, no scripts, personal contact forms, or external resources.
  const css='body{font-family:system-ui,"Microsoft YaHei",sans-serif;color:#20334d;margin:35px auto;max-width:950px;line-height:1.8;padding:0 20px}h1{font-size:28px}h2{font-size:20px;margin-top:28px}h3{font-size:17px}small,.hint{color:#687a90}.eyebrow{font-size:11px;color:#728bad}.score-grid{display:flex;gap:25px;align-items:center}.score-card{flex:1}#total-score{font-size:64px}.radar-card{flex:1}.radar svg{width:100%;max-width:380px}.radar figcaption{display:flex;gap:16px;font-size:12px}table{width:100%;border-collapse:collapse}td,th{padding:10px;text-align:left;border-bottom:1px solid #dbe3ed}article,.scope-note,.flags{border:1px solid #dbe3ed;border-radius:8px;padding:18px;margin:15px 0}.scope-note{background:#f4f7fb;font-size:13px}dt{font-weight:600}dd{margin:0 0 12px}dd small{display:block}button,.report-actions{display:none}summary{font-weight:600}.recommendation-heading{display:flex;align-items:center;gap:12px}.recommendation-heading b{font-size:12px}.report-footer{font-size:12px;color:#71839a}li{margin-bottom:10px}@media(max-width:600px){.score-grid{display:block}}@media print{body{margin:0;font-size:11pt}.score-card{break-inside:avoid}article{break-inside:avoid}.report-actions,.report-footer{display:none}}';
  const html=`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"><title>企业 AI 成熟度诊断报告</title><style>${css}</style><body>${content.replace('<details class="report-section answer-details">','<details class="report-section answer-details" open>')}</body></html>`;
  const url=URL.createObjectURL(new Blob([html],{type:'text/html;charset=utf-8'})),a=document.createElement('a');
  a.href=url;a.download=`企业AI成熟度报告-${report.demo?'示例-':''}${report.generatedAt.slice(0,10)}.html`;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);toast('已下载完整报告，可离线打开或打印');
}
app.addEventListener('click',event=>{
  const b=event.target.closest('button');if(!b||b.disabled)return;
  if(b.dataset.go){go(b.dataset.go);return;}
  if(b.hasAttribute('data-jump-questions')){go(activeQuestions(state.painIds)[0].id);return;}
  if(b.dataset.field) {
    const {field,value}=b.dataset;
    if(field==='industry')state.industryChoice=value;
    if(field==='size')state.sizeId=value;
    if(field==='answers')state.answers[state.step]=Number(value);
    if(field==='pain')updatePains(state,state.painIds.includes(value)?state.painIds.filter(x=>x!==value):[...state.painIds,value]);
    if(field==='quickwin')toggleQuickWin(state,value);
    state.reportAt='';save();render();
    const target=app.querySelector(`[data-field="${field}"][data-value="${value}"]`);target?.focus({preventScroll:true});return;
  }
  if(b.hasAttribute('data-next')){if(stepReady(state))go(flowSteps(state)[flowSteps(state).indexOf(state.step)+1]);return;}
  if(b.hasAttribute('data-back')){const i=flowSteps(state).indexOf(state.step);go(i>0?flowSteps(state)[i-1]:'intro');return;}
  if(b.hasAttribute('data-demo')){state=demoState();go('review');return;}
  if(b.hasAttribute('data-generate')){const missing=missingSteps(state);if(missing.length){go(missing[0]);toast('请先补齐未完成的内容');return;}state.reportAt=new Date().toISOString();go('report');return;}
  if(b.hasAttribute('data-edit')){state.reportAt='';go('review');return;}
  if(b.hasAttribute('data-reset')){$('#reset-dialog').showModal();return;}
  if(b.hasAttribute('data-export')){exportReport();return;}
  if(b.hasAttribute('data-print')){window.print();}
});
app.addEventListener('input',e=>{
  if(e.target.id==='industry-text'){
    state.industryFreeText=e.target.value.slice(0,80);state.reportAt='';save();
    const c=classifyIndustry(state.industryFreeText);$('#industry-match').textContent=c.method==='keyword'?`依据关键词，将采用“${c.industryName}”的参考值。`:'未匹配具体行业，使用“其他行业”参考值。';
  }
});
$('#cancel-reset').onclick=()=>$('#reset-dialog').close();
$('#confirm-reset').onclick=()=>{state=initialState();save();$('#reset-dialog').close();render();window.scrollTo({top:0});app.focus({preventScroll:true});};
window.addEventListener('beforeprint',()=>document.querySelectorAll('.answer-details').forEach(el=>{el.dataset.wasOpen=el.open?'1':'0';el.open=true;}));
window.addEventListener('afterprint',()=>document.querySelectorAll('.answer-details').forEach(el=>{el.open=el.dataset.wasOpen==='1';}));
render();
