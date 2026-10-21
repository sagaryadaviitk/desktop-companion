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
 append(...items){for(const item of items){item.parentNode=this;this.children.push(item);}}replaceChildren(...items){for(const item of this.children)item.parentNode=null;this.children=[];this.append(...items);}setAttribute(key,value){this[key]=value;}
 addEventListener(name,callback){this.handlers[name]=callback;}focus(){this.focused=true;}scrollIntoView(){}showModal(){this.open=true;}close(){this.open=false;}
 get options(){return this.children.filter(item=>item.tag==='option');}
 querySelectorAll(query){return walk(this).filter(item=>item!==this&&item.tag==='input'&&(query!=='input:checked'||item.checked));}
 async emit(name){return this.handlers[name]?.({preventDefault(){}});}
}
function walk(item){return [item,...item.children.flatMap(walk)];}
function staticTree(html){
 const tree=new Element('document'),stack=[tree],elements={},voids=new Set(['area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr']);
 for(const match of html.matchAll(/<\/?([a-z][\w-]*)\b[^>]*>/gi)){
  const token=match[0],tag=match[1].toLowerCase();if(token.startsWith('</')){if(stack[stack.length-1].tag===tag)stack.pop();continue;}
  const item=new Element(tag),id=token.match(/\bid="([^"]+)"/)?.[1];if(id){item.id=id;elements[id]=item;}stack[stack.length-1].append(item);if(/\bdisabled(?:\s|>)/.test(token))item.disabled=true;if(/\bhidden(?:\s|>)/.test(token))item.hidden=true;
  if(!voids.has(tag)&&!token.endsWith('/>'))stack.push(item);
 }
 return {tree,elements};
}
const settle=async()=>{for(let i=0;i<5;i++)await new Promise(resolve=>setImmediate(resolve));};
function fixture({messageReply,authenticated=true,stored,modelStatus,chatModel='test/model',query='?chat=local-one',forkReply,previewReply}={}){
 const {tree,elements}=staticTree(fs.readFileSync(__dirname+'/web/local.html','utf8'));
 const calls=[],events={},timers=new Map();let nextTimer=0;
 const chat={id:'local-one',title:'Local fixture',model:chatModel,skills:[],status:'idle'};
 const catalog=[chat];
 const status=modelStatus||{enabled:true,ready:true,busy:false,model:'test/model',models:[{id:'test/model',name:'Test model',ready:true,tools:true}],skills:[],workspaces:[],api_profiles:[]};
 const detail={chat,messages:[{role:'assistant',content:'<script>literal content</script>',created_at:1}]};
 const details=new Map([[chat.id,detail]]),preview={source:{id:'native-one',title:'Desktop planning chat'},revision:'context-revision-one',messages:[{role:'user',content:'Visible desktop question'},{role:'assistant',content:'<img src=x onerror=alert(1)> literal preview'}],message_count:2,char_count:80,truncated:true,notice:'Only the latest visible text is copied.'};
 const document={hidden:false,getElementById:id=>walk(tree).find(item=>item.id===id)||null,createElement:tag=>new Element(tag),querySelector:()=>({content:'test-build'}),addEventListener:(name,fn)=>events[name]=fn};
 const window={document,sessionStorage:stored||storage(),crypto:{randomUUID:()=> 'request-000000000001'},location:{origin:'https://phone.example',href:'https://phone.example/local'+query,search:query},history:{replaceState(_state,_title,url){const next=new URL(url,'https://phone.example');window.location.href=next.href;window.location.search=next.search;}},addEventListener:(name,fn)=>events[name]=fn,confirm:()=>true,
  ChatMarkdown:{render:(parent,text)=>{parent.textContent=text;}},fetch:async(path,options)=>{
   calls.push({path,options});let data;
   if(path==='/api/auth/status')data={authenticated};
   else if(path==='/api/status')data={csrf:'fake-csrf'};
   else if(path==='/api/local/status')data=status;
   else if(path==='/api/local/chats'&&options.method==='GET')data={chats:catalog};
   else if(path.startsWith('/api/local/chat?'))data=details.get(new URL(path,'https://phone.example').searchParams.get('id'));
   else if(path.startsWith('/api/local/fork-preview?'))return previewReply?previewReply(path,options):{ok:true,status:200,json:async()=>preview};
   else if(path==='/api/local/chats'&&options.method==='POST'||path==='/api/local/fork'){
    if(path==='/api/local/fork'&&forkReply)return forkReply(path,options);
    const input=JSON.parse(options.body),created={id:'local-created',title:input.title||(path==='/api/local/fork'?'Desktop planning chat · Local':'New local chat'),model:input.model,skills:input.skills,status:'idle'},result={chat:created,messages:[]};
    if(path==='/api/local/fork')result.origin={source_id:input.source_chat_id,source_title:preview.source.title,message_count:preview.message_count,revision:input.context_revision,messages:preview.messages};catalog.unshift(created);details.set(created.id,result);data=result;
   }
   else if(path==='/api/local/message')return messageReply?messageReply(path,options):{ok:true,status:202,json:async()=>({state:'accepted',turn_id:'turn-one'})};
   else data={chat,messages:[]};
   return {ok:true,status:200,json:async()=>data};
  }};
 const context={window,document,URL,URLSearchParams,AbortController,TypeError,setTimeout:fn=>{const id=++nextTimer;timers.set(id,fn);return id;},clearTimeout:id=>timers.delete(id),console};
 vm.runInNewContext(fs.readFileSync(__dirname+'/web/local.js','utf8'),context);
 return {elements,calls,detail,chat,status,window,events,timers,catalog,details,preview};
}
test('local polling preserves a typed draft and renders assistant content through the shared safe renderer',async()=>{
 const f=fixture();await settle();const input=f.elements['local-message'];input.value='Draft survives a poll';await input.emit('input');
 const rendered=walk(f.elements['local-messages']).find(item=>item.className==='local-message-body');assert.equal(rendered.textContent,'<script>literal content</script>');
 await f.elements['local-refresh'].emit('click');assert.equal(input.value,'Draft survives a poll');assert.equal(f.elements['local-send'].disabled,false);
});
test('persistent controls remain mounted after replacing conversation messages',async()=>{
 const f=fixture();await settle();await f.elements['local-refresh'].emit('click');assert.equal(f.window.document.getElementById('local-empty-new'),f.elements['local-empty-new']);assert.equal(f.elements['local-notice'].hidden,true);assert.equal(f.elements['local-startup'].hidden,true);assert.equal(f.elements['local-send'].disabled,true);
});
test('composer describes configured tool scope without claiming an office connection is ready',async()=>{
 const f=fixture();await settle();assert.match(f.elements['local-composer-help'].textContent,/No workspace selected/);assert.match(f.elements['local-composer-help'].textContent,/No API profiles configured/);assert.doesNotMatch(f.elements['local-composer-help'].textContent,/API tools available/);
 f.chat.workspace='notes';f.status.workspaces=[{id:'notes',name:'Personal notes'}];f.status.api_profiles=[{id:'reports',name:'Reports',methods:['List projects']}];await f.elements['local-refresh'].emit('click');assert.match(f.elements['local-composer-help'].textContent,/Workspace read access/);assert.match(f.elements['local-composer-help'].textContent,/API methods configured/);
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
test('search filters local history without replacing the selected chat or its draft',async()=>{
 const f=fixture();await settle();f.catalog.push({id:'local-second',title:'Caption ideas',model:'test/model',status:'idle'});await f.elements['local-refresh'].emit('click');f.elements['local-message'].value='Keep this working draft';await f.elements['local-message'].emit('input');
 f.elements['local-search'].value='CAPTION';await f.elements['local-search'].emit('input');assert.equal(f.elements['local-chats'].children.length,1);assert.equal(walk(f.elements['local-chats']).find(item=>item.tag==='strong').textContent,'Caption ideas');assert.equal(f.elements['local-chat-title'].textContent,'Local fixture');assert.equal(f.elements['local-message'].value,'Keep this working draft');
 assert.deepEqual(local.filterChats([{id:'cloud-one',title:'Caption'},{id:'local-good',title:'Caption'}],'caption').map(chat=>chat.id),['local-good']);
});
test('new chat is a separate sheet and creating it immediately selects the conversation in history',async()=>{
 const f=fixture();await settle();assert.equal(f.elements['local-new-panel'].open,false);await f.elements['local-new'].emit('click');assert.equal(f.elements['local-new-panel'].open,true);f.elements['local-title-input'].value='A fresh outline';await f.elements['local-create-form'].emit('submit');
 assert.equal(f.elements['local-new-panel'].open,false);assert.equal(f.elements['local-chat-title'].textContent,'A fresh outline');assert.equal(f.elements['local-message'].focused,true);assert.ok(walk(f.elements['local-chats']).some(item=>item.tag==='strong'&&item.textContent==='A fresh outline'));assert.equal(f.calls.filter(call=>call.options.method==='POST'&&call.path==='/api/local/message').length,0);
});
test('desktop continuation opens a bounded literal context preview and never starts inference',async()=>{
 const f=fixture({query:'?from=native-one&embedded=1'});await settle();assert.equal(f.elements['local-new-panel'].open,true);assert.equal(f.elements['local-fork-review'].hidden,false);assert.equal(f.elements['local-fork-title'].textContent,'Desktop planning chat');assert.match(f.elements['local-fork-count'].textContent,/2 messages/);assert.match(f.elements['local-fork-count'].textContent,/Latest context only/);
 assert.ok(walk(f.elements['local-context-messages']).some(item=>item.tag==='p'&&item.textContent==='<img src=x onerror=alert(1)> literal preview'));assert.equal(f.calls.filter(call=>call.options.method==='POST').length,0);assert.equal(f.elements['local-create'].textContent,'Create local branch');
});
test('creating a local branch sends the reviewed revision, keeps desktop history untouched, and preserves embedded navigation',async()=>{
 const f=fixture({query:'?from=native-one&embedded=1'});await settle();await f.elements['local-create-form'].emit('submit');const writes=f.calls.filter(call=>call.options.method==='POST');assert.equal(writes.length,1);assert.equal(writes[0].path,'/api/local/fork');const body=JSON.parse(writes[0].options.body);assert.equal(body.source_chat_id,'native-one');assert.equal(body.context_revision,'context-revision-one');assert.equal(body.model,'test/model');assert.equal('messages' in body,false);
 assert.equal(f.elements['local-origin'].hidden,false);assert.equal(f.elements['local-origin-link'].textContent,'Desktop planning chat');assert.equal(f.elements['local-origin-link'].href,'/?chat=native-one');assert.match(f.window.location.search,/embedded=1/);assert.match(f.window.location.search,/chat=local-created/);assert.doesNotMatch(f.window.location.search,/from=/);assert.equal(f.elements['local-message'].focused,true);assert.equal(f.elements['local-new-panel'].open,false);
});
test('immutable copied context is literal and separate from the live local transcript',async()=>{
 const f=fixture({query:'?from=native-one'});await settle();await f.elements['local-create-form'].emit('submit');assert.equal(f.elements['local-origin-context'].hidden,false);assert.ok(walk(f.elements['local-origin-messages']).some(item=>item.tag==='strong'&&item.textContent==='Desktop assistant'));assert.ok(walk(f.elements['local-origin-messages']).some(item=>item.tag==='p'&&item.textContent==='<img src=x onerror=alert(1)> literal preview'));assert.equal(walk(f.elements['local-messages']).some(item=>item.textContent==='<img src=x onerror=alert(1)> literal preview'),false);assert.match(walk(f.elements['local-messages']).find(item=>item.className==='local-empty').textContent,/Context copied/);f.elements['local-origin-context'].open=true;await f.elements['local-refresh'].emit('click');assert.equal(f.elements['local-origin-context'].open,true);
});
test('changed desktop context requires explicit preview refresh before another branch attempt',async()=>{
 const f=fixture({query:'?from=native-one',forkReply:async()=>({ok:false,status:409,json:async()=>({error:'The source changed.'})})});await settle();await f.elements['local-create-form'].emit('submit');assert.equal(f.elements['local-create'].disabled,true);assert.match(f.elements['local-create-result'].textContent,/Refresh the context/);await f.elements['local-create-form'].emit('submit');assert.equal(f.calls.filter(call=>call.path==='/api/local/fork').length,1);await f.elements['local-context-refresh'].emit('click');assert.equal(f.elements['local-create'].disabled,false);assert.equal(f.calls.filter(call=>call.path.startsWith('/api/local/fork-preview?')).length,2);assert.equal(f.calls.filter(call=>call.path==='/api/local/fork').length,1);
});
test('uncertain branch creation is not replayed and its request identity survives reload',async()=>{
 const saved=storage(),f=fixture({query:'?from=native-one',stored:saved,forkReply:async()=>{throw new TypeError('lost acknowledgement');}});await settle();await f.elements['local-create-form'].emit('submit');await f.elements['local-refresh'].emit('click');await f.elements['local-create-form'].emit('submit');assert.equal(f.calls.filter(call=>call.path==='/api/local/fork').length,1);assert.equal(f.elements['local-uncertain'].hidden,false);assert.equal(local.requests({storage:saved}).get().kind,'fork');
});
test('origin metadata creates only a bounded desktop-chat link and rejects local or unsafe source identities',()=>{
 assert.equal(local.originInfo({origin:{source_id:'../../private',source_title:'Unsafe'}}),null);assert.equal(local.originInfo({origin:{source_id:'local-one'}}),null);assert.equal(local.originInfo({origin:{source:{id:'native-one',title:'<script>title</script>'}}}).source_title,'<script>title</script>');
});
test('hidden views pause reads and resume with fresh state without sending commands',async()=>{
 const f=fixture();await settle();f.window.document.hidden=true;f.events.visibilitychange();assert.equal(f.timers.size,0);const reads=f.calls.length;f.window.document.hidden=false;f.events.visibilitychange();await settle();assert.ok(f.calls.length>reads);assert.equal(f.calls.some(call=>call.options.method==='POST'),false);
});
test('local entry point uses a separate page with safe static scripts and no credential editor',()=>{
 const main=fs.readFileSync(__dirname+'/web/index.html','utf8'),html=fs.readFileSync(__dirname+'/web/local.html','utf8'),source=fs.readFileSync(__dirname+'/web/local.js','utf8');
 assert.match(main,/<a id="local-assistant-open"[^>]+href="\/local"[^>]+target="_blank"[^>]+rel="noopener noreferrer"/);
 assert.match(html,/class="local-badge">LOCAL/);assert.match(html,/Text only/);assert.doesNotMatch(html,/<script(?![^>]+src=)[^>]*>/);assert.doesNotMatch(source,/\.innerHTML\s*=/);assert.doesNotMatch(html,/<input[^>]+type="password"/);
});
