// Behavioral touch tests run without a browser or desktop commands.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/web/refresh.js','utf8');

function fixture(refresh=async()=>true){
  const listeners={},nodes={},states=new Set(['ready']);let blocked=false,mobile=true,standalone=false,reloads=0,requestReload=()=>true;const haptics=[];
  class Element {
    constructor(){this.scrollTop=0;this.listeners={};this.attributes={};this.hidden=false;this.classList={toggle(){}};}
    addEventListener(name,callback){this.listeners[name]=callback;}
    setAttribute(name,value){this.attributes[name]=value;}
    querySelector(){return nodes.icon;}
    getBoundingClientRect(){return {top:210};}
    closest(selector){return selector.startsWith('input')?(blocked?this:null):nodes.panel;}
  }
  for(const id of ['manual-refresh','pull-refresh','pull-refresh-label','icon','panel'])nodes[id]=new Element();
  const document={getElementById:id=>nodes[id],documentElement:{style:{setProperty(){}}},body:{classList:{contains:name=>states.has(name)}},
    querySelector:selector=>selector==='.topbar'?{getBoundingClientRect:()=>({bottom:53})}:states.has('dialog')?{}:null,
    addEventListener:(name,callback,options)=>{listeners[name]=callback;listeners[name+'Options']=options;}};
  const context={window:{},document,Element,setTimeout:()=>1,clearTimeout(){}};vm.runInNewContext(source,context);
  const controller=context.window.CompanionRefresh.install({refresh,reload:()=>{reloads++;return requestReload();},isMobile:()=>mobile,isStandalone:()=>standalone,haptic:kind=>haptics.push(kind)});
  function event(x=10,y=10,count=1,cancelable=true){return {target:nodes.panel,touches:Array.from({length:count},()=>({clientX:x,clientY:y})),cancelable,preventDefault(){this.prevented=true;}};}
  return {listeners,nodes,states,controller,event,context,reloads:()=>reloads,useReload:fn=>requestReload=fn,block:()=>blocked=true,desktop:()=>mobile=false,app:()=>standalone=true,haptics};
}

test('a downward pull at the panel top refreshes once on release and cancels a card click',async()=>{
  let count=0;const f=fixture(async()=>{count++;return true;});
  f.listeners.touchstart(f.event());const move=f.event(10,110);f.listeners.touchmove(move);
  assert.equal(move.prevented,true);assert.equal(f.nodes['pull-refresh-label'].textContent,'Release to refresh');assert.equal(count,0);
  const end=f.event();await f.listeners.touchend(end);
  assert.equal(end.prevented,true);assert.equal(count,1);assert.equal(f.nodes['pull-refresh-label'].textContent,'Up to date');
  await f.listeners.touchend(f.event());assert.equal(count,1);
  assert.equal(f.listeners.touchmoveOptions.passive,false);
});

test('pulls from the header stay native while early downward panel movement belongs to the soft refresh',async()=>{
  let count=0;const f=fixture(async()=>{count++;return true;});f.states.add('home-open');f.nodes.chats=f.nodes.panel;
  f.nodes.panel.closest=selector=>selector==='#sidebar'?f.nodes.panel:null;
  f.listeners.touchstart(f.event());const headerMove=f.event(10,110);f.listeners.touchmove(headerMove);await f.listeners.touchend(f.event());
  assert.equal(headerMove.prevented,undefined);assert.equal(count,0);
  f.nodes.panel.closest=selector=>selector.startsWith('input')?null:f.nodes.panel;
  f.listeners.touchstart(f.event());const firstMove=f.event(10,14);f.listeners.touchmove(firstMove);
  assert.equal(firstMove.prevented,true);assert.equal(count,0);
  f.listeners.touchmove(f.event(10,110));await f.listeners.touchend(f.event());assert.equal(count,1);
});

test('normal scrolling, short drags, horizontal swipes, and cancelled touches do not refresh',async()=>{
  for(const mode of ['scrolled','short','horizontal','up','cancel','pinch','uncancelable']){
    let count=0;const f=fixture(async()=>{count++;return true;});
    if(mode==='scrolled')f.nodes.panel.scrollTop=20;
    f.listeners.touchstart(f.event());
    f.listeners.touchmove(mode==='short'?f.event(10,45):mode==='horizontal'?f.event(120,20):mode==='up'?f.event(10,-50):mode==='pinch'?f.event(10,110,2):mode==='uncancelable'?f.event(10,110,1,false):f.event(10,110));
    if(mode==='cancel')f.listeners.touchcancel();
    await f.listeners.touchend(f.event());assert.equal(count,0,mode);
  }
});

test('typing, dialogs, links/code, desktop pointers, and signed out views keep their normal gestures',async()=>{
  for(const mode of ['keyboard-open','dialog','signed-out','blocked','desktop']){
    let count=0;const f=fixture(async()=>{count++;return true;});
    if(mode==='blocked')f.block();else if(mode==='desktop')f.desktop();else f.states.add(mode);
    if(mode==='signed-out')f.states.delete('ready');
    f.listeners.touchstart(f.event());const move=f.event(10,110);f.listeners.touchmove(move);await f.listeners.touchend(f.event());
    assert.equal(count,0,mode);assert.notEqual(move.prevented,true,mode);
  }
});

test('manual refresh reports failure and deduplicates overlapping attempts',async()=>{
  let resolve,count=0;const f=fixture(()=>{count++;return new Promise(done=>resolve=done);});
  const pending=f.controller.refresh();await f.controller.refresh();
  assert.equal(count,1);assert.equal(f.nodes['manual-refresh'].disabled,true);
  resolve(false);await pending;
  assert.equal(f.nodes['manual-refresh'].disabled,false);assert.equal(f.nodes['pull-refresh-label'].textContent,'Couldn’t refresh. Try again.');
});

test('an interrupted refresh recovers the button and keeps an actionable status',async()=>{
  const f=fixture(async()=>{throw new Error('Connection interrupted');});
  await f.controller.refresh();
  assert.equal(f.nodes['manual-refresh'].disabled,false);
  assert.equal(f.nodes['pull-refresh-label'].textContent,'Couldn’t refresh. Try again.');
});

test('Home Screen header pulls reload the full page while browser-tab headers remain native',async()=>{
 for(const standalone of [false,true]){
  let count=0;const f=fixture(async()=>{count++;return true;});if(standalone)f.app();f.nodes.panel.scrollTop=800;
  f.nodes.panel.closest=selector=>selector.startsWith('.topbar')?f.nodes.panel:null;
  f.listeners.touchstart(f.event());const move=f.event(10,120);f.listeners.touchmove(move);await f.listeners.touchend(f.event());
  assert.equal(count,0);assert.equal(f.reloads(),standalone?1:0);assert.equal(move.prevented,standalone?true:undefined);
  assert.deepEqual(f.haptics,standalone?['armed']:[]);if(standalone)assert.equal(f.nodes['pull-refresh-label'].textContent,'Reloading app…');
 }
});
test('refresh button requests start and completion feedback only for the explicit user refresh',async()=>{
 const f=fixture();await f.nodes['manual-refresh'].listeners.click();assert.deepEqual(f.haptics,['start','complete']);
});

function headerPull(f,mode='full'){
 f.app();f.nodes.panel.closest=selector=>selector.startsWith('.topbar')?f.nodes.panel:null;
 f.listeners.touchstart(f.event());
 f.listeners.touchmove(mode==='short'?f.event(10,45):mode==='horizontal'?f.event(120,20):f.event(10,120));
 if(mode==='cancel')f.listeners.touchcancel();
 return f.listeners.touchend(f.event());
}
test('short, horizontal and cancelled Home Screen header pulls never reload',async()=>{
 for(const mode of ['short','horizontal','cancel']){const f=fixture();await headerPull(f,mode);assert.equal(f.reloads(),0,mode);}
});
test('header full reload shares the real draft and in-flight guards and never falls back to soft refresh',async()=>{
 for(const [busy,draft,confirmed,navigation] of [[true,false,true,0],[false,true,false,0],[false,true,true,1],[false,false,false,1]]){
  let soft=0,reloaded=0,warnings=0;const f=fixture(async()=>{soft++;return true;});
  f.nodes['update-banner']={hidden:true};f.nodes['update-reload']={addEventListener(){}};
  vm.runInNewContext(fs.readFileSync(__dirname+'/web/updates.js','utf8'),f.context);
  const updates=f.context.window.CompanionUpdates.install({loadedBuild:'a'.repeat(16),hasDraft:()=>draft,isBusy:()=>busy,isInteracting:()=>false,onBusy:()=>warnings++,reload:()=>reloaded++,confirmReload:()=>confirmed});
  f.useReload(updates.reload);await headerPull(f);
  assert.equal(f.reloads(),1);assert.equal(reloaded,navigation);assert.equal(soft,0);assert.equal(warnings,busy?1:0);
  assert.equal(f.nodes['pull-refresh'].hidden,navigation===0);
 }
});
