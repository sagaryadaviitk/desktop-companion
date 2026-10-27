const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/web/browser.js','utf8');
function fixture({chatID='chat-one',selection=true}={}){
 class Element{
  constructor(id){this.id=id;this.hidden=['pair','workspace','empty','screen','reload-app'].includes(id);this.value='';this.type='password';this.handlers={};this.children=[];this.disabled=false;this.dataset={};this.style={};this.textContent='';const set=new Set();this.classList={toggle:(name,value)=>{const enabled=value===undefined?!set.has(name):value;enabled?set.add(name):set.delete(name);return enabled;},contains:name=>set.has(name)};}
  setAttribute(name,value){this[name]=value;}removeAttribute(name){delete this[name];}replaceChildren(...children){this.children=children;}
  addEventListener(name,fn){this.handlers[name]=fn;}setPointerCapture(){}getBoundingClientRect(){return {left:0,top:0,width:200,height:120};}
  emit(name,event={}){return this.handlers[name]?.({preventDefault(){},...event});}
 }
 const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element(id));return elements.get(id);};
 const controlled=['back','forward','reload-page','scroll-up','scroll-down','insert'];const key=new Element('key');key.dataset.key='Enter';controlled.forEach(get);
 const doc={hidden:false,activeElement:null,body:new Element('body'),handlers:{},querySelector:()=>({content:'build-1'}),getElementById:get,querySelectorAll:selector=>selector==='[data-key]'?[key]:[...controlled.map(get),key],createElement:tag=>new Element(tag),addEventListener(name,fn){this.handlers[name]=fn;}};
 const events={},posts=[],postPaths=[],requests=[],revoked=[],timers=[];let next=0,poll,pause=0,frame=0,mode='success',resolveAction,resolveFrame,reloads=0,time=100000,frameOverride={};
 let catalog={available:true,csrf:'test-csrf',build:'build-1',chat:chatID?{id:chatID,title:'My chat <script>'}:null,selection:selection?{id:'one',source:'manual',available:true,revision:'selection-1'}:null,tabs:[{id:'one',title:'Demo <script>',url:'https://example.test/'},{id:'different',title:'Another page',url:'https://other.test/'}]};
 const response=(data,status=200)=>({status,ok:status>=200&&status<300,headers:{get:()=>null},json:async()=>data});
 const window={addEventListener:(name,fn)=>events[name]=fn,confirm:()=>true};
 const context={window,document:doc,navigator:{onLine:true},location:{search:chatID?'?chat='+encodeURIComponent(chatID):'',reload:()=>reloads++},Date:class extends Date{static now(){return time;}},Math,Uint8Array,Blob,AbortController,atob,URLSearchParams,
  URL:{createObjectURL:()=>`blob:test-${++next}`,revokeObjectURL:value=>revoked.push(value)},
  Image:class{set src(value){queueMicrotask(()=>this.onload());}},setTimeout,clearTimeout,setInterval:fn=>{timers.push(fn);return timers.length;},clearInterval(){},
  CompanionConversation:{poll:options=>{poll=options;return {pause(){pause++;},wake(){return options.read();},stop(){}};}},
  fetch:async(path,options)=>{requests.push(path);if(options.method==='POST'){
    const payload=JSON.parse(options.body);posts.push(payload);postPaths.push(path);if(mode==='lost')throw TypeError('Connection lost');
    if(path==='/api/browser/select'){catalog={...catalog,selection:{id:payload.id,source:'manual',available:true,revision:'selection-'+(posts.length+1)}};return response(catalog);}
    if(mode==='pending')return new Promise(resolve=>resolveAction=()=>resolve(response({state:'applied',message:'Acknowledged'})));
    return response({state:'applied',message:'Acknowledged'});
   }
   if(mode==='expired')return response({auth_required:true},401);
   if(path==='/api/browser'||path.startsWith('/api/browser?'))return response(catalog);
   const data={id:catalog.selection?.id||'one',frame:`frame-${++frame}`,chat_id:chatID,selection_revision:catalog.selection?.revision||'',image:'aGVsbG8=',image_mime:'image/png',url:'https://example.test/',title:'Demo <script>',width:1000,height:600,...frameOverride};
   if(mode==='pending-frame')return new Promise(resolve=>resolveFrame=()=>resolve(response(data)));
   return response(data);
  }};
 vm.runInNewContext(source,context);context.CompanionBrowser=window.CompanionBrowser;
 const flush=()=>new Promise(resolve=>setImmediate(resolve));
 return {context,get,doc,events,posts,postPaths,requests,revoked,timers,flush,start:()=>window.CompanionBrowser.install(),poll:()=>poll,pauses:()=>pause,setMode:value=>mode=value,completeAction:()=>resolveAction(),completeFrame:()=>resolveFrame(),reloads:()=>reloads,setCatalog:value=>catalog={...catalog,...value},setFrame:value=>frameOverride=value,advance:ms=>time+=ms};
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
 f.get('tabs').value='different';f.get('tabs').emit('change');f.get('screen').emit('pointerup',{pointerId:2,clientX:10,clientY:10});await f.flush();assert.deepEqual(f.postPaths,['/api/browser/select']);
});
test('typed input is hidden and sent only on explicit insert, then cleared on acknowledgement',async()=>{
 const f=fixture();f.start();await f.flush();f.get('text').value='Test input';assert.equal(f.get('text').type,'password');assert.equal(f.posts.length,0);
 await f.get('typing-form').emit('submit');await f.flush();assert.equal(f.posts.length,1);assert.equal(f.posts[0].text,'Test input');assert.equal(f.get('text').value,'');
 f.get('text').value='not saved';f.events.pagehide();assert.equal(f.get('text').value,'');f.events.pageshow({persisted:true});assert.equal(f.reloads(),1);
});
test('typing the next text while an insertion is in flight preserves the new input',async()=>{
 const f=fixture();f.start();await f.flush();f.get('text').value='First input';f.setMode('pending');
 const pending=f.get('typing-form').emit('submit');await f.flush();assert.equal(f.posts[0].text,'First input');
 f.get('text').value='New unsent input';f.completeAction();await pending;await f.flush();
 assert.equal(f.get('text').value,'New unsent input');assert.equal(f.posts.length,1);
});
test('browser view opens separately and keeps paired-page content out of executable markup',()=>{
 const html=fs.readFileSync(__dirname+'/web/index.html','utf8');for(const id of ['browser-open','chat-browser-open'])assert.match(html,new RegExp('<a id=\"'+id+'\"[^>]+target=\"_blank\"[^>]+rel=\"noopener noreferrer\"'));
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

test('opening a chat browser never screenshots the first unrelated page before explicit selection',async()=>{
 const f=fixture({chatID:'chat / +&=?#',selection:false});f.start();await f.flush();
 assert.deepEqual(f.requests,['/api/browser?chat=chat%20%2F%20%2B%26%3D%3F%23']);
 assert.equal(f.get('tabs').value,'');assert.equal(f.get('screen').hidden,true);assert.equal(f.get('back').disabled,true);
 assert.equal(f.get('chat-back').href,'/?chat=chat%20%2F%20%2B%26%3D%3F%23');
 assert.equal(f.get('heading').textContent,'Browser · My chat <script>');assert.match(f.get('context-detail').textContent,/draft stay open/);
 await f.poll().read();assert.equal(f.requests.filter(path=>path.startsWith('/api/browser/frame')).length,0);
 f.get('tabs').value='different';await f.get('tabs').emit('change');await f.flush();
 assert.deepEqual(f.posts,[{chat_id:'chat / +&=?#',id:'different'}]);
 const frameURL=new URL(f.requests.at(-1),'https://companion.test');
 assert.equal(frameURL.pathname,'/api/browser/frame');assert.equal(frameURL.searchParams.get('id'),'different');assert.equal(frameURL.searchParams.get('chat'),'chat / +&=?#');assert.equal(frameURL.searchParams.get('selection_revision'),'selection-2');
 assert.equal(f.get('screen').hidden,false);assert.equal(f.get('back').disabled,false);
});

test('the generic browser also requires selection and keeps its context separate from a chat',async()=>{
 const f=fixture({chatID:'',selection:false});f.start();await f.flush();assert.deepEqual(f.requests,['/api/browser']);
 f.get('tabs').value='one';await f.get('tabs').emit('change');await f.flush();
 assert.deepEqual(f.posts,[{chat_id:'',id:'one'}]);await f.get('back').emit('click');
 assert.equal(f.posts.at(-1).chat_id,'');assert.equal(f.posts.at(-1).selection_revision,'selection-2');
});

test('a closed selected page cannot fall back to another open page',async()=>{
 const f=fixture();f.start();await f.flush();const priorReads=f.requests.filter(path=>path.startsWith('/api/browser/frame')).length;
 f.get('text').value='for previous page';f.setCatalog({selection:{id:'one',source:'manual',available:false,revision:'selection-1'},tabs:[{id:'different',title:'Unrelated account',url:'https://other.test/'}]});
 await f.get('refresh').emit('click');await f.flush();assert.equal(f.get('screen').hidden,true);assert.equal(f.get('back').disabled,true);assert.equal(f.get('text').value,'');assert.equal(f.get('tabs').value,'');
 assert.match(f.get('selection-note').textContent,/no longer open/);await f.poll().read();
 assert.equal(f.requests.filter(path=>path.startsWith('/api/browser/frame')).length,priorReads);assert.equal(f.posts.length,0);
});

test('a catalog for another chat is rejected before taking a screenshot',async()=>{
 const f=fixture();f.setCatalog({chat:{id:'other-chat',title:'Other chat'}});f.start();await f.flush();
 assert.equal(f.get('screen').hidden,true);assert.equal(f.get('back').disabled,true);assert.equal(f.requests.length,1);assert.match(f.get('notice').textContent,/chat could not be confirmed/);
});

test('frames from another chat or a previous selection revision never enable controls',async()=>{
 for(const mismatch of [{chat_id:'another-chat'},{selection_revision:'old-revision'},{id:'different'}]){
  const f=fixture();f.setFrame(mismatch);f.start();await f.flush();
  assert.equal(f.get('screen').hidden,true);assert.equal(f.get('back').disabled,true);await f.get('back').emit('click');assert.equal(f.posts.length,0);
  assert.match(f.get('notice').textContent,/did not match/);
 }
});

test('another viewer selecting away and back invalidates an in-flight screenshot',async()=>{
 const f=fixture();f.start();await f.flush();f.setMode('pending-frame');const pending=f.poll().read();await f.flush();
 f.setCatalog({selection:{id:'one',source:'manual',available:true,revision:'selection-3'}});
 await f.get('refresh').emit('click');assert.equal(f.get('screen').hidden,true);
 f.completeFrame();await pending;await f.flush();assert.equal(f.get('back').disabled,true);
 f.setMode('success');await f.poll().read();await f.flush();assert.equal(f.get('back').disabled,false);
 await f.get('back').emit('click');assert.equal(f.posts[0].selection_revision,'selection-3');assert.equal(f.posts[0].chat_id,'chat-one');
});

test('freshness expires even if the image is still visible; no stale click is sent',async()=>{
 const f=fixture();f.start();await f.flush();f.advance(7001);f.timers.forEach(fn=>fn());
 assert.equal(f.get('screen').hidden,false);assert.equal(f.get('back').disabled,true);
 f.get('screen').emit('pointerdown',{isPrimary:true,pointerId:1,clientX:50,clientY:50});f.get('screen').emit('pointerup',{pointerId:1,clientX:50,clientY:50});await f.flush();assert.equal(f.posts.length,0);
});

test('zoom and focus only change the phone view and fit resets pan without sending a page action',async()=>{
 const f=fixture();f.start();await f.flush();f.get('zoom-in').emit('click');assert.match(f.get('screen').style.width,/^min\(150%/);
 for(let i=0;i<9;i++)f.get('zoom-in').emit('click');assert.match(f.get('screen').style.width,/^min\(400%/);assert.equal(f.get('zoom-in').disabled,true);
 f.get('viewport').scrollLeft=80;f.get('viewport').scrollTop=55;f.get('focus-view').emit('click');assert.equal(f.doc.body.classList.contains('view-focused'),true);
 f.get('zoom').emit('click');assert.match(f.get('screen').style.width,/^min\(100%/);assert.equal(f.get('viewport').scrollLeft,0);assert.equal(f.get('viewport').scrollTop,0);assert.equal(f.posts.length,0);
});

test('per-chat browser links encode context, use a separate tab and leave composer drafts untouched',()=>{
 const app=fs.readFileSync(__dirname+'/web/app.js','utf8'),html=fs.readFileSync(__dirname+'/web/index.html','utf8');
 const update=app.slice(app.indexOf('function updateBrowserLinks(){'),app.indexOf('function updateComposer(){'));
 const links=new Map(),el=id=>{if(!links.has(id))links.set(id,{setAttribute(name,value){this[name]=value;}});return links.get(id);};
 const drafts=new Map([['chat / +&=?#','Unsent message']]),photoDrafts=new Map([['chat / +&=?#',[{id:'unsent-photo'}]]]);
 const context={el,URL,location:{origin:'https://companion.test'},selectedData:{id:'chat / +&=?#',title:'A <script> chat'},drafts,photoDrafts};
 vm.runInNewContext(update+'\nupdateBrowserLinks();',context);
 assert.equal(el('chat-browser-open').href,'/browser?chat=chat+%2F+%2B%26%3D%3F%23');assert.equal(el('chat-browser-open').hidden,false);assert.match(el('chat-browser-open')['aria-label'],/A <script> chat/);
 assert.equal(el('browser-open').href,el('chat-browser-open').href);assert.equal(drafts.get('chat / +&=?#'),'Unsent message');assert.deepEqual(photoDrafts.get('chat / +&=?#'),[{id:'unsent-photo'}]);
 assert.match(html,/<a id="chat-browser-open"[^>]+target="_blank"[^>]+rel="noopener noreferrer"/);assert.doesNotMatch(app,/chat-browser-open'\)\.addEventListener/);
 context.selectedData={id:'next-chat',title:'Next chat'};vm.runInNewContext('updateBrowserLinks();',context);assert.equal(el('chat-browser-open').href,'/browser?chat=next-chat');
 context.selectedData=null;vm.runInNewContext('updateBrowserLinks();',context);assert.equal(el('chat-browser-open').hidden,true);assert.equal(el('browser-open').href,'/browser');
});
