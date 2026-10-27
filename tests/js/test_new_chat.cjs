const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
function fixture(storage=new Map(),targetWait=null){
 let seq=0,auth=1,posts=[],created=[],fail=false,delivery='sent';const nodes={};
 class Element{constructor(){this.value='';this.hidden=false;this.disabled=false;this.listeners={};this.children=[];this.open=false;this.options=[];}addEventListener(n,f){this.listeners[n]=f;}replaceChildren(...items){this.children=items;this.options=[];if(this===nodes['new-chat-target'])this.value='';}append(...items){this.children.push(...items);this.options.push(...items);if(this===nodes['new-chat-target']&&!this.value)this.value=items[0]?.value||'';}setAttribute(){}showModal(){this.open=true;}close(){this.open=false;}}
 for(const id of ['new-chat-dialog','new-chat-prompt','new-chat-target','new-chat-result','new-chat-close','new-chat-create','new-chat-destination','new-add-photo','new-photo-drafts','new-chat-open','new-chat-another','new-chat-form'])nodes[id]=new Element();
 let attachment;
 const root={window:{},document:{getElementById:id=>nodes[id],createElement:()=>new Element()},crypto:{randomUUID:()=> 'uuid-request-123456-'+seq++},Option:class{constructor(text,value){Object.assign(this,{text,value});}},sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},setTimeout:done=>done(),CompanionAttachments:{install(options){attachment=options;}}};
 vm.runInNewContext(fs.readFileSync(__dirname+'/../../web/new_chat.js','utf8'),root);
 const flow=root.window.CompanionNewChat.install({api:async path=>{if(path.startsWith('/api/chats/creation'))return{state:'created',thread_id:'previous-chat'};if(targetWait)await targetWait;return{available:true,targets:[{id:'personal',name:'Personal chat',project_key:'personal',available:true},{id:'project-target',name:'Scripts',folder:'scripts',project_key:'project:one',available:true}]};},post:async(path,payload)=>{posts.push({path,payload:JSON.parse(JSON.stringify(payload))});if(path==='/api/photos')return{id:'a'.repeat(32),url:'/api/photos/test',name:'Photo'};if(fail){fail=false;throw new Error('Lost connection');}return{state:'created',thread_id:'created-chat',delivery:{state:delivery,message:'Delivery '+delivery}};},preparePhoto:async()=> 'data:image/jpeg;base64,OWN_PHOTO',isConnected:()=>true,getVersion:()=>auth,onCreated:async(...args)=>created.push(args)});
 return {nodes,flow,posts,created,storage,attachment:()=>attachment,fail:()=>fail=true,delivery:v=>delivery=v,signOut:()=>auth++,type(value){nodes['new-chat-prompt'].value=value;nodes['new-chat-prompt'].listeners.input();},submit:()=>nodes['new-chat-form'].listeners.submit({preventDefault(){}})};
}
test('project context is selected, but closing and reopening keeps the drafted destination',async()=>{
 const f=fixture();await f.flow.open('project:one');assert.equal(f.nodes['new-chat-target'].value,'project-target');assert.equal(f.nodes['new-chat-create'].disabled,true);
 f.type('Explain this problem');f.nodes['new-chat-close'].listeners.click();await f.flow.open('personal');assert.equal(f.nodes['new-chat-target'].value,'project-target');assert.equal(f.flow.hasDraft(),true);
});
test('project picker stays disabled during loading and an unavailable project never silently becomes Personal',async()=>{
 let release;const wait=new Promise(resolve=>release=resolve),f=fixture(new Map(),wait);f.nodes['new-chat-target'].value='personal';const opening=f.flow.open('project:one');f.type('Question before projects finish loading');assert.equal(f.nodes['new-chat-target'].disabled,true);assert.equal(f.nodes['new-chat-create'].disabled,true);await f.submit();assert.equal(f.posts.length,0);release();await opening;assert.equal(f.nodes['new-chat-target'].value,'project-target');assert.equal(f.nodes['new-chat-target'].disabled,false);
 const missing=fixture();await missing.flow.open('project:missing');missing.type('Keep the intended project');assert.equal(missing.nodes['new-chat-target'].value,'');assert.equal(missing.nodes['new-chat-create'].disabled,true);
});
test('new-chat photo paste uses its own scope and submits a question without a naming field',async()=>{
 const f=fixture();await f.flow.open('project:one');f.type('Question about photo');await f.attachment().addFiles([{name:'Own.png'}]);
 assert.equal(f.posts[0].path,'/api/photos');assert.ok(f.posts[0].payload.draft_id);assert.equal(f.posts[0].payload.thread_id,undefined);
 await f.submit();const request=f.posts.find(p=>p.path==='/api/chats/create').payload;
 assert.equal(request.target_id,'project-target');assert.equal(request.message,'Question about photo');assert.equal(request.photos[0],'a'.repeat(32));assert.equal(request.title,undefined);assert.equal(f.created.length,1);assert.equal(f.created[0][1],null);assert.equal(f.flow.hasDraft(),false);assert.equal(f.storage.size,0);
});
test('lost acknowledgement keeps immutable input and retries the exact same creation identity',async()=>{
 const f=fixture();await f.flow.open('personal');f.type('Do not duplicate this');f.fail();await f.submit();assert.equal(f.nodes['new-chat-prompt'].disabled,true);assert.equal(f.created.length,0);
 const saved=JSON.parse(f.storage.get('companion-new-chat'));assert.deepEqual(Object.keys(saved),['command_id']);assert.equal(JSON.stringify(saved).includes('Do not duplicate'),false);
 await f.submit();assert.deepEqual(f.posts[0].payload,f.posts[1].payload);assert.equal(f.created.length,1);
});
test('only a known pending connection can poll; unknown delivery never automatically resends',async()=>{
 const pending=fixture();await pending.flow.open();pending.type('First question');pending.delivery('pending');await pending.submit();assert.equal(pending.posts.length,3);assert.equal(pending.created[0][1].message,'First question');
 const uncertain=fixture();await uncertain.flow.open();uncertain.type('Keep this question');uncertain.delivery('unknown');await uncertain.submit();assert.equal(uncertain.posts.length,1);assert.equal(uncertain.created.length,0);assert.equal(uncertain.flow.hasDraft(),true);assert.equal(uncertain.nodes['new-chat-open'].hidden,false);
});
test('a reload witness opens the previous chat and cannot create again until explicitly reset',async()=>{
 const storage=new Map([['companion-new-chat',JSON.stringify({command_id:'known-request-123456'})]]);const f=fixture(storage);await f.flow.open();assert.equal(f.nodes['new-chat-create'].disabled,true);assert.equal(f.nodes['new-chat-open'].hidden,false);assert.equal(f.attachment().canAdd(),false);await f.nodes['new-chat-open'].listeners.click();assert.equal(f.created[0][0].thread_id,'previous-chat');assert.equal(f.posts.length,0);
 f.nodes['new-chat-another'].listeners.click();assert.equal(f.storage.size,0);assert.equal(f.flow.hasDraft(),false);
});
