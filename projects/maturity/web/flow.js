import { CORE_QUESTIONS, INDUSTRIES, MAX_PAINS, PAINS, QUICK_WIN_QUESTION, SIZES } from './engine/config.js';
import { activeQuestions, computeResult } from './engine/scoring.js';
import { buildCards, buildConfidenceFlags, buildExpertText, classifyIndustry } from './engine/rules.js';

export const STORAGE_KEY = 'showcase:maturity:v1';
export const initialState = () => ({ version:1, step:'intro', industryChoice:'', industryFreeText:'', sizeId:'', painIds:[], answers:{}, quickWins:[], demo:false, reportAt:'' });
const validIds = (value, options) => Array.isArray(value) ? [...new Set(value.filter(id => options.some(o => o.id===id)))] : [];
export function flowSteps(state) { return ['industry','size','pains',...activeQuestions(state.painIds).map(q=>q.id),'quickwin','review','report']; }
export function sanitizeState(raw) {
  const s=initialState();
  if (!raw || typeof raw!=='object' || raw.version!==1) return s;
  s.industryChoice=INDUSTRIES.some(i=>i.id===raw.industryChoice)?raw.industryChoice:'';
  s.industryFreeText=typeof raw.industryFreeText==='string'?raw.industryFreeText.slice(0,80):'';
  s.sizeId=SIZES.some(o=>o.id===raw.sizeId)?raw.sizeId:'';
  s.painIds=validIds(raw.painIds,PAINS).slice(0,MAX_PAINS);
  s.quickWins=validIds(raw.quickWins,QUICK_WIN_QUESTION.options);
  if(s.quickWins.includes('qw_none')) s.quickWins=['qw_none'];
  for(const q of activeQuestions(s.painIds)) {
    const answer=raw.answers?.[q.id];
    if(Number.isInteger(answer)&&answer>=0&&answer<q.options.length) s.answers[q.id]=answer;
  }
  s.demo=raw.demo===true;
  s.reportAt=typeof raw.reportAt==='string'&&Number.isFinite(Date.parse(raw.reportAt))?raw.reportAt:'';
  s.step=flowSteps(s).includes(raw.step)?raw.step:'intro';
  if(['review','report'].includes(s.step) && missingSteps(s).length) s.step=missingSteps(s)[0];
  if(s.step==='report'&&!s.reportAt)s.reportAt=new Date().toISOString();
  return s;
}
export function stepReady(s, id=s.step) {
  if(id==='industry') return !!s.industryChoice;
  if(id==='size') return !!s.sizeId;
  if(id==='pains') return s.painIds.length>0 && s.painIds.length<=MAX_PAINS;
  if(id==='quickwin') return s.quickWins.length>0;
  const q=activeQuestions(s.painIds).find(q=>q.id===id);
  if(q)return Number.isInteger(s.answers[id]) && !!q.options[s.answers[id]];
  return true;
}
export function missingSteps(s) { return flowSteps(s).filter(id=>!['review','report'].includes(id)&&!stepReady(s,id)); }
export function updatePains(s, ids) {
  s.painIds=validIds(ids,PAINS).slice(0,MAX_PAINS);
  const active=new Set(activeQuestions(s.painIds).map(q=>q.id));
  s.answers=Object.fromEntries(Object.entries(s.answers).filter(([id])=>active.has(id)));
  s.reportAt='';
  return s;
}
export function toggleQuickWin(s,id) {
  if(!QUICK_WIN_QUESTION.options.some(o=>o.id===id))return s;
  s.quickWins=s.quickWins.includes(id)?s.quickWins.filter(x=>x!==id):id==='qw_none'?['qw_none']:[...s.quickWins.filter(x=>x!=='qw_none'),id];
  s.reportAt='';return s;
}
export function toInput(s) {
  return {industryId:s.industryChoice==='other'?classifyIndustry(s.industryFreeText).industryId:s.industryChoice,
    industryFreeText:s.industryChoice==='other'?s.industryFreeText:undefined,sizeId:s.sizeId,painIds:s.painIds,answers:s.answers,quickWins:s.quickWins};
}
export function buildReport(s) {
  if(missingSteps(s).length) throw new Error('请先完成所有必答项，再生成报告。');
  const input=toInput(s),result=computeResult(input),cards=buildCards(input,result),flags=buildConfidenceFlags(input,result);
  return {input,result,cards,flags,summary:buildExpertText(input,result,cards,flags),generatedAt:s.reportAt,demo:s.demo};
}
export function demoState() {
  const s=initialState();
  Object.assign(s,{step:'review',industryChoice:'machinery',sizeId:'s2',painIds:['p_delivery','p_data'],quickWins:['qw_doc','qw_report'],demo:true});
  s.answers={f_delivery:1,f_data:1,D1:2,D2:2,S1:2,S2:1,M1:2,M2:1,O1:2,O2:1,O3:0};
  return s;
}
