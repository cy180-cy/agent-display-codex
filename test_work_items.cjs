'use strict';
const assert=require('node:assert/strict');
const {derive,workItems,selectWork,modelIndex}=require('./web/forge-live.js');
const thread={id:'sample-thread',title:'示例任务'};
function view(steps=[],status='active',plan=null){return derive({thread,connection:{ok:true},turns:[{id:'t1',status,steps,plan}]});}
const a={id:'read-a',category:'read',status:'complete',displayLabel:'检查 A 文档',start:1};
const b={id:'read-b',category:'read',status:'active',displayLabel:'检查 B 文档',start:2};
let n=0;function test(name,f){f();n++;console.log('PASS '+name);}
test('same-category calls remain separate work items',()=>{
 const items=workItems(view([a,b]));assert.equal(items.length,2);assert.equal(items[0].name,'检查 A 文档');assert.equal(items[1].name,'检查 B 文档');assert.equal(items[0].status,'complete');
});
test('new work never reopens an earlier completed item',()=>{
 const before=JSON.stringify(a);const items=workItems(view([a,b,{...b,id:'read-c'}]));assert.equal(items[0].status,'complete');assert.equal(JSON.stringify(a),before);
});
test('processing is independent of completed calls',()=>{
 const v=view([a]);const items=workItems(v);assert.equal(items[0].status,'complete');assert.equal(items[1].source,'processing');assert.equal(items[1].category,'plan');assert.deepEqual(v.focus.groups,['plan']);
});
test('parallel active items all survive the automatic window',()=>{
 const items=Array.from({length:60},(_,i)=>({id:String(i),order:i,status:i===2||i===7?'active':'complete'}));const picked=selectWork(items,6);assert.equal(picked.items.length,6);assert.ok(picked.items.some(x=>x.id==='2'));assert.ok(picked.items.some(x=>x.id==='7'));assert.equal(picked.pages,10);
});
test('history pages expose the full retained list',()=>{
 const items=Array.from({length:17},(_,i)=>({id:String(i),order:i,status:'complete'}));const all=[0,1,2].flatMap(page=>selectWork(items,6,page).items);assert.equal(new Set(all.map(x=>x.id)).size,17);
});
test('explicit plan takes priority over tool actions',()=>{
 const items=workItems(view([a,b],'active',{items:[{id:'p1',title:'审查资料',status:'completed'},{id:'p2',title:'验证结果',status:'in_progress'}]}));assert.equal(items.length,2);assert.equal(items[0].source,'plan');assert.equal(items[1].name,'验证结果');assert.equal(items[1].status,'active');
});
for(const status of ['completed','failed','interrupted','unknown'])test(status+' never promotes an unreturned tool to successful',()=>{
 const items=workItems(view([b],status));assert.equal(items[0].status,'unknown');assert.equal(items.filter(x=>x.source==='processing').length,0);
});
test('unknown plan progress is not fabricated by turn completion',()=>{
 const items=workItems(view([],'completed',{items:[{id:'p1',title:'未确认的工作',status:'in_progress'}]}));assert.equal(items[0].status,'unknown');
});
test('model selection uses knowledge-work semantics consistently',()=>{
 const jobs=[{form:'arm'},{form:'gear'},{form:'engine'}];assert.equal(modelIndex({turn:{id:'x'}},jobs),1);assert.equal(modelIndex({turn:{id:'different'}},jobs),1);
});
test('no turn produces no invented work items',()=>{assert.deepEqual(workItems({turn:null}),[]);});
console.log(n+' work-item checks passed');
