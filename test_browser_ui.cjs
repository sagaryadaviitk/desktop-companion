const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/web/browser.js','utf8');
function fixture(){
 class Element{
  constructor(id){this.id=id;this.hidden=['pair','workspace','empty','screen','reload-app'].includes(id);this.value='';this.type='password';this.handlers={};this.children=[];this.disabled=false;this.dataset={};this.textContent='';const set=new Set();this.classList={toggle:(name,value)=>{const enabled=value===undefined?!set.has(name):value;enabled?set.add(name):set.delete(name);return enabled;},contains:name=>set.has(name)};}
  setAttribute(name,value){this[name]=value;}removeAttribute(name){delete this[name];}replaceChildren(...children){this.children=children;}
  addEventListener(name,fn){this.handlers[name]=fn;}setPointerCapture(){}getBoundingClientRect(){return {left:0,top:0,width:200,height:120};}
  emit(name,event={}){return this.handlers[name]?.({preventDefault(){},...event});}
 }
 const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element(id));return elements.get(id);};
 const controlled=['back','forward','reload-page','scroll-up','scroll-down','insert'];const key=new Element('key');key.dataset.key='Enter';controlled.forEach(get);
 const doc={hidden:false,activeElement:null,handlers:{},querySelector:()=>({content:'build-1'}),getElementById:get,querySelectorAll:selector=>selector==='[data-key]'?[key]:[...controlled.map(get),key],createElement:tag=>new Element(tag),addEventListener(name,fn){this.handlers[name]=fn;}};
 const events={},posts=[],requests=[],revoked=[],timers=[];let next=0,poll,pause=0,frame=0,mode='success',resolveAction,resolveFrame,reloads=0;
 const response=(data,status=200)=>({status,ok:status>=200&&status<300,headers:{get:()=>null},json:async()=>data});
 const window={addEventListener:(name,fn)=>events[name]=fn,confirm:()=>true};
 const context={window,document:doc,navigator:{onLine:true},location:{reload:()=>reloads++},Date,Math,Uint8Array,Blob,AbortController,atob,
  URL:{createObjectURL:()=>`blob:test-${++next}`,revokeObjectURL:value=>revoked.push(value)},
  Image:class{set src(value){queueMicrotask(()=>this.onload());}},setTimeout,clearTimeout,setInterval:fn=>{timers.push(fn);return timers.length;},clearInterval(){},
  CompanionConversation:{poll:options=>{poll=options;return {pause(){pause++;},wake(){return options.read();},stop(){}};}},
  fetch:async(path,options)=>{requests.push(path);if(options.method==='POST'){
    posts.push(JSON.parse(options.body));if(mode==='lost')throw TypeError('Connection lost');
    if(mode==='pending')return new Promise(resolve=>resolveAction=()=>resolve(response({state:'applied',message:'Acknowledged'})));
    return response({state:'applied',message:'Acknowledged'});
   }
   if(mode==='expired')return response({auth_required:true},401);
   if(path==='/api/browser')return response({available:true,csrf:'test-csrf',build:'build-1',tabs:[{id:'one',title:'Demo <script>',url:'https://example.test/'}]});
   const data={id:'one',frame:`frame-${++frame}`,image:'aGVsbG8=',image_mime:'image/png',url:'https://example.test/',title:'Demo <script>',width:1000,height:600};
   if(mode==='pending-frame')return new Promise(resolve=>resolveFrame=()=>resolve(response(data)));
   return response(data);
  }};
 vm.runInNewContext(source,context);context.CompanionBrowser=window.CompanionBrowser;
 const flush=()=>new Promise(resolve=>setImmediate(resolve));
 return {context,get,doc,events,posts,requests,revoked,timers,flush,start:()=>window.CompanionBrowser.install(),poll:()=>poll,pauses:()=>pause,setMode:value=>mode=value,completeAction:()=>resolveAction(),completeFrame:()=>resolveFrame(),reloads:()=>reloads};
}
test('tap and swipe map to bounded page controls independently of phone scaling',()=>{
 const f=fixture(),browser=f.context.window.CompanionBrowser,rect={left:10,top:20,width:200,height:100};
 assert.deepEqual({...browser.point(rect,110,70)},{x:.5,y:.5});
 assert.equal(browser.gesture({x:20,y:30},{x:21,y:31},rect,600).action,'click');
 const swipe=browser.gesture({x:20,y:90},{x:20,y:30},rect,600);assert.equal(swipe.action,'scroll');assert.equal(swipe.dy,360);
 assert.equal(browser.gesture({x:20,y:5000},{x:20,y:-5000},rect,600).dy,1500);
});
test('fresh frames enable controls and page metadata remains text; replacing frames releases image memory',async()=>{
 const f=fixture();f.start();await f.flush();assert.equal(f.get('back').disabled,false);assert.equal(f.get('screen').hidden,false);
 assert.equal(f.get('page-title').textContent,'Demo <script>');assert.match(f.get('screen').src,/^blob:/);
 await f.poll().read();await f.flush();assert.ok(f.revoked.length>0);assert.equal(f.posts.length,0);
});
test('a page action is sent once and fresh view is required before subsequent controls',async()=>{
 const f=fixture();f.start();await f.flush();f.setMode('pending');const action=f.get('back').emit('click');await f.flush();
 assert.equal(f.posts.length,1);assert.equal(f.posts[0].action,'back');assert.equal(f.posts[0].frame,'frame-1');assert.equal(f.get('forward').disabled,true);
 await f.get('forward').emit('click');assert.equal(f.posts.length,1);f.completeAction();await action;await f.flush();assert.equal(f.get('forward').disabled,false);
});
test('uncertain action never resends and requires explicit review even after automatic refresh',async()=>{
 const f=fixture();f.start();await f.flush();f.setMode('lost');await f.get('back').emit('click');await f.flush();
 assert.equal(f.posts.length,1);assert.equal(f.get('forward').disabled,true);assert.match(f.get('action-result').textContent,/Check the page/);
 await f.poll().read();await f.get('back').emit('click');assert.equal(f.posts.length,1);
 f.setMode('success');await f.get('refresh').emit('click');await f.flush();assert.equal(f.get('forward').disabled,false);assert.equal(f.posts.length,1);
});
test('expired pairing removes image and typed text and stops further page reads',async()=>{
 const f=fixture();f.start();await f.flush();f.get('text').value='PRIVATE INPUT';f.setMode('expired');await f.poll().read();
 assert.equal(f.get('screen').hidden,true);assert.equal(f.get('text').value,'');assert.equal(f.get('pair').hidden,false);assert.equal(f.get('workspace').hidden,true);assert.ok(f.pauses()>0);
});
test('offline and hidden views disable input; a gesture from an older view is not applied',async()=>{
 const f=fixture();f.start();await f.flush();f.get('screen').emit('pointerdown',{isPrimary:true,pointerId:1,clientX:50,clientY:50});
 f.events.offline();f.get('screen').emit('pointerup',{pointerId:1,clientX:50,clientY:50});assert.equal(f.posts.length,0);assert.equal(f.get('back').disabled,true);
 f.doc.hidden=true;f.doc.handlers.visibilitychange();assert.equal(f.get('back').disabled,true);assert.ok(f.pauses()>0);
});
test('a gesture pauses snapshots and cannot carry into a different tab',async()=>{
 const f=fixture();f.start();await f.flush();f.get('screen').emit('pointerdown',{isPrimary:true,pointerId:2,clientX:10,clientY:10});
 const reads=f.requests.length;await f.poll().read();assert.equal(f.requests.length,reads);
 f.get('tabs').value='different';f.get('tabs').emit('change');f.get('screen').emit('pointerup',{pointerId:2,clientX:10,clientY:10});await f.flush();assert.equal(f.posts.length,0);
});
test('typed input is hidden and sent only on explicit insert, then cleared on acknowledgement',async()=>{
 const f=fixture();f.start();await f.flush();f.get('text').value='Test input';assert.equal(f.get('text').type,'password');assert.equal(f.posts.length,0);
 await f.get('typing-form').emit('submit');await f.flush();assert.equal(f.posts.length,1);assert.equal(f.posts[0].text,'Test input');assert.equal(f.get('text').value,'');
 f.get('text').value='not saved';f.events.pagehide();assert.equal(f.get('text').value,'');f.events.pageshow({persisted:true});assert.equal(f.reloads(),1);
});
test('browser view opens separately and keeps paired-page content out of executable markup',()=>{
 const html=fs.readFileSync(__dirname+'/web/index.html','utf8');assert.match(html,/<a id="browser-open"[^>]+target="_blank"[^>]+rel="noopener noreferrer"/);
 const shell=fs.readFileSync(__dirname+'/web/browser.html','utf8');assert.doesNotMatch(shell,/<script(?![^>]+src=)[^>]*>/);assert.doesNotMatch(source,/innerHTML|localStorage|eval\(|Runtime\.evaluate/);
 assert.match(shell,/type="password"[^>]+autocomplete="off"/);
});

test('a screenshot begun before an action cannot re-enable controls afterward',async()=>{
 const f=fixture();f.start();await f.flush();f.setMode('pending-frame');const pending=f.poll().read();await f.flush();
 await f.get('back').emit('click');assert.equal(f.posts.length,1);assert.equal(f.get('forward').disabled,true);
 f.completeFrame();await pending;await f.flush();assert.equal(f.get('forward').disabled,true);
 f.setMode('success');await f.poll().read();await f.flush();assert.equal(f.get('forward').disabled,false);assert.equal(f.posts.length,1);
});

test('dragging a zoomed image pans the phone view without scrolling the desktop page',async()=>{
 const f=fixture();f.start();await f.flush();f.get('zoom').emit('click');
 f.get('screen').emit('pointerdown',{isPrimary:true,pointerId:3,clientX:100,clientY:60});
 f.get('screen').emit('pointermove',{pointerId:3,clientX:30,clientY:40});
 assert.equal(f.get('viewport').scrollLeft,70);
 f.get('screen').emit('pointerup',{pointerId:3,clientX:30,clientY:40});await f.flush();assert.equal(f.posts.length,0);
});
