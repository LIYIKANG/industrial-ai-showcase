import test from 'node:test';
import assert from 'node:assert/strict';
import { CORE_QUESTIONS, DIM_WEIGHTS, PAINS } from '../web/engine/config.js';
import { activeQuestions,computeResult } from '../web/engine/scoring.js';
import { buildCards,buildConfidenceFlags,classifyIndustry } from '../web/engine/rules.js';
import { initialState,sanitizeState,flowSteps,stepReady,missingSteps,updatePains,toggleQuickWin,buildReport,demoState } from '../web/flow.js';

test('original questionnaire has 9 core questions, 4 scoring dimensions and selected follow-ups',()=>{
 assert.equal(CORE_QUESTIONS.length,9);assert.equal(Object.values(DIM_WEIGHTS).reduce((a,b)=>a+b),1);
 assert.equal(activeQuestions(['p_delivery','p_data']).length,11);
});
test('score endpoints, rounding and independent AI axis follow original engine',()=>{
 const s=demoState();for(const score of [0,1,2,3]){
  const answers=Object.fromEntries(activeQuestions(s.painIds).map(q=>[q.id,score]));
  const r=computeResult({industryId:'machinery',sizeId:'s2',painIds:s.painIds,quickWins:['qw_doc'],answers});
  assert.equal(r.totalScore,[0,33,67,100][score]);assert.equal(r.aiAction,[0,33,67,100][score]);assert.equal(r.radar.length,5);
 }
 const r=buildReport(s).result;assert.equal(r.totalScore,49);assert.deepEqual(r.dims,{D:67,S:44,M:44,O:33});
});
test('complete sample returns contextual cards; incomplete answers cannot create report',()=>{
 assert(missingSteps(initialState()).length>0);assert.throws(()=>buildReport(initialState()));
 const s=demoState();assert.equal(missingSteps(s).length,0);assert(buildReport(s).cards.length>0);
 delete s.answers.D1;assert.throws(()=>buildReport(s));assert(!stepReady(s,'D1'));
});
test('changing pain points drops stale follow-up answers and adds required new questions',()=>{
 const s=demoState();updatePains(s,['p_quality']);assert.equal(s.answers.f_delivery,undefined);assert.equal(s.answers.f_data,undefined);
 assert(missingSteps(s).includes('f_quality'));assert.equal(activeQuestions(s.painIds).length,10);
 updatePains(s,PAINS.map(p=>p.id));assert.equal(s.painIds.length,3);
});
test('unknown quick win excludes other choices in both directions',()=>{
 const s=demoState();toggleQuickWin(s,'qw_none');assert.deepEqual(s.quickWins,['qw_none']);
 toggleQuickWin(s,'qw_doc');assert.deepEqual(s.quickWins,['qw_doc']);toggleQuickWin(s,'qw_doc');assert.deepEqual(s.quickWins,[]);
});
test('tampered stored report cannot bypass required answers or inject invalid options',()=>{
 const s=sanitizeState({version:1,step:'report',industryChoice:'bogus',painIds:['nope','p_data','p_data'],answers:{D1:99,O1:'2'},quickWins:['qw_doc','qw_none']});
 assert.equal(s.step,'industry');assert.equal(s.industryChoice,'');assert.deepEqual(s.answers,{});assert.deepEqual(s.painIds,['p_data']);assert.deepEqual(s.quickWins,['qw_none']);
 assert.equal(sanitizeState({version:2,step:'report'}).step,'intro');
});
test('backtracking makes the score change; review step count matches selected pain set',()=>{
 const s=demoState();const first=buildReport(s).result.totalScore;s.answers.D1=0;assert.notEqual(buildReport(s).result.totalScore,first);
 assert.equal(flowSteps(s).length,17);updatePains(s,['p_delivery']);assert.equal(flowSteps(s).length,16);
});
test('industry classification and contradictory-answer flagging remain functional',()=>{
 assert.equal(classifyIndustry('注塑模具加工').industryId,'machinery');assert.equal(classifyIndustry('unknown').industryId,'other');
 const s=demoState();s.answers.M2=3;s.answers.D2=0;const r=buildReport(s);assert(r.flags.length>0);
 assert.equal(buildCards(r.input,r.result)[0].id,r.cards[0].id);assert(buildConfidenceFlags(r.input,r.result).length>0);
});
