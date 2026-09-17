// Unit-level DOM double: exercise invalid inputs and delayed responses without a browser.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../projects/solver/frontend/static/app.js'), 'utf8')
  .replace('init().catch((error) => setTask("初始化失败", error.message, 100));', '');
function setup() {
  const nodes = new Map();
  const make = () => ({value: '', innerHTML: '', textContent: '', style: {}, disabled: false, checked: false, setAttribute(){}, before(n){nodes.set(n.id,n)}, addEventListener(){}});
  const node = id => { if (!nodes.has(id)) nodes.set(id,make()); return nodes.get(id); };
  const ctx = vm.createContext({document: {getElementById:node, querySelectorAll:()=>[], createElement:make}, console, URL});
  vm.runInContext(source,ctx);
  ctx.run = text => vm.runInContext(text,ctx);
  ctx.run("atpState.materials = [{id:'M'}];");
  node('atpHorizon').value='30'; node('promiseMaterial').value='M'; node('promiseDate').value='2026-09-30'; node('promiseQty').value='500';
  return {ctx,node};
}
test('invalid promise quantities remove the previous successful result', async()=>{
  const {ctx,node}=setup(); let calls=0; ctx.postJson=async()=>{calls++;};
  for(const invalid of ['-1','0','','Infinity']) {
    node('promiseSummary').innerHTML='可承诺500kg'; node('promiseQty').value=invalid;
    await ctx.solvePromise(); assert.equal(node('promiseSummary').innerHTML,'');
  }
  assert.equal(calls,0);
});
test('late promise response cannot restore a result after an input change',async()=>{
  const {ctx,node}=setup(); let finish; ctx.postJson=()=>new Promise(r=>finish=r);
  const running=ctx.solvePromise(); ctx.invalidateBusiness('promise');
  finish({can_promise:true}); await running;
  assert.equal(node('promiseSummary').innerHTML,'');
  assert.match(node('promiseFeedback').textContent,/重新计算/);
});
test('batch rejects the whole submission and reports the invalid row',async()=>{
  const {ctx,node}=setup(); let calls=0; ctx.postJson=async()=>{calls++;};
  node('kitBatchInput').value='MAT007,500\nMAT008,abc';
  node('kitBatchResult').innerHTML='old result'; await ctx.runKitBatch();
  assert.equal(calls,0); assert.equal(node('kitBatchResult').innerHTML,'');
  assert.match(node('batchFeedback').textContent,/第 2 行/);
});
test('valid batch preserves every row and accepts Chinese commas',async()=>{
  const {ctx,node}=setup(); let submitted; ctx.postJson=async(url,p)=>{submitted=p;return {summary:{},items:[]}};
  node('kitBatchInput').value='MAT007，500\nMAT008,300'; await ctx.runKitBatch();
  assert.equal(submitted.items.length,2); assert.equal(submitted.items[1].quantity,300);
});
test('invalid ATP horizon clears result and prevents calculation',async()=>{
  const {ctx,node}=setup(); let calls=0; ctx.postJson=async()=>{calls++;};
  ctx.run('atpState.result={old:true};'); node('atpHorizon').value='0';
  await ctx.computeAtp(); assert.equal(calls,0); assert.equal(ctx.run('atpState.result'),null);
  assert.equal(node('atpSaveSnapshot').disabled,true);
});
test('exports apply the visible edited model before serializing',async()=>{
  const {ctx}=setup(); let applied=false;
  ctx.run('modelEdited=true; currentOntology={old:true};');
  ctx.applyModelEditor=async()=>{applied=true;ctx.run('currentOntology={newModel:true}; modelEdited=false;');};
  let payload; ctx.fetch=async(url,p)=>{payload=JSON.parse(p.body);return {ok:false}};
  await assert.rejects(ctx.download('/api/export/json','test.json'));
  assert.equal(applied,true); assert.equal(payload.ontology.newModel,true);
});
test('loading ATP demo does not change the unrelated active project ID',async()=>{
  const {ctx}=setup(); ctx.run("currentProjectId='original';");
  ctx.getJson=async()=>({project_id:'demo',data:{materials:[{id:'M'}]}});
  ctx.postJson=async()=>({}); ctx.computeAtp=async()=>{};
  await ctx.loadAtpDemo(); assert.equal(ctx.run('currentProjectId'),'original');
});
