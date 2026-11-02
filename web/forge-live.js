/* Local read-only adapter; the author's CSS, models and draw routines are retained. */
(function(global){
'use strict';
const THREAD=typeof document!=='undefined'?(document.querySelector?.('meta[name="display-thread"]')?.content||'sample-thread'):'sample-thread';
const TITLE=typeof document!=='undefined'?(document.querySelector?.('meta[name="display-title"]')?.content||'示例任务'):'示例任务';
const GROUPS=[
 {id:'write',name:'修改文件',station:'code_smith'},
 {id:'read',name:'读取资料',station:'research'},
 {id:'plan',name:'任务编排',station:'builder'},
 {id:'check',name:'验证检查',station:'review'},
 {id:'command',name:'执行命令',station:'data_ops'},
 {id:'tool',name:'其他工具',station:'designer'},
 {id:'search',name:'搜索检索',station:'scout'},
 {id:'browser',name:'浏览器',station:'deploy'}
];
const DONE=new Set(['complete','returned']);
const STATUS={active:'执行中',complete:'完成',returned:'已返回',failed:'失败',unknown:'待确认',pending:'等待记录'};
const TURN_STATUS={active:'本轮执行中',completed:'本轮已完成',failed:'本轮失败',interrupted:'本轮已中断',unknown:'状态待确认'};
const numeric=v=>typeof v==='number'&&Number.isFinite(v)?v:null;
const fmt=v=>numeric(v)===null?'—':Math.round(v).toLocaleString('en-US');
const short=(v,n=80)=>String(v||'').replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g,'').slice(0,n);
const tool=s=>short(s?.tool||'执行记录').split('__').pop().replace(/^functions\./,'');
const group=s=>GROUPS.some(g=>g.id===s?.category)?s.category:'tool';
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const clock=t=>numeric(t)===null?'--:--:--':new Date(t*1000).toLocaleTimeString('zh-CN',{hour12:false});
const elapsed=t=>numeric(t)===null?'—':t>=60?Math.floor(t/60)+'分'+Math.floor(t%60)+'秒':Math.max(0,t).toFixed(1)+'s';
const tid=id=>'TURN-'+String(id||'WAIT').replace(/[^a-zA-Z0-9]/g,'').slice(-8).toUpperCase();
// The two connected themes share one bounded snapshot request and one polling clock.
const stream={listeners:new Set(),key:null,timer:0,request:null,pending:null,last:null,stopped:false};
async function refreshStream(){
 if(stream.stopped)return;
 if(stream.pending)return stream.pending;
 clearTimeout(stream.timer);
 stream.pending=(async()=>{
  stream.request=new AbortController();const timeout=setTimeout(()=>stream.request?.abort(),7000);
  try{const r=await fetch('/api/snapshot',{headers:{'X-Display-Key':stream.key},cache:'no-store',signal:stream.request.signal});if(!r.ok){const e=new Error('连接返回 '+r.status);e.authExpired=r.status===403;throw e;}const data=await r.json();stream.last=data;for(const s of stream.listeners)s.data(data);}
  catch(e){if(!stream.stopped)for(const s of stream.listeners)s.error(e);}
  finally{clearTimeout(timeout);stream.request=null;stream.pending=null;if(!stream.stopped)stream.timer=setTimeout(refreshStream,document.hidden||![...stream.listeners].some(x=>!x.host.hidden)?5000:1000);}
 })();return stream.pending;
}
function subscribeStream(host,key,data,error){
 stream.key=key;const listener={host,data,error};stream.listeners.add(listener);
 if(stream.last)data(stream.last);refreshStream();return ()=>stream.listeners.delete(listener);
}
function stopStream(){stream.stopped=true;clearTimeout(stream.timer);stream.request?.abort();const e=new Error('采集已关闭');e.closed=true;for(const s of stream.listeners)s.error(e);}
if(typeof document!=='undefined')document.addEventListener('visibilitychange',()=>{if(!document.hidden&&stream.listeners.size)refreshStream();});
function derive(snapshot,previous){
 if(!snapshot||snapshot.thread?.id!==THREAD)return {valid:false,connected:false,reason:'聊天标识不匹配'};
 const turns=Array.isArray(snapshot.turns)?snapshot.turns:[],turn=turns.at(-1)||null;
 const steps=(turn?.steps||[]).filter(s=>s&&s.id).slice(-240),leaf=steps.filter(s=>!s.batch),work=leaf.length?leaf:steps;
 const connected=snapshot.connection?.ok===true,running=connected&&turn?.status==='active';
 const active=work.filter(s=>s.status==='active'),batch=steps.filter(s=>s.batch&&s.status==='active'),last=work.at(-1)||steps.at(-1)||null;
 const focusSteps=running?(active.length?active:batch):[];
 let focused=[...new Set(focusSteps.map(group))];
 if(running&&!focused.length)focused=['plan'];
 if(!running)focused=[];
 const groups=GROUPS.map(g=>{const records=work.filter(s=>group(s)===g.id);return {...g,records,last:records.at(-1)||null,pending:records.filter(s=>s.status==='active').length,returned:records.filter(s=>DONE.has(s.status)).length,failed:records.filter(s=>s.status==='failed').length,focused:focused.includes(g.id)};});
 const usage=turn?.usage?.available?turn.usage:snapshot.usage?.available?snapshot.usage:null;
 const code=turn?(snapshot.codeEntries||[]).filter(e=>e.turnId===turn.id).slice(-64):[];
 const completed=turns.filter(t=>t.status==='completed');
 return {valid:true,connected,running,turn,turns,steps,work,active,last,groups,usage,code,
  focus:{turnId:turn?.id,groups:focused,kind:focusSteps.length?'tool':running?'processing':'idle'},
  returned:work.filter(s=>DONE.has(s.status)).length,failed:work.filter(s=>s.status==='failed').length,
  deliveries:completed.slice(-4).reverse(),completed:completed.length,now:snapshot.now,
  taskTitle:short(turn?.taskTitle||turn?.task||snapshot.taskTitle||snapshot.thread.title,22),
  task:short(turn?.task||snapshot.task||turn?.summary||'',6000),snapshot};
}
function slots(n){
 if(n===6)return [['read','search'],['write'],['command'],['check'],['plan'],['browser','tool']];
 if(n===7)return [['read'],['search'],['write'],['command'],['check'],['plan'],['browser','tool']];
 return ['read','search','write','command','check','plan','browser','tool'].slice(0,n).map(x=>[x]);
}

// A part binds to one recorded work item, never to a permanent tool category.
function workItems(view){
 if(!view?.turn)return [];
 const plan=view.turn.plan?.items;
 let items=Array.isArray(plan)&&plan.length?plan.map((p,i)=>({
  id:'plan:'+p.id,name:short(p.title,64),detail:'已记录计划项 · '+(p.status==='completed'?'完成':p.status==='in_progress'?'进行中':'待执行'),
  category:'plan',status:p.status==='completed'?'complete':p.status==='in_progress'?'active':'pending',
  source:'plan',order:i,start:view.turn.plan.at,owner:p.owner||null
 })):view.work.map((r,i)=>({id:r.id,name:short(r.displayLabel||r.label||tool(r),64),
  detail:tool(r)+' · '+clock(r.start),category:group(r),status:r.status,source:'tool',order:i,start:r.start,record:r}));
 // Terminal turn state does not prove that an unreturned tool succeeded.
 if(!view.running)items=items.map(x=>x.status==='active'?{...x,status:'unknown'}:x);
 if(view.running&&!view.active.length&&!items.some(x=>x.status==='active')){
  items.push({id:'processing:'+view.turn.id,name:'模型处理中',detail:'等待下一条公开执行记录',category:'plan',status:'active',source:'processing',order:items.length});
 }
 return items;
}
function selectWork(items,capacity,page=null){
 const pages=Math.max(1,Math.ceil(items.length/capacity));
 if(page!==null)return {items:items.slice(Math.max(0,Math.min(pages-1,page))*capacity,(Math.max(0,Math.min(pages-1,page))+1)*capacity),pages,page:Math.max(0,Math.min(pages-1,page)),hiddenActive:0};
 const active=items.filter(x=>x.status==='active'),chosen=active.slice(0,capacity);
 const remainder=items.some(x=>x.source==='plan')?[...items.filter(x=>x.status==='pending'),...items.filter(x=>x.status!=='pending').reverse()]:items.slice().reverse();
 for(const x of remainder)if(chosen.length<capacity&&!chosen.some(a=>a.id===x.id))chosen.push(x);
 chosen.sort((a,b)=>a.order-b.order);
 return {items:chosen,pages,page:null,hiddenActive:Math.max(0,active.length-capacity)};
}
function modelIndex(view,jobs){
 // Keep the author's knowledge-work model stable across chat turns.
 return Math.max(0,jobs.findIndex(j=>j.form==='gear'));
}

function mountForge(ctx){
 const {host,SR,ST,TEL,AG,DP,AGENTS,JOBS,CODE,OP}=ctx,$=id=>SR.getElementById(id);
 const key=document.querySelector('meta[name="display-key"]')?.content;
 if(!key||key==='DISPLAY_LOCAL_SESSION_KEY')return null;
 const state={view:null,focus:null,turnId:null,phase:'ingest',phaseTime:0,intro:false,seen:new Map(),seenOutput:new Map(),timer:0,request:null,stopped:false,lastApplied:0,latestCode:null,modal:null,feedback:'',overlayKind:'resources',sourceCount:0,liveCompletion:false,items:[],visible:[],page:null,batchTime:0,geometry:new Map(),drawClock:0};
 const text=(id,v)=>{const el=$(id);if(el&&el.textContent!==String(v))el.textContent=String(v);};
 const relabel=(sel,v)=>{const el=SR.querySelector(sel);if(el)el.textContent=v;};
 const name=id=>GROUPS.find(g=>g.id===id)?.name||'执行记录';
 const heading=(n,v)=>'<b>'+n+'</b> / '+esc(v);
 host.dataset.liveSource='knowledge';host.dataset.liveStatus='connecting';
 function setupLabels(){
  const activityStyle=document.createElement('style');activityStyle.textContent='@keyframes live-work-scan{from{transform:translateX(-100%)}to{transform:translateX(360%)}}.pc.hot .pc-bar{overflow:hidden}.pc.hot .pc-bar i{width:28%!important;animation:live-work-scan 1.8s linear infinite}.pl .h,.pl .k{overflow:hidden;min-width:0}.pl .h span,.pl .k>span:first-child{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}.pl .h em,.pl .k .p{flex-shrink:0}.pc-n{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}@media(prefers-reduced-motion:reduce){.pc.hot .pc-bar i{animation:none}}';SR.append(activityStyle);
  const sub=SR.querySelector('.sub2');
  if(sub){const c=$('clock');sub.replaceChildren(document.createTextNode((TITLE+" · 本机只读 · 真实记录 · ")));if(c)sub.append(c);}
  relabel('.sub','真实工作项 · 连续流光 · 装饰造型循环');
  const title=SR.querySelector('#colL > .ph > span:first-child');if(title)title.innerHTML=heading('01','工具活动阵列 · ARRAY');
  relabel('#stage .cap','已返回记录 · OBSERVED RETURNS');
  relabel('#op .op-cap span','用量更新 · TOKENS/RECORD');
  const check=OP.ck.parentElement;check.replaceChildren(document.createTextNode('检查状态'),OP.ck);
  [['tAbs','本轮输入 · INPUT TOKENS'],['tMass','缓存命中 · CACHE HIT'],['tForged','本轮输出 · OUTPUT TOKENS'],['tDel','本轮合计 · TOTAL TOKENS']].forEach(([id,label])=>{const el=$(id)?.previousElementSibling;if(el)el.textContent=label;});
  [['sbDel','保留完成轮次 '],['sbAbs','保留记录 '],['sbForged','已返回记录 '],['sbJob','当前轮次 ']].forEach(([id,label])=>{const el=$(id);if(el)el.parentElement.replaceChildren(document.createTextNode(label),el);});
  relabel('.mini .m-title i',("视界熔炉 · "+TITLE+"只读"));
  relabel('#deliv .ph > span:first-child','04 / 轮次结果 · TURN RESULTS');
  relabel('.mini .m-stats > div:nth-child(2) > span','记录返回');relabel('.mini .m-stats > div:nth-child(3) > span','保留完成轮次');
  const hint=SR.querySelector('.hint');if(hint)hint.title='零件对应计划项或实际执行动作；八条输入通道表示工具类别。';
  GROUPS.forEach((g,i)=>{
   AGENTS[i].id='CH_'+g.id;
   AGENTS[i].role=g.name;
   const head=AG[i].el.querySelector('.ag-h span:first-child');if(head)head.textContent=g.id;
   const role=AG[i].el.querySelector('.role');if(role)role.textContent=g.name;
   const labels=AG[i].el.querySelectorAll('.ag-r > span:not(.bar):not(.v)');
   if(labels[0])labels[0].textContent='记录';if(labels[1])labels[1].textContent='待回';
   const label=DP[i].bar.closest('.dpr')?.querySelector('.n');if(label)label.textContent=g.id;
   AG[i].load=0;AG[i].ctx=0;AG[i].hv=[];AG[i].queue=0;AG[i].lastBeat='尚无记录';
  });
  const live=SR.querySelector('.strip .live');
  if(live){live.textContent='连接中 · 只读';live.style.pointerEvents='auto';live.style.cursor='pointer';live.tabIndex=0;live.setAttribute('role','button');live.setAttribute('aria-label','连接与资源');live.title='连接与资源：清理历史缓存、关闭本地看板';live.addEventListener('click',()=>openModal('resources'));live.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();openModal('resources');}});}
  const panel=$('code')?.parentElement;
  if(panel){panel.title='点击查看已记录的真实代码片段和工具输出';panel.style.pointerEvents='auto';panel.addEventListener('click',()=>openModal('code'));}
 }

 function rebuildJob(view){
  const j=JOBS[ST.job],oldIds=state.visible.map(x=>x?.id||'');
  j.id=view?.turn?tid(view.turn.id):'WAITING';j.title=view?.taskTitle||("等待"+TITLE+"执行记录");
  state.items=workItems(view);
  const selected=selectWork(state.items,j.parts.length,state.page);
  state.visible=Array.from({length:j.parts.length},(_,i)=>selected.items[i]||null);state.selection=selected;
  const saved=new Map(oldIds.map((id,i)=>[id,{done:ST.done[i]||0,hot:ST.hot[i]||0,prog:ST.prog[i]||0}]));
  j.parts.forEach((p,i)=>{
   const item=state.visible[i],g=GROUPS.find(g=>g.id===item?.category)||GROUPS[2];
   p.n=item?short(item.name,28):'待分配零件';p.en=item?(item.source==='plan'?'PLAN':item.source==='processing'?'MODEL':'ACTION'):'STANDBY';
   p.task=item?item.detail:'等待新的真实工作项';p.ag=g.station;
  });
  const changed=oldIds.join('|')!==state.visible.map(x=>x?.id||'').join('|');
  ctx.buildJobDom();
  state.visible.forEach((item,i)=>{const prev=saved.get(item?.id);ST.done[i]=prev?.done||0;ST.hot[i]=prev?.hot||0;ST.prog[i]=prev?.prog||0;});
  if(changed&&!state.intro&&view?.running){state.batchTime=0;ST.pulse=0;}
  ctx.getDom().orbitLabels.forEach((el,i)=>el.replaceChildren(document.createElement('i'),document.createTextNode('CH'+String(i+1).padStart(2,'0')+' · '+GROUPS[i].id.toUpperCase())));
 }
 function setupWorkControls(){
  const meta=$('bayMeta');if(!meta)return;
  const controls=document.createElement('span');controls.style.cssText='display:inline-flex;align-items:center;gap:5px;margin-left:8px;pointer-events:auto';
  for(const [label,title,action] of [['‹','上一批',()=>{state.page=Math.max(0,(state.page??Math.max(0,state.selection.pages-1))-1);}],['›','下一批',()=>{state.page=Math.min(state.selection.pages-1,(state.page??Math.max(0,state.selection.pages-1))+1);}],['跟随','跟随当前执行',()=>{state.page=null;}]]){
   const b=document.createElement('button');b.type='button';b.className='tb';b.textContent=label;b.title=title;b.style.cssText='padding:2px 6px;font-size:10px;min-height:22px';b.addEventListener('click',()=>{action();rebuildJob(state.view);updateUI(0);});controls.append(b);
  }
  meta.after(controls);
  const select=document.createElement('select');select.setAttribute('aria-label','投影造型');select.title='装饰投影，可切换原版六种造型；不改变任务';
  select.style.cssText='max-width:108px;background:var(--bg);color:var(--fg);border:1px solid var(--line);font:10px var(--mono);padding:3px;pointer-events:auto';
  const auto=document.createElement('option');auto.value='auto';auto.textContent='造型自动循环';select.append(auto);
  JOBS.forEach((j,i)=>{const option=document.createElement('option');option.value=String(i);option.textContent=j.short;select.append(option);});select.value='auto';
  select.addEventListener('change',()=>{state.modelOverride=select.value==='auto'?null:Number(select.value);state.visualAge=0;ST.job=state.modelOverride??ST.job;state.page=null;state.visible=[];state.phase=state.view?.running?'form':state.phase;state.intro=!!state.view?.running;state.phaseTime=0;rebuildJob(state.view);updateUI(0);});
  controls.append(select);state.modelSelect=select;
 }
 function showCode(entries,initial){
  const list=initial?entries.slice(-3):entries,lines=[];
  for(const e of list){
   const code=String(e.code||'').slice(0,8192);
   if(code&&state.seen.get(e.id)!==code){
    lines.push('# '+clock(e.time)+' · '+short(e.title||e.tool,100)+' · '+(STATUS[e.status]||e.status||'已记录'));
    const split=code.split(/\r?\n/);lines.push(...(initial?split.slice(-28):split.slice(0,96)));state.seen.set(e.id,code);
   }
   const output=String(e.output||'').slice(0,2000);
   if(output&&state.seenOutput.get(e.id)!==output){lines.push('# 工具输出 · '+(STATUS[e.status]||e.status||'已返回'));lines.push(...output.split(/\r?\n/).slice(-8).map(s=>'# '+s));state.seenOutput.set(e.id,output);}
   state.latestCode=e;
  }
  if(state.seen.size>64){const keys=[...state.seen.keys()];keys.slice(0,keys.length-64).forEach(k=>{state.seen.delete(k);state.seenOutput.delete(k);});}
  if(!lines.length)return;
  if(initial){CODE.q=[];CODE.lines=[];CODE.cur=null;CODE.hlEl=null;$('code').replaceChildren();lines.slice(-CODE.MAX).forEach(line=>ctx.cFinish(ctx.cNewLine(line)));ctx.setCur(false);}
  else{lines.slice(-100).forEach(line=>ctx.cPush(line));if(CODE.q.length>128)CODE.q.splice(0,CODE.q.length-128);}
  $('code').dataset.source='recorded-tools';updateCodeTitle();
 }
 function updateCodeTitle(){
  const e=state.latestCode,head=$('code')?.parentElement.querySelector('.ph > span:first-child');
  if(head)head.innerHTML=heading('07','执行代码 · '+short(e?.language||'LOG',12).toUpperCase());
  text('smPh',e?(STATUS[e.status]||e.status||'已记录'):'等待记录');if($('smPh'))$('smPh').title=e?short(e.title||e.tool,120):'等待真实工具代码';
 }
 function apply(snapshot){
  const view=derive(snapshot,state.focus);if(!view.valid){disconnect(view.reason);return;}
  const initial=state.turnId===null,changed=(view.turn?.id||'')!==state.turnId,previous=state.view;
  if(!changed&&previous?.turn?.status==='completed'&&view.running){state.phase='ingest';state.phaseTime=0;state.intro=true;state.liveCompletion=false;}
  if(changed){state.visualAge=0;state.pendingModel=null;state.turnId=view.turn?.id||'';state.phaseTime=0;state.phase=view.running?'ingest':view.turn?.status==='completed'?'deliver':'ingest';state.intro=view.running||view.connected&&view.turn?.status==='unknown';state.liveCompletion=false;ST.job=state.modelOverride??modelIndex(view,JOBS);if(state.modelSelect)state.modelSelect.value=state.modelOverride==null?'auto':String(ST.job);state.page=null;state.visible=[];state.batchTime=0;ST.jobT0=ctx.getNow();ST.pulse=-1;ST.done.fill(0);ST.hot.fill(0);ST.prog.fill(0);ST.active=-1;ST.focus=-1;state.seen.clear();state.seenOutput.clear();state.latestCode=null;state.selectedCodeId=null;CODE.q=[];CODE.lines=[];CODE.cur=null;CODE.hlEl=null;$('code').replaceChildren();}
  if(previous?.turn?.status!=='completed'&&view.turn?.id===previous?.turn?.id&&view.turn?.status==='completed'){state.phase='assemble';state.phaseTime=0;state.intro=false;state.liveCompletion=true;ST.pulse=0;}
  // A terminal snapshot is authoritative even when a prior phase transition was missed.
  if(view.turn?.status==='completed'&&!state.liveCompletion){state.phase='deliver';state.intro=false;}
  state.view=view;state.focus=view.focus;state.lastApplied=Date.now();host.dataset.liveStatus=view.connected?(view.turn?.status||'waiting'):'disconnected';host.dataset.liveTurn=state.turnId;
  const count=view.turn?.observed||view.work.length;if(!changed)TEL.absBucket+=Math.max(0,count-state.sourceCount);state.sourceCount=count;
  const signature=JSON.stringify([state.turnId,view.taskTitle,view.work.map(s=>[s.id,s.status,s.category,s.displayLabel]),view.turn?.plan,view.focus.kind,view.connected]);
  if(signature!==state.jobSignature){state.jobSignature=signature;rebuildJob(view);}
  showCode(view.code,initial||changed);state.latestCode=view.code.at(-1)||state.latestCode;updateCodeTitle();updateUI(0);if(state.modal)renderModal();
 }
 function disconnect(reason){
  if(state.view){state.view={...state.view,connected:false,running:false,focus:{...state.view.focus,groups:[],kind:'idle'}};state.focus=state.view.focus;}
  host.dataset.liveStatus='disconnected';state.feedback=reason||'连接暂时中断，正在重连';updateUI(0);
 }
 async function sync(){
  if(state.stopped)return;
  if(!state.unsubscribe){state.unsubscribe=subscribeStream(host,key,snapshot=>{state.authExpired=false;state.feedback='';apply(snapshot);},e=>{state.authExpired=!!e.authExpired;if(e.closed)state.stopped=true;disconnect(e.closed?'采集已关闭':state.authExpired?'页面会话已失效，请刷新页面重新连接':'连接暂时中断，正在重连');});}
  else await refreshStream();
 }

 function updateState(dt){
  const v=state.view,running=!!v?.running,visualRunning=running||!!v?.connected&&v?.turn?.status==='unknown';state.phaseTime+=dt;state.batchTime+=dt;state.drawClock+=dt;
  state.visualAge=(state.visualAge||0)+dt;
  if(visualRunning&&!state.intro&&!state.liveCompletion&&state.phase==='build'&&state.modelOverride==null&&state.visualAge>=36){state.pendingModel=(ST.job+1)%JOBS.length;state.phase='dissolve';state.phaseTime=0;state.intro=true;state.visualAge=0;}
  if(v?.turn?.status==='completed'&&!state.liveCompletion){state.phase='deliver';state.intro=false;}
  if(visualRunning&&state.intro){
   const phases=['dissolve','ingest','collapse','form','split','build'],d=ctx.PDUR[state.phase]||1;
   if(state.phase!=='build'&&state.phaseTime>=d){if(state.phase==='dissolve'&&state.pendingModel!=null){ST.job=state.pendingModel;state.pendingModel=null;state.visible=[];rebuildJob(v);}state.phase=phases[phases.indexOf(state.phase)+1]||'build';state.phaseTime=0;}
   if(state.phase==='build')state.intro=false;
  }else if(state.liveCompletion){
   if(state.phase==='assemble'&&state.phaseTime>=ctx.PDUR.assemble){state.phase='deliver';state.phaseTime=0;ST.pulse=0;}
   if(state.phase==='deliver'&&state.phaseTime>=ctx.PDUR.deliver)state.liveCompletion=false;
  }
  const phase=state.phase;
  ST.ph=Math.max(0,ctx.PHASES.indexOf(phase));ST.t=Math.min((ctx.PDUR[phase]||1)*.9999,state.phaseTime);
  if(phase!=='build')ctx.originalState(0);
  else{
   ST.ring=1;ST.form=1;ST.explode=.2+.8*Math.min(1,state.batchTime/1.8);
   ST.energy=running?.95:.4;ST.warm=running?.6:0;ST.holeA=0;ST.whole=Math.max(0,ST.whole-dt*1.5);
  }
  if(!v?.turn){ST.holeA=1;ST.holeK=1;ST.ring=0;ST.form=0;ST.energy=.4;}
  ST.liveActiveParts=[];ST.liveActiveOwners=[];ST.active=-1;
  state.visible.forEach((item,i)=>{
   const active=running&&item?.status==='active',done=!!item&&DONE.has(item.status);
   // The visual interpolation never becomes a displayed completion percentage.
   ST.prog[i]=done?1:0;ST.done[i]+=((done?1:0)-ST.done[i])*Math.min(1,dt*3);
   ST.hot[i]+=((active?1:0)-ST.hot[i])*Math.min(1,dt*5);
   if(active){ST.liveActiveParts.push(i);ST.liveActiveOwners.push(ctx.AGI[JOBS[ST.job].parts[i].ag]);}
  });
  ST.active=ST.liveActiveParts[0]??-1;
  // Tool channels keep real concurrency even when a plan owns the visible part.
  if(running)for(const g of v.focus.groups){const i=GROUPS.findIndex(x=>x.id===g);if(!ST.liveActiveOwners.includes(i))ST.liveActiveOwners.push(i);}
  const f=ctx.form();
  if(!state.geometry.has(f))state.geometry.set(f,f.ex.map(e=>e.slice()));
  const base=state.geometry.get(f);
  f.ex.forEach((e,i)=>{const scale=1-.88*(ST.done[i]||0);for(let k=0;k<3;k++)e[k]=base[i][k]*scale;});
  if(!running&&v?.turn?.status==='completed'){ST.form=1;ST.explode=state.liveCompletion&&phase==='assemble'?1-Math.min(1,state.phaseTime/ctx.PDUR.assemble):0;ST.ring=1;ST.holeA=0;ST.whole=0;}
  if(!v?.connected){ST.active=-1;ST.liveActiveParts=[];ST.liveActiveOwners=[];ST.energy=.3;ST.warm=0;}
  if(ST.pulse>=0){ST.pulse+=dt/1.6;if(ST.pulse>=1)ST.pulse=-1;}
  if(ST.focus>=0){ST.focusT-=dt;if(ST.focusT<=0)ST.focus=-1;}
  host.dataset.livePhase=phase;host.dataset.liveFocus=v?.focus.groups.join(',')||'';host.dataset.liveParts=ST.liveActiveParts.join(',');host.dataset.liveItems=String(state.items.length);
 }
 function codeStep(dt){
  CODE.hot=!!state.view?.running;
  if(!CODE.q.length&&(!CODE.cur||CODE.cur.typed>=CODE.cur.len)){ctx.setCur(false);return;}
  ctx.originalCodeStep(dt);
 }
 function updateUI(dt){
  const v=state.view,connected=!!v?.connected,running=!!v?.running,t=v?.turn;
  const now=(v?.now||Date.now()/1000)+(Date.now()-state.lastApplied)/1000;
  const count=v?.work.length||0,returned=v?.returned||0,focused=v?.focus.groups||[],usage=v?.usage,total=usage?.total||{},lastUsage=usage?.last||{};
  text('nowTitle',(TITLE));$('nowTitle').removeAttribute('title');text('bjT',v?.taskTitle||("等待"+TITLE+"执行记录"));if(v?.task)$('bjT').title=v.task;
  const condition=state.stopped?'看板采集已关闭':!connected?(state.authExpired?'页面会话失效 · 请刷新页面':'连接中断 · 保留最后记录'):running?(v.focus.kind==='processing'?'模型处理中 · 等待下一条工具记录':'真实工具执行中'):TURN_STATUS[t?.status]||'等待新的任务';
  text('nowEye',condition);text('mCap',condition+' · '+(v?.taskTitle||(TITLE)));
  text('nowSub',(t?.task?'已读取任务与公开执行事件':'未读到当前用户任务')+' · '+(v?.snapshot.connection.partialHistory?'部分历史':'已载入记录')+' · '+(v?.turn?.plan?.items?.length?'真实计划项':'真实执行动作')+'映射零件');
  text('bigN',String(returned).padStart(2,'0'));text('bigD',String(count).padStart(2,'0'));
  text('bjPct',returned+' / '+count+' 记录返回');$('bjBar').style.width=(count?returned/count*100:0)+'%';
  $('bjBar').title='已返回记录占当前已观测记录的比例，不是整体任务完成百分比';
  text('bayMeta',state.visible.filter(Boolean).length+' / '+state.items.length+' 工作项'+(state.page===null?' · 实时跟随':' · 第'+(state.page+1)+'批'));text('jobId',t?tid(t.id):'WAITING');
  text('bjId',(t?tid(t.id):'WAITING')+' · '+JOBS[ST.job].short);text('sbJob',t?tid(t.id):'—');text('mJob',t?tid(t.id):'—');
  text('sbDel',v?.completed||0);text('sbAbs',v?.steps.length||0);text('sbForged',returned);text('mProg',returned+' / '+count);text('mDel',v?.completed||0);
  const s=SR.querySelector('.strip .live');if(s){s.textContent=connected?(TITLE+" · 只读"):state.stopped?'采集已关闭':'连接中断';s.title=condition+'；点击查看连接与资源';}
  const system=SR.querySelector('.sb .on');if(system)system.textContent=connected?'LOCAL READ ONLY':'SOURCE OFFLINE';
  const topAg=$('sAg');if(topAg){topAg.textContent=focused.length+' / 8';if(topAg.parentElement.firstChild.nodeType===3)topAg.parentElement.firstChild.nodeValue='GROUPS';}
  text('agMeta','8 类工具 · '+focused.length+' 活跃');

  const dom=ctx.getDom();
  state.visible.forEach((item,i)=>{
   const hot=running&&item?.status==='active',all=!!item&&DONE.has(item.status),card=dom.cards[i];if(!card)return;
   const status=!item?'STANDBY':item.source==='processing'?'PROCESSING':({active:'RUNNING',complete:'COMPLETED',returned:'RETURNED',failed:'FAILED',pending:'QUEUED',unknown:'UNCONFIRMED'}[item.status]||'UNCONFIRMED');
   card.el.className='pc'+(state.visible.length%2===1&&i===state.visible.length-1?' wide':'')+(hot?' hot':all?' done':'')+(ST.focus===i?' focus':'');
   card.el.style.opacity=item?'1':'.28';card.el.title=item?item.name+'\n'+item.detail:'尚无工作项绑定此零件';
   card.s.textContent=status;card.pct.textContent=item?item.source==='processing'?'持续处理':all?(item.source==='plan'?'已完成':'已返回'):item.status==='active'?'进行中':item.source==='plan'&&item.status==='pending'?'待执行':STATUS[item.status]||'待确认':'—';
   card.bar.style.width=all?'100%':hot?'45%':'0%';card.bar.title=all?'记录确认完成':'活动指示，不代表完成百分比';
   const label=dom.labels[i];if(label){label.className='pl'+(hot?' hot':all?' done':'')+(ST.focus===i?' focus':'');label.style.visibility=item?'visible':'hidden';label.title=item?item.name+'\n'+item.detail:'';const pp=label.querySelector('.p');if(pp)pp.textContent=status;}
  });
  const max=Math.max(1,...(v?.groups||[]).map(g=>g.records.length));
  GROUPS.forEach((g,i)=>{
   const data=v?.groups[i],active=running&&focused.includes(g.id),records=data?.records||[],pending=data?.pending||0;
   AG[i].load=active?.9:.15;AG[i].queue=pending;AG[i].ctx=0;
   AG[i].el.className='ag'+(active?' on':pending?' busy':'');
   AG[i].bars[0].style.width=records.length/max*100+'%';AG[i].vals[0].textContent=String(records.length);
   AG[i].bars[1].style.width=Math.min(100,pending*25)+'%';AG[i].vals[1].textContent=String(pending);
   AG[i].port.style.opacity=active?'1':'.35';AG[i].st.textContent=active?(v.focus.kind==='processing'?'THINK':'RUN'):pending?'WAIT':'IDLE';
   AG[i].task.textContent=active&&v.focus.kind==='processing'?'模型处理中 · 等待下一条记录':data?.last?tool(data.last)+' · '+(STATUS[data.last.status]||'待确认'):'尚无该类记录';
   DP[i].bar.style.width=records.length/max*100+'%';DP[i].q.textContent=String(pending);
   const bins=Array.from({length:32},(_,b)=>records.filter(r=>r.start>=now-32+b&&r.start<now-31+b).length),peak=Math.max(1,...bins);
   AG[i].hist.forEach((el,b)=>el.style.height=Math.max(2,(bins[b]||0)/peak*100)+'%');
  });
  text('dispMeta','待返回 '+(v?.active.length||0));
  const input=numeric(total.input_tokens),output=numeric(total.output_tokens),tokens=numeric(total.total_tokens),hit=numeric(usage?.cacheHitRate);
  text('tAbs',fmt(input));text('tForged',fmt(output));text('tDel',fmt(tokens));text('tMass',hit===null?'—':(hit*100).toFixed(1)+'%');
  const scope=usage?.scope==='turn'?'本轮':usage?.scope==='session'?'会话累计':'未采集';
  const labelScope=usage?.scope==='session'?'会话':'本轮';
  [['tAbs',labelScope+'输入 · INPUT TOKENS'],['tForged',labelScope+'输出 · OUTPUT TOKENS'],['tDel',labelScope+'合计 · TOTAL TOKENS']].forEach(([id,label])=>{const el=$(id)?.previousElementSibling;if(el&&el.textContent!==label)el.textContent=label;});
  for(const id of ['tAbs','tMass','tForged','tDel'])$(id).title=scope+'；来源：'+(usage?.source||'无')+'；不代表真实账单金额';
  const latest=v?.active.at(-1)||v?.last,owner=v?.focus.kind==='processing'?GROUPS.find(g=>g.id===focused.at(-1)):latest?GROUPS.find(g=>g.id===group(latest)):null;
  OP.el.className='op'+(running?' hot':'');if(owner)OP.el.style.setProperty('--c',AGENTS[GROUPS.indexOf(owner)].c);else OP.el.style.removeProperty('--c');
  OP.code.textContent=v?.focus.kind==='processing'?'AI':latest?String((v.work.indexOf(latest)+1)||1).padStart(2,'0'):'--';
  OP.n.textContent=latest?(v?.focus.kind==='processing'?'MODEL PROCESSING':tool(latest)):'WAITING FOR RECORDS';
  OP.t.textContent=v?.focus.kind==='processing'?'上一工具已返回，等待下一条公开执行记录':latest?(STATUS[latest.status]||'待确认')+' · '+short(v?.taskTitle,28):condition;
  OP.ag.textContent=owner?.id.toUpperCase()||'—';OP.ow.textContent=owner?.id||'—';OP.ow.title='工具类别映射，不是已确认的实际执行者身份';
  OP.tok.textContent=fmt(tokens);OP.tok.title=scope+' Token（不是单条工具的独立用量）';
  OP.elp.textContent=t?.start?elapsed((t.end||now)-t.start):'—';
  const checks=(v?.work||[]).filter(x=>group(x)==='check'),lastCheck=checks.at(-1);OP.ck.textContent=lastCheck?(STATUS[lastCheck.status]||'待确认'):'未观测';
  const labels=['任务开始','工具请求','工具返回','验证检查','轮次结束'],flags=[!!t?.start,count>0,returned>0,checks.some(x=>DONE.has(x.status)),t?.status==='completed'];
  OP.steps.forEach((el,i)=>{el.textContent=labels[i];el.className=flags[i]?'done':running&&((i===2&&v.active.length)||(i===3&&lastCheck?.status==='active'))?'on':'';el.style.setProperty('--sp','0%');});
  const lastTokens=numeric(lastUsage.total_tokens);text('opRate',fmt(lastTokens));$('opRate').title='最近一条真实用量记录的 Token 数；日志没有连续采样，不能当作每秒速率';
  const wave=lastTokens===null?0:Math.min(1,lastTokens/Math.max(1,tokens||lastTokens));
  ctx.WAVE.forEach((el,i)=>{const t0=usage?.at||0,age=Math.floor(now-t0);el.style.height=(i===ctx.WAVE.length-1-Math.min(63,Math.max(0,age))?Math.max(3,wave*100):3)+'%';});
  ctx.stepEls.forEach((el,i)=>{const idx=t?.status==='completed'?4:state.phase==='build'?3:state.phase==='ingest'?0:['collapse','form'].includes(state.phase)?1:state.phase==='split'?2:state.phase==='dissolve'?0:4;el.className=i<idx?'done':i===idx?'on'+(i===3?' hot':''):'';el.querySelector('i').style.setProperty('--p',i<idx||t?.status==='completed'?1:0);el.title=t?.status==='completed'?'本轮已完成，交付已记录':'原版视觉阶段映射；实际执行状态以记录标签为准';});
  const inRate=(v?.work||[]).filter(s=>s.start>=now-10).length/10,outRate=(v?.work||[]).filter(s=>s.end>=now-10).length/10;
  text('sIn',inRate.toFixed(1));text('sOut',outRate.toFixed(1));$('sIn').title='最近10秒工具记录开始数/秒';$('sOut').title='最近10秒工具记录返回数/秒';
  renderEvents(v);renderDeliveries(v);updateCodeTitle();
 }
 function renderEvents(v){
  const rows=(v?.work||[]).map(s=>({time:s.end||s.start,category:group(s),verb:STATUS[s.status]||'待确认',message:tool(s),tail:s.exitCode==null?'':'exit '+s.exitCode}));
  (v?.turn?.messages||[]).forEach(m=>rows.push({time:m.time,category:'plan',verb:m.phase==='commentary'?'进度':'回复',message:short(m.text,180),tail:''}));
  rows.sort((a,b)=>(b.time||0)-(a.time||0));const visible=rows.slice(0,12),signature=JSON.stringify(visible);if(signature===state.logSignature)return;state.logSignature=signature;
  $('log').innerHTML=visible.map((e,i)=>{const ix=Math.max(0,GROUPS.findIndex(g=>g.id===e.category));return '<div class="lr'+(i===0?' fresh':'')+'" style="--c:'+AGENTS[ix].c+'"><span>'+esc(clock(e.time))+'</span><span class="a">'+esc(e.category)+'</span><span class="vb">'+esc(e.verb)+'</span><span class="x">'+esc(e.message)+(e.tail?' · '+esc(e.tail):'')+'</span></div>';}).join('');
 }
 function renderDeliveries(v){
  const rows=(v?.deliveries||[]).map(t=>({t:clock(t.end).slice(0,5),id:tid(t.id),title:short(t.taskTitle||t.task||t.summary||'已完成轮次',80),d:elapsed((t.end||0)-(t.start||0)),fresh:state.liveCompletion}));
  const signature=JSON.stringify(rows);if(signature!==state.deliverySignature){state.deliverySignature=signature;TEL.deliv=v?.completed||0;ctx.setDeliveries(rows);}
  text('delivMeta',(v?.completed||0)+' 保留完成轮次');
 }
 function closeModal(){if(!state.modal)return;state.modal.remove();state.modal=null;state.modalSignature='';SR.querySelector('.strip .live')?.focus();}
 function openModal(kind){
  state.overlayKind=kind;state.modalSignature='';
  if(!SR.querySelector('#adlive-style')){const style=document.createElement('style');style.id='adlive-style';style.textContent='.adlive-shade{position:fixed;inset:0;z-index:1000;background:#020409cc;display:flex;align-items:center;justify-content:center;pointer-events:auto;padding:20px}.adlive-dialog{width:min(920px,94vw);max-height:86vh;overflow:auto;background:var(--bg);padding:18px;color:var(--fg);font:12px/1.7 var(--mono)}.adlive-head{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:14px}.adlive-head strong{font:600 16px var(--cjk)}.adlive-body dl{display:grid;grid-template-columns:minmax(100px,1fr) minmax(120px,2fr);gap:8px 20px;margin:16px 0}.adlive-body dt{color:var(--fg-2)}.adlive-body dd{margin:0;overflow-wrap:anywhere}.adlive-actions{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0 8px}.adlive-body p{color:var(--fg-2);margin:10px 0}.adlive-body pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:48vh;overflow:auto;padding:12px;background:#080b10;border:1px solid var(--line);font:11px/1.65 var(--mono)}.adlive-body select{max-width:100%;width:100%;padding:8px;background:var(--bg);color:var(--fg);border:1px solid var(--line-2);font:inherit}.adlive-body output{display:block;color:var(--ice);margin-top:12px}.adlive-note{color:var(--fg-3)!important;font-size:11px}';SR.append(style);}
  if(!state.modal){
   const shade=document.createElement('div');shade.className='adlive-shade';shade.innerHTML='<section class="panel adlive-dialog" role="dialog" aria-modal="true" aria-label="连接与资源"><div class="adlive-head"><strong></strong><button type="button" class="tb" data-close>关闭</button></div><div class="adlive-body"></div></section>';
   shade.querySelector('[data-close]').addEventListener('click',closeModal);shade.addEventListener('click',e=>{if(e.target===shade)closeModal();});
   shade.addEventListener('keydown',e=>{e.stopPropagation();if(e.key==='Escape')closeModal();});SR.append(shade);state.modal=shade;
  }
  renderModal();state.modal.querySelector('[data-close]').focus();
 }
 function renderModal(){
  const shade=state.modal;if(!shade)return;
  const body=shade.querySelector('.adlive-body');
  shade.querySelector('strong').textContent=state.overlayKind==='code'?'真实执行代码与工具输出':'连接与资源';
  shade.querySelector('[role="dialog"]').setAttribute('aria-label',state.overlayKind==='code'?'真实执行代码与工具输出':'连接与资源');
  const v=state.view;
  if(state.overlayKind==='code'){
   const list=v?.code||[],signature=JSON.stringify([list.map(e=>[e.id,e.status,e.updatedAt]),state.selectedCodeId]);if(signature===state.modalSignature)return;state.modalSignature=signature;
   body.replaceChildren();const note=document.createElement('p');note.textContent='仅展示日志已记录的工具调用、命令和文件修改片段。秘密字段已隐藏；不读取模型内部推理，也不执行这里显示的代码。';body.append(note);
   const select=document.createElement('select');select.setAttribute('aria-label','选择真实代码记录');
   list.slice().reverse().forEach(e=>{const option=document.createElement('option');option.value=e.id;option.textContent=clock(e.time)+' · '+short(e.title||e.tool,90)+' · '+(STATUS[e.status]||e.status||'已记录');select.append(option);});
   if(!list.length){const p=document.createElement('p');p.textContent='当前轮次尚无可展示的工具代码。';body.append(p);return;}
   select.value=list.some(e=>e.id===state.selectedCodeId)?state.selectedCodeId:list.at(-1).id;
   const meta=document.createElement('p'),pre=document.createElement('pre'),output=document.createElement('pre');pre.setAttribute('aria-label','已记录代码');output.setAttribute('aria-label','工具输出');
   function choose(){state.selectedCodeId=select.value;const e=list.find(x=>x.id===select.value);if(!e)return;meta.textContent=[e.kind,e.language,e.source,e.truncated?'已截取片段':'',e.redacted?'已脱敏':''].filter(Boolean).join(' · ');pre.textContent=e.code||'没有代码正文';output.textContent=e.output||'尚无已记录的输出';}
   select.addEventListener('change',choose);body.append(select,meta,pre,output);choose();return;
  }
  const metrics=v?.snapshot.metrics||{},connection=v?.snapshot.connection||{},cache=v?.snapshot.codeCache||{};
  const pairs=[['版本','V2.1 · 熔炉与天工开物'],['零件来源',v?.turn?.plan?.items?.length?'已记录计划':'已记录工具动作'],['当前工作项',state.items.length+' 项 · 本屏 '+state.visible.filter(Boolean).length+' 项'],['连接目标',(TITLE+" · 仅此一个聊天")],['连接方式','本机只读 · 日志末尾读取与增量跟随'],['当前状态',state.stopped?'采集已关闭':v?.connected?(TURN_STATUS[v?.turn?.status]||'等待任务'):'连接中断'],['最后读取',clock(connection.lastCheck)],['采集器常驻内存',fmt(metrics.workingSetMiB)+' MB'],['代码缓存',fmt(cache.bytes??cache.totalBytes)+' bytes · '+(v?.code.length||0)+' 条当前轮次记录'],['内存中保留',fmt(connection.retainedSteps)+' / '+fmt(connection.maxRetainedSteps)+' 步'],['同步间隔','前台 1 秒 / 后台 5 秒 · 两主题共用连接'],['真实用量来源',v?.usage?v.usage.source+' · '+v.usage.scope:'尚未读到 Token 记录'],['费用 / 质量评分','未提供真实来源，未填模拟数值'],['主题接入','06 视界熔炉、07 天工开物 · 其余八款原版演示']];
  body.innerHTML='<p><b>当前用户任务：</b>'+esc(v?.task||'当前读取范围内没有用户请求正文')+'</p><p>视界熔炉和天工开物连接真实记录。几何模型、通道和光束为视觉映射，通道数量不表示实际 Agent 数量。零件对应真实计划项或执行动作；模型处理中使用独立状态零件，不改写已完成工具。</p><dl>'+pairs.map(([a,b])=>'<dt>'+esc(a)+'</dt><dd>'+esc(b)+'</dd>').join('')+'</dl><div class="adlive-actions"><button type="button" class="tb" data-clear>清理历史缓存</button><button type="button" class="tb" data-stop>关闭看板采集</button></div><p class="adlive-note">清理只影响看板内存缓存，保留当前状态并继续跟随。关闭采集后，请再关闭网页以释放绘图资源。内存数字不含浏览器和显存。</p><output aria-live="polite"></output>';
  body.querySelector('output').textContent=state.notice||state.feedback||'';
  const clear=body.querySelector('[data-clear]'),stop=body.querySelector('[data-stop]');clear.disabled=stop.disabled=state.stopped;
  clear.addEventListener('click',async()=>{
   clear.disabled=true;
   try{const r=await fetch('/api/cache/clear',{method:'POST',headers:{'X-Display-Key':key}});if(!r.ok)throw Error();const result=await r.json();CODE.q=[];CODE.lines=[];CODE.cur=null;CODE.hlEl=null;$('code').replaceChildren();state.seen.clear();state.seenOutput.clear();state.notice='已清理看板历史缓存，源聊天记录未改动。';clearTimeout(state.timer);await sync();renderModal();}
   catch(e){state.notice='清理未完成，请检查连接后重试。';renderModal();}
  });
  stop.addEventListener('click',async()=>{
   stop.disabled=true;
   try{const r=await fetch('/api/shutdown',{method:'POST',headers:{'X-Display-Key':key}});if(!r.ok)throw Error();state.stopped=true;stopStream();disconnect('采集已关闭');state.notice='本地采集服务已关闭；关闭这个网页后，绘图也会停止。';renderModal();}
   catch(e){state.notice='关闭未完成，可在启动本服务的 PowerShell 窗口按 Ctrl+C。';renderModal();}
  });
 }
 function start(){
  TEL.abs=0;TEL.absBucket=0;TEL.forged=0;TEL.deliv=0;TEL.mass=0;TEL.absRate=[];
  setupLabels();ST.job=4;setupWorkControls();rebuildJob(null);ctx.setDeliveries([]);updateUI(0);sync();
 }
 document.addEventListener('visibilitychange',()=>{if(!state.stopped&&!document.hidden){clearTimeout(state.timer);sync();}});
 return {start,updateState,updateUI,codeStep};
}
function mountLoom(ctx){
 const {host,SR,ST,JOBS,AGENTS,SW,SLAB,CODE}=ctx,$=id=>SR.getElementById(id);
 const key=document.querySelector('meta[name="display-key"]')?.content;if(!key||key==='DISPLAY_LOCAL_SESSION_KEY')return null;
 const state={view:null,focus:null,items:[],visible:[],turnId:null,seen:new Map(),seenOutput:new Map(),page:null,model:-1,modal:null,modalKind:'resources',stopped:false,selectedCode:null};
 const text=(id,value)=>{const el=$(id);if(el&&el.textContent!==String(value))el.textContent=String(value);};
 const relabel=(sel,value)=>{const el=SR.querySelector(sel);if(el)el.textContent=value;};
 const statusOf=v=>state.stopped?'采集已关闭':!v?("正在连接"+TITLE):!v.connected?'连接中断 · 正在重连':v.running?(v.focus.kind==='processing'?'模型处理中 · 等待公开记录':'真实工具执行中'):TURN_STATUS[v.turn?.status]||'等待新任务';
 function bind(){
  const j=JOBS[ST.job],oldModel=state.model;
  state.items=workItems(state.view);const selection=selectWork(state.items,j.secs.length,state.page);state.pages=selection.pages;
  const chosen=Array.from({length:j.secs.length},(_,i)=>selection.items[i]||null);
  const signature=JSON.stringify([ST.job,chosen.map(x=>[x?.id,x?.name,x?.detail])]);state.visible=chosen;
  if(state.bindSignature!==signature){
   state.bindSignature=signature;
   j.id=state.view?.turn?tid(state.view.turn.id):'WAITING';j.title=(TITLE+" · 装饰织造投影");
   j.secs.forEach((s,i)=>{const item=chosen[i];s.n=item?short(item.name,22):'待分配织位';s.en=item?(item.source==='plan'?'PLAN':item.source==='processing'?'MODEL':'ACTION'):'STANDBY';s.task=item?short(item.detail,110):'等待真实工作项';});
   ctx.buildJobDom();ctx.buildStrands();ctx.syncCols();state.model=ST.job;
  }
  if(oldModel!==ST.job)state.visualChanged=true;
 }
 function feedCode(entries,reset){
  if(reset){CODE.q=[];CODE.lines=[];CODE.cur=null;CODE.hlEl=null;$('code').replaceChildren();state.seen.clear();state.seenOutput.clear();}
  const lines=[];
  for(const e of entries){
   if(e.code&&state.seen.get(e.id)!==e.code){lines.push('# '+clock(e.time)+' · '+short(e.title||e.tool,90));lines.push(...String(e.code).split(/\r?\n/).slice(-64));state.seen.set(e.id,e.code);}
   if(e.output&&state.seenOutput.get(e.id)!==e.output){lines.push('# 工具输出 · '+(STATUS[e.status]||e.status));lines.push(...String(e.output).split(/\r?\n/).slice(-6).map(x=>'# '+x));state.seenOutput.set(e.id,e.output);}
  }
  for(const map of [state.seen,state.seenOutput])while(map.size>64)map.delete(map.keys().next().value);
  if(reset)lines.slice(-CODE.MAX).forEach(line=>ctx.cFinish(ctx.cNewLine(line)));
  else{lines.slice(-96).forEach(line=>ctx.cPush(line));if(CODE.q.length>128)CODE.q.splice(0,CODE.q.length-128);}
  $('code').dataset.source='recorded-tools';ctx.setCur(false);
 }
 function apply(snapshot){
  const v=derive(snapshot,state.focus);if(!v.valid){disconnect(new Error(v.reason));return;}
  const changed=state.turnId!==v.turn?.id;state.turnId=v.turn?.id;state.view=v;state.focus=v.focus;
  if(changed)state.page=null;
  bind();feedCode(v.code,changed);updateUI(0);if(state.modal)renderModal();
 }
 function disconnect(error){
  if(error.closed)state.stopped=true;
  if(state.view)state.view={...state.view,connected:false,running:false,focus:{groups:[],kind:'idle'}};
  state.error=error.authExpired?'会话已失效，请刷新页面':error.closed?'采集已关闭':'连接中断，正在重连';updateUI(0);
 }
 function start(){
  host.dataset.liveSource='knowledge';host.dataset.visualMode='decorative';
  const sub=SR.querySelector('.sub2'),clockEl=$('clock');sub.replaceChildren(document.createTextNode((TITLE+" · 本机只读 · 真实记录 + 造型演绎 · ")),clockEl);
  relabel('.sub','单智能体 · 多工具光丝 · 真实状态驱动重点高亮');
  relabel('.swarm .ph > span:first-child','01 / 工具通道 · TOOL CHANNELS');
  relabel('.codep .ph > span:first-child','05 / 真实执行代码 · RECORDED CODE');
  relabel('.proj .ph > span:first-child','02 / 真实工作项 · WORK ITEMS');
  relabel('.ev .ph > span:first-child','03 / 光丝演绎 · VISUAL FIELD');
  relabel('.deliv .ph > span:first-child','04 / 轮次结果 · TURN RESULTS');
  relabel('.core-sub > span','MAIN AGENT');relabel('.core-sub > b',(TITLE));relabel('.mini .m-title i',("天工开物 · "+TITLE+"只读"));
  relabel('.mini .m-stats > div:nth-child(2)>span','记录返回');relabel('.mini .m-stats > div:nth-child(3)>span','完成轮次');
  const metricLabels={cQ:'待返回动作',cHb:'距最近记录',cSw:'活跃工具通道',cTen:'缓存命中',mSilk:'输入 Token',mKnot:'输出 Token',mSec:'已返回记录',mEl:'本轮用时'};
  for(const [id,label] of Object.entries(metricLabels)){const el=$(id),parent=el?.parentElement;if(!parent)continue;const span=parent.querySelector('span');if(span)span.textContent=label;else if(parent.firstChild?.nodeType===3)parent.firstChild.nodeValue=label;}
  [['sSp','GROUPS '],['sSilk','INPUT '],['sKnot','OUTPUT '],['sbDel','保留完成轮次 '],['sbSilk','输入 Token '],['sbKnot','已返回记录 '],['sbJob','当前轮次 ']].forEach(([id,label])=>{const el=$(id);if(el)el.parentElement.replaceChildren(document.createTextNode(label),el);});
  SW.forEach((g,i)=>{g.el.querySelector('.nm').textContent=GROUPS[i].id.toUpperCase();g.el.querySelector('.role').textContent=GROUPS[i].name;SLAB[i].el.querySelector('.h span').textContent='CH_'+GROUPS[i].id.toUpperCase();});
  const source=SR.querySelector('.strip .live');source.tabIndex=0;source.setAttribute('role','button');source.setAttribute('aria-label','连接与资源');source.style.cssText='pointer-events:auto;cursor:pointer';source.addEventListener('click',()=>openModal('resources'));source.addEventListener('keydown',e=>{if(e.key==='Enter')openModal('resources');});
  const note=document.createElement('div');note.textContent='造型、光丝与织造阶段为视觉演绎；真实任务状态以右侧记录为准。';note.style.cssText='position:absolute;bottom:8px;left:8px;color:var(--muted,#95a8bb);font:10px var(--mono);pointer-events:none;z-index:6';$('mid').append(note);
  const controls=document.createElement('span');controls.style.cssText='display:inline-flex;gap:5px;pointer-events:auto';
  for(const [name,label,action] of [['‹','上一批工作项',()=>{state.page=Math.max(0,(state.page??Math.max(0,state.pages-1))-1);}],['›','下一批工作项',()=>{state.page=Math.min(state.pages-1,(state.page??0)+1);}],['跟随','跟随当前工作项',()=>{state.page=null;}]]){const b=document.createElement('button');b.className='tb';b.type='button';b.textContent=name;b.setAttribute('aria-label',label);b.style.cssText='font-size:10px;padding:2px 6px';b.addEventListener('click',()=>{action();bind();updateUI(0);});controls.append(b);}
  $('pjMeta').after(controls);
  $('code').parentElement.style.pointerEvents='auto';$('code').parentElement.title='点击查看真实代码与工具输出';$('code').parentElement.addEventListener('click',()=>openModal('code'));
  const style=document.createElement('style');style.textContent='.lc-t,.sr .n b,.sw-t,.slab .t{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.sr .n{min-width:0}.pm{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.pm>div{min-width:0}.pm b{font-size:14px;overflow-wrap:anywhere}.live-shade{position:fixed;inset:0;z-index:10000;background:#020409cc;display:flex;align-items:center;justify-content:center;pointer-events:auto;padding:20px}.live-dialog{width:min(900px,94vw);max-height:86vh;overflow:auto;background:#0b1018;color:#dce6f0;border:1px solid #344255;padding:20px;font:13px/1.7 var(--mono)}.live-dialog header{display:flex;align-items:center;justify-content:space-between;gap:12px}.live-dialog dl{display:grid;grid-template-columns:160px 1fr;gap:8px}.live-dialog dd{margin:0}.live-dialog pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:40vh;overflow:auto;background:#05080d;padding:12px}.live-dialog select{width:100%;background:#101620;color:#dce6f0;padding:8px}.live-dialog button{cursor:pointer}.live-actions{display:flex;gap:10px;margin:14px 0}.live-dialog p{color:#a7b8c9}';SR.append(style);
  ctx.setDeliveries([]);bind();updateUI(0);
  subscribeStream(host,key,apply,disconnect);
 }
 function updateState(dt){
  // Original choreography is a visual clock. It never completes a real work item.
  ctx.originalState(dt);
  if(state.model!==ST.job)bind();
  host.dataset.visualPhase=ctx.PHASES[ST.ph];host.dataset.visualModel=String(ST.job);
 }
 function spiderState(index){
  const visual=ctx.originalSpider(index),v=state.view,hot=v?.running&&v.focus.groups.includes(GROUPS[index].id);
  return {...visual,act:hot?1:visual.act};
 }
 function updateUI(){
  const v=state.view,t=v?.turn,now=Date.now()/1000,condition=statusOf(v),groups=v?.groups||[],items=state.visible,dom=ctx.getDom(),total=v?.usage?.total||{},hit=numeric(v?.usage?.cacheHitRate),j=JOBS[ST.job];
  host.dataset.liveStatus=state.stopped?'stopped':!v?.connected?'disconnected':t?.status||'waiting';host.dataset.liveTurn=t?.id||'';host.dataset.liveItems=String(state.items.length);host.dataset.liveActive=String(items.filter(x=>v?.running&&x?.status==='active').length);
  text('coreCap','FX · '+ctx.PHASES[ST.ph].toUpperCase());text('coreSt',condition);text('pjT',(TITLE+" · ")+condition);text('pjId',(t?tid(t.id):'WAITING')+' · '+j.kindName+' · 装饰投影');text('pjMeta',items.filter(Boolean).length+' / '+state.items.length+' 项');
  text('pjPct',(v?.returned||0)+' / '+(v?.work.length||0)+' 记录返回');$('pjBar').style.width=(v?.work.length?v.returned/v.work.length*100:0)+'%';$('pjBar').title='已观测工具记录返回比例，不是整体任务完成百分比';
  text('mSilk',fmt(total.input_tokens));text('mKnot',fmt(total.output_tokens));text('mSec',(v?.returned||0)+' / '+(v?.work.length||0));text('mEl',t?.start?elapsed((t.end||now)-t.start):'—');
  const scope=v?.usage?.scope==='session'?'会话累计':v?.usage?.scope==='turn'?'本轮':'未采集';
  for(const [id,label] of [['mSilk',scope+'输入 Token'],['mKnot',scope+'输出 Token']]){const el=$(id);el.parentElement.querySelector('span').textContent=label;el.title=scope+'；来源：'+(v?.usage?.source||'无')+'；不是当前工具的独立用量';}
  text('cQ',v?.active.length||0);const last=v?.snapshot.connection?.lastRecord;text('cHb',last?Math.max(0,Math.floor(now-last))+'s':'—');text('cSw',(v?.focus.groups.length||0)+' / 8');text('cTen',hit===null?'—':(hit*100).toFixed(1)+'%');
  text('sSp',(v?.focus.groups.length||0)+'/8');text('sSilk',fmt(total.input_tokens));text('sKnot',fmt(total.output_tokens));text('sbSilk',fmt(total.input_tokens));text('sbKnot',v?.returned||0);text('sbDel',v?.completed||0);text('sbJob',t?tid(t.id):'—');
  text('swMeta',(v?.focus.groups.length||0)+' 活跃 · 8 工具通道');const max=Math.max(1,...groups.map(g=>g.records.length));
  SW.forEach((g,i)=>{const data=groups[i],hot=v?.running&&v.focus.groups.includes(GROUPS[i].id),records=data?.records||[],last=(hot?records.find(r=>r.status==='active'):null)||data?.last;g.el.className='sw'+(hot?' on':'');g.t.textContent=hot&&v.focus.kind==='processing'?'模型处理中 · 等待公开记录':last?short(last.displayLabel||tool(last),110)+' · '+(STATUS[last.status]||'待确认'):'尚无该类工具记录';g.tag.textContent=hot?(v.focus.kind==='processing'?'处理 MODEL':'执行 RUN'):'待命 IDLE';g.bar.style.width=records.length/max*100+'%';g.v.textContent=String(records.length);g.el.title='记录数，不是负载或速度';
   const sl=SLAB[i];sl.el.className='slab '+sl.side+(hot?' on':'');sl.t.textContent=GROUPS[i].name+' · '+(hot?'当前活动':'工具通道');sl.k.textContent=(data?.pending||0)+' 待返回 / '+records.length+' 记录';
   for(let k=0;k<g.hist.length;k++){const count=records.filter(r=>r.start>=now-24+k&&r.start<now-23+k).length;g.hist[k].style.height=Math.min(100,Math.max(3,count*25))+'%';}
  });
  items.forEach((item,i)=>{const hot=!!v?.running&&item?.status==='active',done=!!item&&DONE.has(item.status),label=!item?'待分配':item.source==='processing'?'模型处理中':done?'已返回':STATUS[item.status]||'待确认';for(const card of [dom.cards[i],dom.secs[i]]){if(!card)continue;card.el.className=(card===dom.cards[i]?'lc':'sr')+(hot?' on':done?' done':'')+(ST.focus===i?' focus':'');card.el.style.opacity=item?'1':'.35';card.el.title=item?item.name+'\n'+item.detail:'此织位尚无真实工作项';const owner=card.el.querySelector('em');if(owner)owner.textContent=item?'工具通道 '+item.category:'未绑定工作项';card.p.textContent=label;card.bar.style.width=done?'100%':hot?'45%':'0%';card.bar.title='真实状态的视觉指示，不是预计完成百分比';}});
  ctx.stepEls.forEach((el,i)=>{const ix=ctx.stepIndex(ctx.PHASES[ST.ph]);el.className=i<ix?'done':i===ix?'on':'';el.querySelector('i').style.setProperty('--p',i<ix?1:i===ix?Math.min(1,ctx.stepProgress(ctx.PHASES[ST.ph])):0);el.title='装饰织造阶段；不表示真实任务已经进入此阶段';const sn=el.querySelector('.sn');if(sn&&!sn.textContent.startsWith('FX'))sn.textContent='FX '+sn.textContent;});
  const live=SR.querySelector('.strip .live');live.textContent=state.stopped?'采集已关闭':v?.connected?(TITLE+" · 只读"):'连接中';live.title=condition+' · 点击查看连接与资源';
  relabel('.sb .on',v?.connected?'LOCAL READ ONLY':'SOURCE OFFLINE');
  text('mCap',(TITLE+" · ")+condition+' · 造型演绎');text('mJob',t?tid(t.id):'—');text('mProg',(v?.returned||0)+' / '+(v?.work.length||0)+' 记录返回');text('mDel',v?.completed||0);
  const code=v?.code.at(-1);text('codeMeta',code?(STATUS[code.status]||'已记录')+' · '+short(code.language,12):'等待真实代码');
  const rows=(v?.work||[]).map(s=>({time:s.end||s.start,status:STATUS[s.status]||'待确认',label:short(s.displayLabel||tool(s),180),cat:group(s)}));(t?.messages||[]).forEach(m=>rows.push({time:m.time,status:m.phase==='commentary'?'进度':'回复',label:short(m.text,180),cat:'plan'}));rows.sort((a,b)=>(b.time||0)-(a.time||0));
  const signature=JSON.stringify(rows.slice(0,14));if(signature!==state.logSignature){state.logSignature=signature;if($('log'))$('log').innerHTML=rows.slice(0,14).map(r=>'<div class="lr"><span>'+esc(clock(r.time))+'</span><span class="a">'+esc(r.cat)+'</span><span class="x">'+esc(r.status+' · '+r.label)+'</span></div>').join('');}
  const deliveries=(v?.deliveries||[]).map(x=>({t:clock(x.end).slice(0,5),id:tid(x.id),title:'本轮回复已完成',d:elapsed((x.end||0)-(x.start||0))})),ds=JSON.stringify(deliveries);if(ds!==state.deliverySignature){state.deliverySignature=ds;ctx.setDeliveries(deliveries);}text('delivMeta',(v?.completed||0)+' 保留完成轮次');ctx.renderHeat();
 }
 function codeStep(dt){CODE.hot=!!state.view?.running;if(!CODE.q.length&&(!CODE.cur||CODE.cur.typed>=CODE.cur.len)){ctx.setCur(false);return;}ctx.originalCodeStep(dt);}
 function openModal(kind){
  state.modalKind=kind;
  if(!state.modal){const shade=document.createElement('div');shade.className='live-shade';shade.innerHTML='<section class="live-dialog" role="dialog" aria-modal="true"><header><strong></strong><button class="tb" type="button" data-close>关闭</button></header><div data-body></div></section>';shade.querySelector('[data-close]').addEventListener('click',closeModal);shade.addEventListener('click',e=>{if(e.target===shade)closeModal();});shade.addEventListener('keydown',e=>{e.stopPropagation();if(e.key==='Escape')closeModal();});SR.append(shade);state.modal=shade;}
  renderModal();state.modal.querySelector('[data-close]').focus();
 }
 function closeModal(){state.modal?.remove();state.modal=null;SR.querySelector('.strip .live')?.focus();}
 function renderModal(){
  if(!state.modal)return;const body=state.modal.querySelector('[data-body]'),v=state.view;
  state.modal.querySelector('strong').textContent=state.modalKind==='code'?'真实代码与工具输出':'连接与资源 · 天工开物';state.modal.querySelector('[role=dialog]').setAttribute('aria-label',state.modalKind==='code'?'真实代码与工具输出':'连接与资源');
  if(state.modalKind==='code'){
   const signature=JSON.stringify([v?.code.map(x=>[x.id,x.updatedAt]),state.selectedCode]);if(state.modalSignature===signature)return;state.modalSignature=signature;body.replaceChildren();
   const entries=v?.code||[],note=document.createElement('p');note.textContent='只展示当前轮次已记录的真实代码和输出，不执行显示内容。';body.append(note);if(!entries.length){note.textContent='当前轮次尚无已记录代码。';return;}
   const select=document.createElement('select');select.setAttribute('aria-label','选择真实代码记录');for(const e of entries){const op=document.createElement('option');op.value=e.id;op.textContent=clock(e.time)+' · '+short(e.title,80);select.append(op);}select.value=entries.some(x=>x.id===state.selectedCode)?state.selectedCode:entries.at(-1).id;
   const code=document.createElement('pre'),output=document.createElement('pre');code.setAttribute('aria-label','已记录代码');output.setAttribute('aria-label','工具输出');function choose(){state.selectedCode=select.value;const entry=entries.find(x=>x.id===select.value);code.textContent=entry.code||'没有代码正文';output.textContent=entry.output||'等待已记录输出';}select.addEventListener('change',choose);body.append(select,code,output);choose();return;
  }
  state.modalSignature=null;const conn=v?.snapshot.connection||{},metrics=v?.snapshot.metrics||{},pairs=[['连接目标',(TITLE+" · 仅此一个聊天")],['真实状态',statusOf(v)],['数据内容','任务、工具动作、公开代码、轮次结果、Token'],['装饰内容','8 台织机、六种投影、光丝热力、织造成形阶段'],['绘图含义','装饰机械不是额外 Agent，造型完成不是任务交付'],['采集器常驻内存',fmt(metrics.workingSetMiB)+' MB（不含浏览器与显存）'],['保留记录',fmt(conn.retainedSteps)+' / '+fmt(conn.maxRetainedSteps)],['同步','两主题共用连接 · 前台 1 秒 / 后台 5 秒'],['已接入主题','06 视界熔炉、07 天工开物'],['其他八款','作者原版模拟演示']];
  body.innerHTML='<p>真实信息控制重点高亮；原版机械和织造动画持续演绎。</p><dl>'+pairs.map(([a,b])=>'<dt>'+esc(a)+'</dt><dd>'+esc(b)+'</dd>').join('')+'</dl><div class="live-actions"><button class="tb" type="button" data-clear>清理历史缓存</button><button class="tb" type="button" data-stop>关闭看板采集</button></div><p>清理只影响显示缓存，继续跟随；关闭采集后，关闭网页以释放绘图资源。</p><output aria-live="polite"></output>';body.querySelector('output').textContent=state.notice||state.error||'';
  for(const b of body.querySelectorAll('.live-actions button'))b.disabled=state.stopped;
  body.querySelector('[data-clear]').addEventListener('click',async()=>{try{const r=await fetch('/api/cache/clear',{method:'POST',headers:{'X-Display-Key':key}});if(!r.ok)throw Error();feedCode([],true);state.notice='已清理看板缓存，源聊天记录未改动。';await refreshStream();renderModal();}catch{state.notice='清理未完成，请检查连接。';renderModal();}});
  body.querySelector('[data-stop]').addEventListener('click',async()=>{try{const r=await fetch('/api/shutdown',{method:'POST',headers:{'X-Display-Key':key}});if(!r.ok)throw Error();state.stopped=true;stopStream();state.notice='采集已关闭；关闭网页以停止动画。';renderModal();}catch{state.notice='关闭未完成，可在启动窗口按 Ctrl+C。';renderModal();}});
 }
 return {start,updateState,updateUI,codeStep,spiderState};
}
const api={mountForge,mountLoom,derive,slots,workItems,selectWork,modelIndex,groups:GROUPS};
if(typeof module!=='undefined'&&module.exports)module.exports=api;
global.AgentDisplayLive=api;
})(typeof window!=='undefined'?window:globalThis);








