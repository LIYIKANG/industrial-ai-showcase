const {chromium}=require(process.env.PLAYWRIGHT_PATH||'playwright');
const fs=require('fs'),path=require('path'),assert=require('assert');
const root=path.resolve(__dirname,'..'),reports=[],errors=[],external=[];
const ok=x=>{reports.push(x);console.log('PASS',x)};
(async()=>{const b=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});const ctx=await b.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});const p=await ctx.newPage();p.setDefaultTimeout(20000);
 p.on('pageerror',e=>errors.push(e.message));p.on('request',r=>{if(/^https?:/.test(r.url())&&!/^http:\/\/(127\.0\.0\.1|localhost):/.test(r.url()))external.push(r.url())});
 const active=()=>p.locator('#main-content > div:not([hidden])');
 try{
 const status=await(await fetch('http://127.0.0.1:18080/api/status')).json();assert.equal(status.agents.length,8);assert.equal(status.services.length,8);assert(status.services.every(s=>s.state==='ready'));
 const ms=status.services.find(s=>s.id==='maturity');assert.equal(ms.port,18088);ok('New maturity service and existing 7 services are healthy together');
 await p.goto('http://127.0.0.1:18080/#agent/maturity');await active().getByRole('heading',{name:'企业 AI 成熟度诊断 Agent',exact:true}).waitFor();assert.equal(await active().locator('.full-steps li').count(),4);
 await active().getByRole('button',{name:'进入操作工作台',exact:false}).click();
 const f=p.frameLocator('.project-frames .frame-shell[data-service="maturity"] iframe');
 await f.getByRole('button',{name:'开始企业评估',exact:false}).waitFor();ok('Dedicated maturity guide opens the embedded working assessment');
 await p.screenshot({path:path.join(root,'runtime/maturity-intro.png'),fullPage:true});
 await f.getByRole('button',{name:'开始企业评估',exact:false}).click();assert(await f.locator('[data-next]').isDisabled());
 await f.locator('[data-field="industry"][data-value="other"]').click();await f.locator('#industry-text').fill('注塑模具加工');await f.getByText('依据关键词，将采用“机械装备与通用设备”的参考值。',{exact:true}).waitFor();
 await f.locator('[data-next]').click();await f.locator('[data-field="size"][data-value="s2"]').click();await f.locator('[data-next]').click();
 assert(await f.locator('[data-next]').isDisabled());
 for(const id of ['p_delivery','p_quality','p_inventory'])await f.locator(`[data-field="pain"][data-value="${id}"]`).click();
 assert(await f.locator('[data-field="pain"][data-value="p_equipment"]').isDisabled());
 await f.locator('[data-field="pain"][data-value="p_inventory"]').click();await f.locator('[data-field="pain"][data-value="p_quality"]').click();
 await f.locator('[data-next]').click();ok('Required choices, keyword classification and three-pain limit work');
 await f.locator('[data-field="answers"][data-value="2"]').click();
 await p.reload();await f.locator('.question-card[data-step="f_delivery"]').waitFor();assert.equal(await f.locator('[data-field="answers"][data-value="2"]').getAttribute('aria-pressed'),'true');ok('Refresh restores current question and selected answer');
 for(let i=0;i<10;i++){
  const card=f.locator('.question-card');assert(!['review','quickwin'].includes(await card.getAttribute('data-step')));
  await f.locator('[data-field="answers"][data-value="2"]').click();await f.locator('[data-next]').click();
 }
 await f.locator('.question-card[data-step="quickwin"]').waitFor();
 await f.locator('[data-field="quickwin"][data-value="qw_doc"]').click();await f.locator('[data-field="quickwin"][data-value="qw_none"]').click();
 assert.equal(await f.locator('[data-field="quickwin"][data-value="qw_doc"]').getAttribute('aria-pressed'),'false');
 await f.locator('[data-field="quickwin"][data-value="qw_report"]').click();assert.equal(await f.locator('[data-field="quickwin"][data-value="qw_none"]').getAttribute('aria-pressed'),'false');
 await f.locator('[data-next]').click();await f.getByRole('heading',{name:'核对本次答卷',exact:true}).waitFor();assert.equal(await f.locator('.answer-list li').count(),10);ok('Complete human walkthrough reaches review; exclusive quick-win choices behave correctly');
 await f.locator('[data-generate]').click();await f.locator('#total-score').waitFor();assert.equal(await f.locator('#total-score').innerText(),'67');
 assert.equal(await f.locator('.radar svg').count(),1);assert(await f.locator('.recommendations article').count()>0);assert.equal(await f.locator('input[type="tel"]').count(),0);ok('Answer-driven score 67, five-axis chart and recommendations appear without contact collection');
 await f.locator('[data-edit]').first().click();await f.locator('[data-go="pains"]').first().click();
 await f.locator('[data-field="pain"][data-value="p_delivery"]').click();await f.locator('[data-field="pain"][data-value="p_equipment"]').click();
 await f.locator('.stage-nav [data-go="review"]').click();assert(await f.locator('[data-generate]').isDisabled());await f.locator('.missing').waitFor();
 await f.locator('.answer-list [data-go="f_equipment"]').click();await f.locator('[data-field="answers"][data-value="2"]').click();
 await f.locator('.stage-nav [data-go="review"]').click();await f.locator('[data-generate]').click();assert.equal(await f.locator('#total-score').innerText(),'67');ok('Changing pain points requires the new follow-up; removed answers do not contaminate score');
 await f.locator('[data-reset]').click();await f.locator('#cancel-reset').click();assert.equal(await f.locator('#total-score').innerText(),'67');
 await f.locator('[data-reset]').click();await f.locator('#confirm-reset').click();await f.locator('[data-demo]').click();
 await f.locator('[data-generate]').click();assert.equal(await f.locator('#total-score').innerText(),'49');await f.getByText('虚构示例报告',{exact:true}).waitFor();ok('Reset cancellation preserves results; confirmed reset and labeled example produce 49');
 const event=p.waitForEvent('download');await f.locator('[data-export]').click();const dl=await event;const dest=path.join(root,'runtime/maturity-report.html');await dl.saveAs(dest);
 const html=fs.readFileSync(dest,'utf8');assert(html.includes('>49</strong>'));assert(html.includes('原方案估算目标'));assert(html.includes('<dt>业务痛点</dt>'));assert(html.includes('<dt>优先改善环节</dt>'));assert(!/<script\b/i.test(html));assert(html.includes('<details class="report-section answer-details" open>'));
 const offline=await ctx.newPage();await offline.goto('file://'+dest);assert.equal(await offline.locator('#total-score').innerText(),'49');assert.equal(await offline.locator('.answer-details li').count(),11);await offline.close();ok('Downloaded report opens offline with exact score, full answers and no executable scripts');
 await p.screenshot({path:path.join(root,'runtime/maturity-report-desktop.png'),fullPage:true});
 await f.locator('[data-edit]').first().click();await f.locator('.answer-list [data-go="D1"]').click();await f.locator('[data-field="answers"][data-value="0"]').click();
 await f.locator('.stage-nav [data-go="review"]').click();assert.equal(await f.locator('#report').count(),0);await f.locator('[data-generate]').click();assert.equal(await f.locator('#total-score').innerText(),'39');ok('Editing an answer invalidates the old report and recomputes from 49 to 39');
 await p.locator('#agent-nav [data-go="agent/decision"]').click();await active().locator('.start-agent[data-go="work/decision"]').waitFor();await p.locator('#agent-nav [data-go="agent/maturity"]').click();await active().locator('.start-agent[data-go="work/maturity"]').click();assert.equal(await f.locator('#total-score').innerText(),'39');ok('Switching Agents preserves assessment state');
 await p.locator('#main-nav [data-page="wall"]').click();await p.locator('[data-wall-select="0"]').selectOption('maturity');
 const wall=p.frameLocator('.wall-cell[data-cell="0"] .frame-shell[data-service="maturity"] iframe');await p.locator('[data-wall-open="0"]').click();await wall.locator('#total-score').waitFor();assert.equal(await wall.locator('#total-score').innerText(),'39');await p.locator('.focus-exit').click();ok('Maturity assessment is available in multi-screen view and opens full-size');
 await p.locator('#agent-nav [data-go="agent/maturity"]').click();await active().locator('.start-agent[data-go="work/maturity"]').click();
 for(const width of [800,390]){
  await p.setViewportSize({width,height:1000});assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  assert(await f.locator('body').evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await active().locator('.project-frames').scrollIntoViewIfNeeded();await p.screenshot({path:path.join(root,`runtime/maturity-responsive-${width}.png`),fullPage:true});
 }
 ok('Tablet and phone portal and assessment layouts avoid horizontal overflow');
 const isolated=await ctx.newPage();await isolated.goto(ms.url);await isolated.evaluate(()=>sessionStorage.setItem('showcase:maturity:v1','{"version":1,"step":"report","answers":{"D1":999}}'));await isolated.reload();await isolated.locator('.question-card[data-step="industry"]').waitFor();assert.equal(await isolated.locator('#report').count(),0);await isolated.close();ok('Malformed or incomplete saved state cannot manufacture a completed report');
 assert.equal(errors.length,0,errors.join('\n'));assert.equal(external.length,0,external.join('\n'));ok('No browser exceptions or outbound third-party requests during assessment');
 fs.writeFileSync(path.join(root,'runtime/maturity-verification.json'),JSON.stringify({checked_at:new Date().toISOString(),passed:reports},null,2));
 }catch(e){await p.screenshot({path:path.join(root,'runtime/maturity-failure.png'),fullPage:true}).catch(()=>{});throw e;}finally{await b.close()}
})().catch(e=>{console.error(e);process.exit(1)});
