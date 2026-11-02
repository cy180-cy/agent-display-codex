'use strict';
// One shared connection; visual choreography cannot become recorded completion.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
let callback,fetches=0,errors=0;const delivered=[];
const ctx={module:{exports:{}},globalThis:null,AbortController,console,Set,Map,Date,document:{hidden:false,addEventListener(){}},
 setTimeout(){return 1;},clearTimeout(){},fetch:async()=>{fetches++;await new Promise(resolve=>{callback=resolve;});return {ok:true,json:async()=>({fixture:true})};}};
ctx.globalThis=ctx;
vm.createContext(ctx);
let source=fs.readFileSync(__dirname+'/web/forge-live.js','utf8');
source=source.replace('const api={mountForge,','const api={testSubscribe:subscribeStream,testRefresh:refreshStream,testStop:stopStream,mountForge,');
vm.runInContext(source,ctx);
const api=ctx.module.exports;
(async()=>{
 api.testSubscribe({hidden:false},'fixture-key',s=>delivered.push(['forge',s]),()=>errors++);
 api.testSubscribe({hidden:false},'fixture-key',s=>delivered.push(['works',s]),()=>errors++);
 assert.equal(fetches,1,'themes must coalesce in-flight reads');
 const pending=api.testRefresh();callback();await pending;
 assert.equal(delivered.length,2);assert.equal(delivered[0][1],delivered[1][1]);
 api.testStop();assert.equal(errors,2,'closing the collector must notify both connected themes');
 await api.testRefresh();assert.equal(fetches,1,'closed collector must not restart polling');
 assert.equal(typeof api.mountLoom,'function');
 // Execute the real build hook: simulated events must not enter real panels.
 const build=fs.readFileSync(__dirname+'/build_live.py','utf8');
 const hook=build.match(/WORKS_HOOK = r'''([\s\S]*?)'''/)[1];
 let fakeDeliveries=0,fakeCode=0;
 const noop=()=>{},c={window:{AgentDisplayLive:{mountLoom:()=>({updateState:noop,updateUI:noop,codeStep:noop,spiderState:noop})}}};
 for(const key of ['host','SR','ST','JOBS','AGENTS','SW','SLAB','CODE','PHASES','stepEls'])c[key]={};
 for(const key of ['stepIndex','stepProgress','updateState','updateUI','spiderState','codeStep','buildJobDom','buildStrands','syncCols','cPush','cNewLine','cFinish','setCur','renderHeat','renderDeliv','log','heartbeat','pushHist','codePhase','codeFill','codeSecStart','codeSecDone'])c[key]=noop;
 c.addDelivery=()=>fakeDeliveries++;c.codeFill=()=>fakeCode++;c.codePhase=()=>fakeCode++;c.DELIV=[];
 vm.createContext(c);vm.runInContext(hook,c);c.addDelivery({});c.codeFill();c.codePhase('deliver');
 assert.equal(fakeDeliveries,0,'decorative completion must not create a delivery');
 assert.equal(fakeCode,0,'decorative phases must not produce fake executed code');
 console.log('7 shared-theme and visual-isolation checks passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
