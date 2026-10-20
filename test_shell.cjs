const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function fixture({display=false,legacy=false,vibrate}={}){
 const frames=[],media={matches:display,addEventListener:(_,fn)=>media.change=fn},doc={documentElement:{dataset:{}},activeElement:null},navigator={standalone:legacy,vibrate};
 const root={window:{matchMedia:()=>media,innerHeight:800},document:doc,navigator,WeakMap,requestAnimationFrame:fn=>frames.push(fn)};
 vm.runInNewContext(fs.readFileSync(__dirname+'/web/shell.js','utf8'),root);return {ui:root.window.CompanionShell,doc,media,frames};
}
test('Home Screen mode detects display-mode and the iOS legacy flag and follows changes',()=>{
 for(const options of [{display:true},{legacy:true}]){const f=fixture(options);assert.equal(f.ui.standalone(),true);assert.equal(f.doc.documentElement.dataset.standalone,'true');}
 const f=fixture();assert.equal(f.ui.standalone(),false);f.media.matches=true;f.media.change();assert.equal(f.doc.documentElement.dataset.standalone,'true');
});
test('photo and polling updates do not repeatedly collapse an unchanged focused textarea',()=>{
 const f=fixture(),writes=[];let height='';const input={value:'',clientWidth:250,scrollHeight:90,style:{get height(){return height;},set height(value){height=value;writes.push(value);}}};
 f.ui.sizeInput(input);for(let i=0;i<10;i++)f.ui.sizeInput(input);assert.deepEqual(writes,['52px']);
 input.value='A caption';f.ui.sizeInput(input);assert.equal(height,'90px');const size=writes.length;f.ui.sizeInput(input);assert.equal(writes.length,size);
 input.clientWidth=180;input.scrollHeight=180;f.ui.sizeInput(input,120);assert.equal(height,'120px');
 input.value='';f.ui.sizeInput(input);assert.equal(height,'52px');
});
test('photo layout reanchors the current selection without moving focus or overwriting newer typing',()=>{
 const f=fixture(),ranges=[],input={isConnected:true,disabled:false,selectionStart:2,selectionEnd:4,selectionDirection:'forward',setSelectionRange:(...args)=>ranges.push(args)};
 f.doc.activeElement=input;f.ui.repaintCaret(input);input.selectionStart=6;input.selectionEnd=6;f.frames.shift()();assert.deepEqual(ranges,[[6,6,'forward']]);
 f.ui.repaintCaret(input);f.doc.activeElement={};f.frames.shift()();assert.equal(ranges.length,1);
 f.doc.activeElement=input;input.disabled=true;f.ui.repaintCaret(input);f.frames.shift()();assert.equal(ranges.length,1);
});
test('haptics use the supported vibration API and degrade safely when absent, rejected or disabled',()=>{
 assert.equal(fixture().ui.haptic(),false);assert.equal(fixture({vibrate:()=>false}).ui.haptic(),false);assert.equal(fixture({vibrate:()=>{throw Error('blocked');}}).ui.haptic(),false);
 const values=[],f=fixture({vibrate:n=>{values.push(n);return true;}});assert.equal(f.ui.haptic('start'),true);f.ui.haptic('complete');assert.deepEqual(values,[10,6]);
});

function keyboardFixture(){
 const listeners={},nodes={},classes=new Set(['ready','keyboard-open']);
 const state={mobile:true,dialog:false,blocked:false,outside:false,selecting:false,zoom:1,blurs:0};
 class Element{
  constructor(){this.scrollTop=0;this.hidden=true;this.classList={toggle(){}};}
  addEventListener(name,fn){this[name]=fn;}
  setAttribute(){}
  getBoundingClientRect(){return {top:53,bottom:53};}
  querySelector(){return nodes.icon;}
  closest(selector){if(selector.startsWith('#messages')||selector.startsWith('#chats'))return state.outside?null:nodes.messages;if(selector.startsWith('.topbar'))return null;return state.blocked?this:null;}
 }
 for(const id of ['instruction','messages','manual-refresh','pull-refresh','pull-refresh-label','icon'])nodes[id]=new Element();
 const input=nodes.instruction;input.value='Keep draft';input.selectionStart=input.selectionEnd=7;
 const document={activeElement:input,documentElement:{dataset:{},style:{setProperty(){}}},body:{classList:{contains:name=>classes.has(name)}},getElementById:id=>nodes[id],querySelector:s=>s==='.topbar'?nodes.messages:state.dialog?{}:null,addEventListener:(name,fn,options)=>{(listeners[name]??=[]).push(fn);fn.options=options;}};
 input.blur=()=>{state.blurs++;document.activeElement=null;classes.delete('keyboard-open');};
 const context={window:{matchMedia:()=>({matches:false,addEventListener(){}}),getSelection:()=>({isCollapsed:!state.selecting}),visualViewport:{get scale(){return state.zoom;}}},navigator:{},document,Element,WeakMap,setTimeout:()=>1,clearTimeout(){}};
 vm.runInNewContext(fs.readFileSync(__dirname+'/web/shell.js','utf8'),context);context.window.CompanionShell.installKeyboard({isMobile:()=>state.mobile});
 const event=(x=10,y=10,count=1,finger=4)=>({target:nodes.messages,touches:Array.from({length:count},()=>({clientX:x,clientY:y,identifier:finger})),preventDefault(){this.prevented=true;},cancelable:true});
 const fire=(name,e=event())=>(listeners[name]||[]).map(fn=>fn(e));
 return {context,document,classes,state,input,nodes,listeners,event,fire};
}
test('downward message swipe dismisses the keyboard once without changing a draft or preventing native scrolling',()=>{
 const f=keyboardFixture();f.fire('touchstart');const move=f.event(12,65);f.fire('touchmove',move);f.fire('touchmove',f.event(12,100));
 assert.equal(f.state.blurs,1);assert.equal(f.input.value,'Keep draft');assert.equal(move.prevented,undefined);assert.equal(f.listeners.touchmove[0].options.passive,true);
});
test('keyboard dismissal stays within mobile messages and preserves dialogs, other inputs, zoom and interactive content',()=>{
 for(const mode of ['desktop','closed','other-input','dialog','outside','blocked','zoom']){
  const f=keyboardFixture();if(mode==='desktop')f.state.mobile=false;else if(mode==='closed')f.classes.delete('keyboard-open');else if(mode==='other-input')f.document.activeElement={};else if(mode==='zoom')f.state.zoom=1.5;else f.state[mode]=true;
  f.fire('touchstart');f.fire('touchmove',f.event(10,100));assert.equal(f.state.blurs,0,mode);
 }
});
test('short, upward, horizontal, cancelled, multi-finger and changed-finger gestures keep the keyboard open',()=>{
 for(const mode of ['short','up','horizontal','cancel','end','pinch-start','pinch-move','finger']){
  const f=keyboardFixture();f.fire('touchstart',f.event(10,10,mode==='pinch-start'?2:1));
  if(mode==='cancel'||mode==='end')f.fire(mode==='cancel'?'touchcancel':'touchend');
  f.fire('touchmove',mode==='short'?f.event(10,35):mode==='up'?f.event(10,-20):mode==='horizontal'?f.event(90,20):mode==='pinch-move'?f.event(10,100,2):mode==='finger'?f.event(10,100,1,5):f.event(10,100));
  assert.equal(f.state.blurs,0,mode);
 }
});
test('existing or newly started text selection is never dismissed by a swipe',()=>{
 for(const mode of ['message-selection','input-selection','selection-after-start','input-selection-after-start']){
  const f=keyboardFixture();if(mode==='message-selection')f.state.selecting=true;if(mode==='input-selection')f.input.selectionEnd=9;
  f.fire('touchstart');if(mode==='selection-after-start')f.state.selecting=true;if(mode==='input-selection-after-start')f.input.selectionEnd=9;
  f.fire('touchmove',f.event(10,100));assert.equal(f.state.blurs,0,mode);
 }
});
test('focus changes and pinch zoom beginning during a swipe cancel dismissal',()=>{
 for(const mode of ['focus','zoom']){const f=keyboardFixture();f.fire('touchstart');if(mode==='focus')f.document.activeElement={};else f.state.zoom=1.4;f.fire('touchmove',f.event(10,100));assert.equal(f.state.blurs,0,mode);}
});
test('keyboard dismissal cannot also soft refresh or reload; the next separate pull can refresh normally',async()=>{
 const f=keyboardFixture();let soft=0,reloads=0;vm.runInNewContext(fs.readFileSync(__dirname+'/web/refresh.js','utf8'),f.context);
 f.context.window.CompanionRefresh.install({refresh:async()=>{soft++;return true;},reload:()=>{reloads++;return true;},isMobile:()=>true,isStandalone:()=>true,haptic(){}});
 f.fire('touchstart');f.fire('touchmove',f.event(10,130));await Promise.all(f.fire('touchend'));
 assert.equal(f.state.blurs,1);assert.equal(soft,0);assert.equal(reloads,0);
 f.fire('touchstart');f.fire('touchmove',f.event(10,130));await Promise.all(f.fire('touchend'));assert.equal(soft,1);assert.equal(reloads,0);
});
