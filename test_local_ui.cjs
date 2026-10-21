const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const local=require('./web/local.js');
const storage=()=>{const values=new Map();return {getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key),values};};
test('local requests retain identity across reloads without storing message text and never permit an unnoticed second request',()=>{
 const saved=storage(),one=local.requests({storage:saved,uuid:()=> 'request-000000000001'});
 const first=one.begin('message','local-first');assert.equal(first.request_id,'request-000000000001');
 assert.throws(()=>one.begin('message','local-first'),/previous request/);
 const reloaded=local.requests({storage:saved,uuid:()=> 'request-000000000002'});assert.deepEqual(reloaded.get(),first);
 assert.deepEqual(Object.keys(JSON.parse([...saved.values.values()][0])).sort(),['chat_id','kind','request_id']);
 reloaded.clear();assert.equal(reloaded.begin('message','local-first').request_id,'request-000000000002');
});
test('skill choices reject unknown entries, deduplicate, and enforce three selected skills',()=>{
 const skills=['one','two','three','four'].map(id=>({id}));
 assert.deepEqual(local.selectedSkills(['one','unknown','one','two','three','four'],skills),['one','two','three']);
 assert.equal(local.validChatId('cloud-thread'),false);assert.equal(local.validChatId('local-../private'),false);assert.equal(local.validChatId('local-123'),true);
 assert.equal(local.pollDelay(0,true),1200);assert.equal(local.pollDelay(0,false),5000);assert.equal(local.pollDelay(9,true),30000);
});
test('local writes use the existing same-origin CSRF contract and send exactly one request',async()=>{
 const calls=[];
 const result=await local.requestJSON('/api/local/message',{method:'POST',csrf:'fake-csrf',build:'fake-build',body:{request_id:'request-000000000001',chat_id:'local-one',message:'hello'},fetcher:async(path,options)=>{calls.push({path,options});return {ok:true,json:async()=>({state:'accepted'})};}});
 assert.equal(result.state,'accepted');assert.equal(calls.length,1);assert.equal(calls[0].options.credentials,'same-origin');assert.equal(calls[0].options.headers['X-Companion-CSRF'],'fake-csrf');assert.equal(calls[0].options.headers['X-Companion-UI'],'fake-build');
});
test('lost acknowledgements and server failures remain uncertain and are never retried by transport',async()=>{
 for(const fetcher of [async()=>{throw new TypeError('offline');},async()=>({ok:false,status:503,json:async()=>({error:'unavailable'})}),async()=>({ok:true,json:async()=>null})]){
  let calls=0;await assert.rejects(local.requestJSON('/api/local/message',{method:'POST',fetcher:async(...args)=>{calls++;return fetcher(...args);}}),error=>error.uncertain===true);assert.equal(calls,1);
 }
 await assert.rejects(local.requestJSON('/api/local/message',{method:'POST',fetcher:async()=>({ok:false,status:403,json:async()=>({error:'Refresh first'})})}),error=>error.status===403&&error.uncertain===false);
});

class Element{
 constructor(tag='div'){this.tag=tag;this.children=[];this.handlers={};this.dataset={};this.value='';this.textContent='';this.disabled=false;this.hidden=false;this.checked=false;this.open=false;this.scrollHeight=0;this.scrollTop=0;this.clientHeight=300;}
 append(...items){this.children.push(...items);}replaceChildren(...items){this.children=[...items];}setAttribute(key,value){this[key]=value;}
 addEventListener(name,callback){this.handlers[name]=callback;}focus(){}scrollIntoView(){}
 get options(){return this.children.filter(item=>item.tag==='option');}
 querySelectorAll(query){return walk(this).filter(item=>item!==this&&item.tag==='input'&&(query!=='input:checked'||item.checked));}
 async emit(name){return this.handlers[name]?.({preventDefault(){}});}
}
function walk(item){return [item,...item.children.flatMap(walk)];}
const settle=async()=>{for(let i=0;i<5;i++)await new Promise(resolve=>setImmediate(resolve));};
function fixture({messageReply,authenticated=true,stored,modelStatus,chatModel='test/model'}={}){
 const ids=[...fs.readFileSync(__dirname+'/web/local.html','utf8').matchAll(/id="([^"]+)"/g)].map(match=>match[1]);
 const elements=Object.fromEntries(ids.map(id=>[id,new Element(id==='local-model'||id==='local-workspace-choice'?'select':'div')]));
 const calls=[],events={},timers=new Map();let nextTimer=0;
 const chat={id:'local-one',title:'Local fixture',model:chatModel,skills:[],status:'idle'};
 const status=modelStatus||{enabled:true,ready:true,busy:false,model:'test/model',models:[{id:'test/model',name:'Test model',ready:true,tools:true}],skills:[],workspaces:[],api_profiles:[]};
 const detail={chat,messages:[{role:'assistant',content:'<script>literal content</script>',created_at:1}]};
 const document={hidden:false,getElementById:id=>elements[id]||null,createElement:tag=>new Element(tag),querySelector:()=>({content:'test-build'}),addEventListener:(name,fn)=>events[name]=fn};
 const window={document,sessionStorage:stored||storage(),crypto:{randomUUID:()=> 'request-000000000001'},location:{origin:'https://phone.example',href:'https://phone.example/local?chat=local-one',search:'?chat=local-one'},history:{replaceState(){}},addEventListener:(name,fn)=>events[name]=fn,confirm:()=>true,
  ChatMarkdown:{render:(parent,text)=>{parent.textContent=text;}},fetch:async(path,options)=>{
   calls.push({path,options});let data;
   if(path==='/api/auth/status')data={authenticated};
   else if(path==='/api/status')data={csrf:'fake-csrf'};
   else if(path==='/api/local/status')data=status;
   else if(path==='/api/local/chats'&&options.method==='GET')data={chats:[chat]};
   else if(path.startsWith('/api/local/chat?'))data=detail;
   else if(path==='/api/local/message')return messageReply?messageReply(path,options):{ok:true,status:202,json:async()=>({state:'accepted',turn_id:'turn-one'})};
   else data={chat,messages:[]};
   return {ok:true,status:200,json:async()=>data};
  }};
 const context={window,document,URL,URLSearchParams,AbortController,TypeError,setTimeout:fn=>{const id=++nextTimer;timers.set(id,fn);return id;},clearTimeout:id=>timers.delete(id),console};
 vm.runInNewContext(fs.readFileSync(__dirname+'/web/local.js','utf8'),context);
 return {elements,calls,detail,chat,status,window,events,timers};
}
test('local polling preserves a typed draft and renders assistant content through the shared safe renderer',async()=>{
 const f=fixture();await settle();const input=f.elements['local-message'];input.value='Draft survives a poll';await input.emit('input');
 const rendered=walk(f.elements['local-messages']).find(item=>item.className==='local-message-body');assert.equal(rendered.textContent,'<script>literal content</script>');
 await f.elements['local-refresh'].emit('click');assert.equal(input.value,'Draft survives a poll');assert.equal(f.elements['local-send'].disabled,false);
});
test('a ready alternate model enables creation without enabling a chat whose own model is unavailable',async()=>{
 const modelStatus={enabled:true,ready:true,busy:false,model:'test/default',models:[{id:'test/default',name:'Default',ready:false,tools:true},{id:'test/alternate',name:'Alternate',ready:true,tools:true}],skills:[],workspaces:[],api_profiles:[]};
 const f=fixture({modelStatus,chatModel:'test/default'});await settle();f.elements['local-message'].value='Keep the draft';await f.elements['local-message'].emit('input');
 assert.equal(f.elements['local-create'].disabled,true);assert.equal(f.elements['local-send'].disabled,true);assert.match(f.elements['local-composer-help'].textContent,/This chat’s model is unavailable/);
 f.elements['local-model'].value='test/alternate';await f.elements['local-model'].emit('change');assert.equal(f.elements['local-create'].disabled,false);assert.equal(f.elements['local-send'].disabled,true);
 f.chat.model='test/alternate';await f.elements['local-refresh'].emit('click');assert.equal(f.elements['local-send'].disabled,false);
 f.elements['local-model'].value='test/default';await f.elements['local-model'].emit('change');assert.equal(f.elements['local-create'].disabled,true);assert.equal(f.elements['local-send'].disabled,false);assert.equal(f.elements['local-message'].value,'Keep the draft');
});
test('an unlisted chat model stays unavailable while another configured model is ready',async()=>{
 const f=fixture({chatModel:'test/removed'});await settle();f.elements['local-message'].value='Draft for the old model';await f.elements['local-message'].emit('input');assert.equal(f.elements['local-send'].disabled,true);assert.equal(f.elements['local-create'].disabled,false);
});
test('chat-only models remain usable for text and clearly show that file and API tools are unavailable',async()=>{
 const f=fixture();f.status.models[0].tools=false;await settle();f.elements['local-message'].value='A text question';await f.elements['local-message'].emit('input');
 assert.equal(f.elements['local-send'].disabled,false);assert.equal(f.elements['local-create'].disabled,false);assert.match(f.elements['local-model'].options[0].textContent,/Chat only/);assert.match(f.elements['local-chat-detail'].textContent,/Chat only/);assert.match(f.elements['local-composer-help'].textContent,/File and API tools are unavailable/);
});
test('uncertain local send is held across polling and repeated clicks with the draft preserved',async()=>{
 const f=fixture({messageReply:async()=>{throw new TypeError('lost acknowledgement');}});await settle();f.elements['local-message'].value='Keep this request';await f.elements['local-message'].emit('input');
 await f.elements['local-message-form'].emit('submit');await f.elements['local-refresh'].emit('click');await f.elements['local-message-form'].emit('submit');
 assert.equal(f.calls.filter(call=>call.path==='/api/local/message').length,1);assert.equal(f.elements['local-message'].value,'Keep this request');assert.equal(f.elements['local-uncertain'].hidden,false);assert.equal(f.elements['local-send'].disabled,true);
});
test('an invalid successful acknowledgement cannot silently unblock a duplicate local send',async()=>{
 const f=fixture({messageReply:async()=>({ok:true,status:200,json:async()=>({state:'unknown'})})});await settle();f.elements['local-message'].value='Check acknowledgement';await f.elements['local-message'].emit('input');await f.elements['local-message-form'].emit('submit');
 assert.equal(f.elements['local-uncertain'].hidden,false);assert.equal(f.elements['local-send'].disabled,true);
});
test('new text typed during delivery is retained after the earlier message is accepted',async()=>{
 let accept;const f=fixture({messageReply:()=>new Promise(resolve=>accept=()=>resolve({ok:true,status:202,json:async()=>({state:'accepted'})}))});await settle();const input=f.elements['local-message'];input.value='First message';await input.emit('input');const send=f.elements['local-message-form'].emit('submit');await settle();input.value='Next draft';await input.emit('input');accept();await send;
 assert.equal(input.value,'Next draft');assert.equal(f.calls.filter(call=>call.path==='/api/local/message').length,1);
});
test('unpaired local page links to existing pairing and does not request private local data',async()=>{
 const f=fixture({authenticated:false});await settle();assert.equal(f.elements['local-workspace'].hidden,true);assert.equal(f.elements['local-pair'].hidden,false);assert.equal(f.calls.some(call=>call.path==='/api/local/chats'),false);
});
test('archiving clears the active conversation and does not keep polling an archived chat',async()=>{
 const f=fixture();await settle();const reads=f.calls.filter(call=>call.path.startsWith('/api/local/chat?')).length;
 f.elements['local-message'].value='Unsent draft';await f.elements['local-message'].emit('input');await f.elements['local-archive'].emit('click');await f.elements['local-refresh'].emit('click');
 assert.equal(f.calls.filter(call=>call.path==='/api/local/archive').length,1);assert.equal(f.calls.filter(call=>call.path.startsWith('/api/local/chat?')).length,reads);assert.equal(f.elements['local-message'].value,'');assert.equal(f.elements['local-chat-title'].textContent,'Start a local chat');assert.equal(f.elements['local-message'].disabled,true);
});
test('local entry point uses a separate page with safe static scripts and no credential editor',()=>{
 const main=fs.readFileSync(__dirname+'/web/index.html','utf8'),html=fs.readFileSync(__dirname+'/web/local.html','utf8'),source=fs.readFileSync(__dirname+'/web/local.js','utf8');
 assert.match(main,/<a id="local-assistant-open"[^>]+href="\/local"[^>]+target="_blank"[^>]+rel="noopener noreferrer"/);
 assert.match(html,/class="local-badge">LOCAL/);assert.match(html,/Text only/);assert.doesNotMatch(html,/<script(?![^>]+src=)[^>]*>/);assert.doesNotMatch(source,/\.innerHTML\s*=/);assert.doesNotMatch(html,/<input[^>]+type="password"/);
});
