const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const scope={window:{},setTimeout,clearTimeout,Date};vm.runInNewContext(fs.readFileSync(__dirname+'/web/conversation.js','utf8'),scope);const ui=scope.window.CompanionConversation;
test('accepted messages remain visible through saved-history lag and reconcile only by exact client identity',()=>{
 const r=ui.receipts();r.begin('chat','request','Same text',[{id:'photo'}]);assert.equal(r.list('chat')[0].state,'sending');
 r.settle('chat','request',{state:'sent',client_id:'client-one'});assert.equal(r.list('chat',[])[0].state,'accepted');
 assert.equal(r.list('other').length,0);assert.equal(r.list('chat',[{role:'user',client_id:'different',text:'Same text'}]).length,1);
 assert.equal(r.list('chat',[{role:'assistant',client_id:'client-one'}]).length,1);
 assert.equal(r.list('chat',[{role:'user',client_id:'client-one'}]).length,0);
});
test('queued, rejected and uncertain requests never masquerade as delivered conversation messages',()=>{
 const r=ui.receipts();for(const state of ['queued','unknown','rejected']){r.begin('chat','request','Draft');r.settle('chat','request',{state});assert.equal(r.list('chat').length,0);}
 r.begin('chat','request','Draft');r.remove('request');assert.equal(r.list('chat').length,0);
 r.begin('chat','request','Private draft');r.reset();assert.equal(r.list('chat').length,0);
});
test('working indicator requires a recent live active turn and disappears for stale, disconnected and idle states',()=>{
 const live={status:'working',turn_id:'turn',checked_at:1000};assert.equal(ui.working(live,true,2000),true);
 for(const [value,connected,now]of [[live,false,2000],[live,true,17000],[{...live,status:'idle'},true,2000],[{status:'working'},true,2000],[{...live,checked_at:30000},true,2000]])assert.equal(ui.working(value,connected,now),false);
});
test('polls do not overlap and schedule the next read after completion; hidden views are skipped',async()=>{
 let callback,reads=0,resolve,enabled=true,nextDelay=1500;const timers=[];
 const p=ui.poll({read:()=>{reads++;return new Promise(r=>resolve=r);},enabled:()=>enabled,delay:()=>nextDelay,setTimer:(fn,ms)=>{callback=fn;timers.push(ms);return timers.length;},clearTimer:()=>{}});
 const tick=callback();assert.equal(reads,1);assert.equal(timers.length,1);await callback();assert.equal(reads,1);resolve();await tick;assert.equal(timers.length,2);
 enabled=false;nextDelay=5000;await callback();assert.equal(reads,1);assert.equal(timers.at(-1),5000);p.stop();await callback();assert.equal(reads,1);
});
test('reopening follows the latest message through delayed image/layout growth while deliberate history scrolling stays put',()=>{
 const events={},frames=[],observers=[];let resize;const panel={clientHeight:300,scrollHeight:1000,scrollTop:700,children:[],addEventListener:(name,fn)=>events[name]=fn};
 class Resize{constructor(fn){resize=fn;}observe(){}disconnect(){}}
 class Mutation{constructor(fn){observers.push(fn);}observe(){}}
 const s=ui.scroller(panel,{frame:fn=>frames.push(fn),Resize,Mutation});function flush(){while(frames.length)frames.shift()();}
 flush();panel.clientHeight=0;events.scroll();assert.equal(s.following(),true);
 panel.clientHeight=300;panel.scrollTop=10;s.open();flush();assert.equal(panel.scrollTop,1000);
 panel.scrollHeight=1500;events.load();flush();assert.equal(panel.scrollTop,1500);
 panel.scrollTop=200;events.scroll();assert.equal(s.following(),false);panel.scrollHeight=1800;resize();flush();assert.equal(panel.scrollTop,200);
 s.open();events.wheel({deltaY:-10});flush();assert.equal(panel.scrollTop,200);
 s.open();flush();assert.equal(panel.scrollTop,1800);
});

test('read failures back off exponentially with bounded jitter and recover to the active cadence',async()=>{
 let callback,fail=true;const waits=[];
 const p=ui.poll({read:async()=>!fail,enabled:()=>true,delay:()=>750,random:()=>0,setTimer:(fn,ms)=>{callback=fn;waits.push(ms);return waits.length;},clearTimer:()=>{}});
 for(let i=0;i<9;i++)await callback();
 assert.deepEqual(waits.slice(1,6),[500,1000,2000,4000,8000]);assert.equal(waits.at(-1),15000);
 fail=false;await callback();assert.equal(waits.at(-1),750);p.stop();
});

test('wakeups coalesce during a read and paused/offline views create no timer after completion',async()=>{
 let callback,resolve,reads=0;const waits=[];
 const p=ui.poll({read:()=>{reads++;return new Promise(r=>resolve=r);},enabled:()=>true,delay:()=>1000,setTimer:(fn,ms)=>{callback=fn;waits.push(ms);return waits.length;},clearTimer:()=>{}});
 const first=callback();p.wake();p.wake();assert.equal(reads,1);resolve(true);await first;assert.equal(waits.at(-1),0);
 const second=callback();p.pause();const count=waits.length;resolve(true);await second;assert.equal(waits.length,count);
 p.wake();assert.equal(waits.at(-1),0);p.stop();
});

test('server Retry-After is a minimum delay even when a view is explicitly resumed',async()=>{
 let callback;const waits=[];
 const p=ui.poll({read:async()=>false,enabled:()=>true,delay:()=>750,retryAfter:()=>20000,random:()=>1,setTimer:(fn,ms)=>{callback=fn;waits.push(ms);return waits.length;},clearTimer:()=>{}});
 await callback();assert.equal(waits.at(-1),20000);p.wake();assert.equal(waits.at(-1),20000);p.stop();
});

test('accepted receipts in another chat retain exact identity without indefinitely blocking app reload',()=>{
 const receipts=ui.receipts();receipts.begin('A','request-A','Message');assert.equal(receipts.hasPending(),true);
 receipts.settle('A','request-A',{state:'sent',client_id:'client-A'});receipts.list('B',[]);
 assert.equal(receipts.hasPending(),false);assert.equal(receipts.list('A',[]).length,1);
 receipts.begin('B','request-B','Another message');assert.equal(receipts.hasPending(),true);
 receipts.settle('B','request-B',{state:'steered',client_id:'client-B'});assert.equal(receipts.hasPending(),false);
 receipts.list('A',[{role:'user',client_id:'different',text:'Message'}]);assert.equal(receipts.list('A').length,1);
 receipts.list('A',[{role:'user',client_id:'client-A'}]);assert.equal(receipts.list('A').length,0);
});

 test('a pending wake respects server cooldown and a cancelled callback cannot read while paused',async()=>{
 let callback,resolve,reads=0;const waits=[];
 const p=ui.poll({read:()=>{reads++;return new Promise(r=>resolve=r);},enabled:()=>true,delay:()=>750,retryAfter:()=>20000,setTimer:(fn,ms)=>{callback=fn;waits.push(ms);return waits.length;},clearTimer:()=>{}});
 const tick=callback();p.wake();resolve(true);await tick;assert.equal(waits.at(-1),20000);
 p.pause();await callback();assert.equal(reads,1);p.stop();
 });
