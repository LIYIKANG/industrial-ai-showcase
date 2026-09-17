// End-to-end customer walkthrough and regression checks against the running container.
const {chromium} = require(process.env.PLAYWRIGHT_PATH || 'playwright');
const fs = require('fs'), path = require('path'), assert = require('assert');
const base = path.resolve(__dirname,'..'), tests = [], errors = [];
const url = process.env.SHOWCASE_TEST_URL || 'http://127.0.0.1:18080';
const output = name => path.join(base,'runtime',name);
function ok(name) { tests.push(name); console.log('PASS',name); }
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const ctx = await browser.newContext({viewport:{width:1440,height:1000}, acceptDownloads:true});
  const page = await ctx.newPage();
  page.setDefaultTimeout(30000);
  page.on('pageerror', e => errors.push(e.message));
  page.on('dialog', d => { errors.push('Unexpected dialog: '+d.message()); d.dismiss(); });
  const active = () => page.locator('#main-content > div:not([hidden])');
  const frame = id => page.frameLocator(`.project-frames .frame-shell[data-service="${id}"] iframe`);
  async function go(hash) { await page.evaluate(h => { location.hash=h; },hash); await page.waitForFunction(h => location.hash==='#'+h,hash==='work/maintenance'?'agent/maintenance':hash==='unknown-route'?'overview':hash); await page.waitForTimeout(80); }
  async function openAgent(id) {
    await page.locator(`#agent-nav [data-go="agent/${id}"]`).click();
    await active().locator(`.start-agent[data-go="work/${id}"]`).click();
    await active().locator('.step-rail').waitFor();
  }
  async function saveDownload(button,name) { const event=page.waitForEvent('download'); await button.click(); const d=await event; await d.saveAs(output(name)); assert(fs.statSync(output(name)).size>100); }
  try {
    const data = await (await fetch(url+'/api/status')).json();
    assert.equal(data.agents.length,8); assert.equal(data.agents.filter(a=>a.services.length).length,7);
    assert.equal(data.services.length,8); assert(data.services.every(s=>s.state==='ready')); ok('7 operable Agents, 1 proposal and 8 healthy services');
    assert(!/安必成|添正|李奕慷/.test(JSON.stringify(data.agents))); ok('customer-facing Agent catalog uses functional names');
    await page.goto(url); await page.locator('.agent-card').last().waitFor(); assert.equal(await page.locator('.agent-card').count(),8);
    for (const a of data.agents) {
      await page.locator(`#agent-nav [data-go="agent/${a.id}"]`).click();
      await active().getByRole('heading',{name:a.name,exact:true}).waitFor();
      await active().locator('.full-steps li').last().waitFor();
      assert.equal(await active().locator('.full-steps li').count(),4);
      await active().getByText('为什么需要它',{exact:true}).waitFor();
      await active().getByText('最终得到什么',{exact:true}).waitFor();
      await active().getByText('开始前准备',{exact:true}).waitFor();
      assert.equal(await active().locator('iframe').count(),0);
    }
    ok('all 8 standalone guide pages explain purpose, preparation, actions and outcomes');
    await go('work/maintenance'); await page.waitForURL('**/#agent/maintenance');
    await active().getByRole('heading',{name:'当前阶段：能力方案'}).waitFor(); ok('proposal does not advertise an unavailable operating system');
    await openAgent('document'); let pdf=frame('pdf');
    await pdf.locator('#load-demo-pdf').waitFor();
    const uploadY=await pdf.locator('.upload-panel').evaluate(e=>e.offsetTop), sideY=await pdf.locator('.side-panel').evaluate(e=>e.offsetTop);
    assert(uploadY<sideY); ok('narrow PDF layout puts upload before recognition controls');
    await pdf.locator('#load-demo-pdf').click(); await pdf.locator('#start-button').click();
    await pdf.getByText('SKU 价格明细',{exact:true}).waitFor({timeout:25000});
    const edited='QA-CUSTOMER-EDIT-110';
    await pdf.locator('.line-item-input[data-field="product_name"]').first().fill(edited);
    const regen=page.waitForRequest(r=>r.url().includes('/api/customer/regenerate')&&r.method()==='POST');
    await saveDownload(pdf.getByRole('button',{name:'下载结果',exact:true}),'agent-edited.xlsx');
    assert((await regen).postDataJSON().line_items.some(x=>x.product_name===edited));
    ok('PDF edited fields are revalidated before direct Excel download');
    await active().locator('[data-complete="document"]').click();
    await active().getByText('手动进度 1 / 4',{exact:true}).waitFor();
    await active().locator('[data-select-service="pdf-backup"]').click();
    await active().getByText('手动进度 0 / 4',{exact:true}).waitFor();
    pdf=frame('pdf-backup'); await pdf.locator('#load-demo-pdf').click(); await pdf.locator('#start-button').click();
    await pdf.getByText('SKU 价格明细',{exact:true}).waitFor({timeout:25000});
    await pdf.locator('.line-item-input[data-field="product_name"]').first().fill('QA-WORD-EDIT-110');
    await saveDownload(pdf.getByRole('button',{name:'下载 Word',exact:true}),'agent-edited.docx');
    ok('Word variant exports current edited fields and has separate checklist progress');
    await active().locator('[data-select-service="pdf"]').click();
    await active().getByText('手动进度 1 / 4',{exact:true}).waitFor();
    assert.equal(await frame('pdf').locator('.line-item-input[data-field="product_name"]').first().inputValue(),edited);
    await active().locator('[data-refresh-agent="document"]').click(); await page.locator('#cancel-reload').click();
    assert.equal(await frame('pdf').locator('.line-item-input[data-field="product_name"]').first().inputValue(),edited);
    ok('switching versions and cancelling reload preserve entered values');
    await active().locator('[data-reset-steps="document"]').click();
    await active().getByText('手动进度 0 / 4',{exact:true}).waitFor();
    assert.equal(await frame('pdf').locator('.line-item-input[data-field="product_name"]').first().inputValue(),edited);
    for(let i=0;i<4;i++) await active().locator('[data-complete="document"]').click();
    await active().locator('.completion').waitFor(); ok('manual checklist completes and resets without clearing application data');
    await openAgent('decision'); const solver=frame('solver');
    await solver.locator('#loadExample').click(); await solver.locator('#solveBtn').click();
    await solver.getByText('目标值 2360',{exact:true}).waitFor({timeout:20000});
    await solver.locator('[data-view="modelView"]').click();
    const model=JSON.parse(await solver.locator('#modelEditor').inputValue());
    // Change the objective coefficient, then solve directly without applying: old code used the old model.
    const objective=model.objective; console.log('Model objective keys',Object.keys(objective));
    if(Array.isArray(objective.terms)) objective.terms.forEach(t=>{t.coefficient*=2});
    else if(objective.coefficients) Object.keys(objective.coefficients).forEach(k=>{objective.coefficients[k]*=2});
    else throw Error('Unknown objective shape');
    await solver.locator('#modelEditor').fill(JSON.stringify(model,null,2));
    await solver.locator('#solveBtn').click();
    await solver.getByText('目标值 4720',{exact:true}).waitFor({timeout:20000});
    ok('solver uses the visible edited model and updates objective from 2360 to 4720');
    await solver.locator('[data-view="modelView"]').click(); await solver.locator('#modelEditor').fill('{invalid');
    await solver.locator('#solveBtn').click(); await solver.getByText(/JSON 格式错误/).waitFor();
    assert.equal(await solver.getByText('目标值 4720',{exact:true}).count(),0);
    ok('invalid model cannot silently reuse a previous successful result');
    await solver.locator('#loadExample').click(); await solver.locator('#solveBtn').click(); await solver.getByText('目标值 2360',{exact:true}).waitFor({timeout:20000});
    await openAgent('quotation'); const bot=frame('bot');
    await bot.locator('[data-example="missing"]').click(); await bot.locator('#sendBtn').click(); await bot.getByText('字段校验失败',{exact:false}).waitFor();
    ok('quotation workflow provides actionable validation feedback');
    await openAgent('formulation'); const blend=frame('blend');
    await blend.getByText('配料求解',{exact:true}).click(); await blend.locator('#btnDemoOrder').click();
    assert.equal(await blend.locator('[data-f="weight"]').inputValue(),'3000');
    await blend.locator('#btnSolve').click(); await blend.getByText('求解成功，全部订单达标',{exact:true}).waitFor({timeout:45000});
    await saveDownload(blend.getByRole('button',{name:'导出 CSV',exact:true}).first(),'agent-blend.csv');
    await blend.locator('[data-f="weight"]').fill('3500'); await blend.getByText('输入已更改，请重新生成配料方案。',{exact:true}).waitFor();
    assert.equal(await blend.getByRole('button',{name:'导出 CSV',exact:true}).count(),0);
    ok('blending sample computes, exports, and invalidates stale output after edits');
    await openAgent('molecular'); const molecular=frame('molecular');
    await molecular.getByRole('button',{name:'计算 GPC 分子量',exact:true}).click({timeout:30000});
    await molecular.getByText('55,000.00',{exact:true}).waitFor({timeout:15000});
    await molecular.getByRole('combobox').first().click(); await molecular.getByText('复配Mw计算',{exact:true}).click();
    await molecular.getByRole('heading',{name:'复配结果',exact:true}).waitFor(); ok('molecular GPC example and blending-mode switch work');
    await openAgent('energy'); const mvr=frame('mvr');
    await mvr.getByText('MAE',{exact:true}).waitFor({timeout:30000});
    await mvr.getByRole('combobox').click(); await mvr.getByText('Linear Regression',{exact:true}).click();
    await mvr.getByText('数据或模型已修改。请点击“训练模型”，生成与当前选择对应的预测结果。',{exact:true}).waitFor();
    await mvr.getByRole('button',{name:'训练模型',exact:true}).click();
    await mvr.getByText('Linear Regression 预测效果',{exact:true}).waitFor({timeout:20000});
    await mvr.getByText('数据认知与模型说明',{exact:true}).click(); await mvr.getByRole('heading',{name:'2. 数据认知驾驶舱'}).waitFor();
    ok('energy workflow retrains changed model and opens data explanation');
    await openAgent('decision'); assert.equal(await solver.getByText('目标值 2360',{exact:true}).count(),1); ok('Agent navigation preserves previous calculation');
    await active().locator('[data-toggle-guide="decision"]').click(); assert(await active().locator('.step-rail').isHidden());
    await active().locator('[data-toggle-guide="decision"]').click(); assert(await active().locator('.step-rail').isVisible());
    await active().locator('[data-focus-agent="decision"]').click(); assert((await page.locator('.frame-focus iframe').boundingBox()).width>=1430);
    await page.locator('.focus-exit').click(); assert.equal(await page.locator('.frame-focus').count(),0); ok('guide collapse and full-size workspace restore correctly');
    await go('wall'); await page.locator('.wall-cell iframe').last().waitFor(); assert.equal(await page.locator('.wall-cell iframe').count(),4);
    const wallBot=page.frameLocator('.wall-cell[data-cell="1"] .frame-shell[data-service="bot"] iframe');
    await wallBot.locator('[data-example="missing"]').click(); const inputBefore=await wallBot.locator('textarea').inputValue();
    await page.locator('[data-wall-select="1"]').selectOption('pdf'); await page.locator('[data-wall-select="1"]').selectOption('bot');
    assert.equal(await wallBot.locator('textarea').inputValue(),inputBefore);
    await page.locator('[data-wall-open="1"]').click(); await wallBot.locator('#sendBtn').click(); await wallBot.getByText('字段校验失败',{exact:false}).waitFor();
    await page.locator('.focus-exit').click(); ok('4 simultaneous systems allow input and preserve per-window state when switching');
    await page.locator('#expand-wall').click(); await page.locator('.wall-cell[data-cell="1"] .wall-guide').click();
    await page.waitForFunction(()=>!document.querySelector('#wall-container').classList.contains('wall-expanded')); ok('leaving expanded multi-screen view clears its overlay');
    await go('wall'); await page.screenshot({path:output('agent-wall-final.png'),fullPage:true});
    await go('materials'); await active().locator('.material-row').last().waitFor(); assert.equal(await active().locator('.material-row').count(),2);
    assert.equal((await fetch(url+'/materials/anbicheng/Purchase%20Order_2030792.pdf')).status,404);
    assert.equal((await fetch(url+'/materials/energy-sample.csv')).status,200); ok('only curated samples are downloadable; original client attachment path is denied');
    await page.route('**/api/status',r=>r.abort()); await page.waitForTimeout(4600); await page.locator('#connection-notice').waitFor();
    await page.unroute('**/api/status'); await page.locator('#retry-connection').click(); await page.locator('#connection-notice').waitFor({state:'hidden'}); ok('connection failure is visible and retry restores service state');
    await go('unknown-route'); await page.waitForURL('**/#overview'); ok('invalid deep link recovers to overview');
    await page.screenshot({path:output('agent-overview-final.png'),fullPage:true});
    await go('agent/document'); await page.screenshot({path:output('agent-guide-final.png'),fullPage:true});
    await go('work/document'); await page.screenshot({path:output('agent-work-final.png'),fullPage:true});
    for(const width of [800,390]) {
      await page.setViewportSize({width,height:1000});
      for(const hash of ['overview','agent/document','work/document','wall']) {
        await go(hash); const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1); assert(!overflow,`horizontal overflow ${width} ${hash}`);
      }
      await go('work/document'); await active().locator('.project-frames').scrollIntoViewIfNeeded(); await page.waitForTimeout(400);
      await page.screenshot({path:output(`agent-responsive-${width}.png`),fullPage:true});
    }
    ok('800px tablet and 390px phone layouts retain named navigation and avoid page overflow');
    // Failed sample loading must not prevent entering any Agent.
    const fallback=await ctx.newPage(); await fallback.route('**/api/materials',r=>r.fulfill({status:503,body:'unavailable'}));
    await fallback.goto(url+'/#materials'); await fallback.getByRole('button',{name:'重试加载'}).waitFor();
    await fallback.locator('[data-go="agent/decision"]').click(); await fallback.getByRole('button',{name:'进入操作工作台',exact:false}).waitFor();
    await fallback.close(); ok('sample-library failure does not block Agent access');
    assert.equal(errors.length,0,errors.join('\n')); ok('no uncaught browser errors or unexpected dialogs');
    fs.writeFileSync(output('agent-verification.json'),JSON.stringify({checked_at:new Date().toISOString(),url,passed:tests},null,2));
  } catch(error) { await page.screenshot({path:output('agent-test-failure.png'),fullPage:true}).catch(()=>{}); throw error; }
  finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1)});
