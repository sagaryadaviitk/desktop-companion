const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
 constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this.handlers={};this.value='';this.textContent='';this.open=false;this.disabled=false;this.hidden=false;this.classList={contains:()=>false};}
 append(...nodes){this.children.push(...nodes);} replaceChildren(...nodes){this.children=[...nodes];}
 addEventListener(name,fn){this.handlers[name]=fn;} setAttribute(name,value){this[name]=value;}
 querySelectorAll(selector){return walk(this).filter(n=>n!==this&&n.tag===selector);}
 showModal(){this.open=true;} close(){this.open=false;} focus(){}
 emit(name){return this.handlers[name]?.({preventDefault(){}});}
}
function walk(root){return [root,...root.children.flatMap(walk)];}
function document(){const nodes=new Map();return {nodes,body:new Element(),createElement:tag=>new Element(tag),getElementById(id){if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);}};}
function queueFixture(){
 const doc=document(),calls=[],results=[];let refreshes=0,fail=false;const item={id:'queue-1',revision:'a'.repeat(64),text:'Original',editable_text:'Original',can_steer:true,can_edit:true,can_delete:true,attachment_count:1,photos:[{id:'photo-1',name:'Screenshot',url:'/api/photos/photo-1'}]};
 const data={id:'chat-1',control:{live:{queue_steer:true}},pending_instructions:[item]},context={window:{},document:doc,crypto:{randomUUID:()=>`request-${calls.length+1}`}};
 vm.runInNewContext(fs.readFileSync(__dirname+'/web/queue.js','utf8'),context);
 const ui=context.window.CompanionQueue.install({document:doc,getChat:()=>data,isConnected:()=>true,request:async(path,payload)=>{calls.push({path,payload});if(fail)throw Error('Acknowledgement lost');return {state:path.endsWith('edit')?'updated':'deleted',message:'Saved'};},steer:async()=>{},refresh:async()=>refreshes++,onResult:(...args)=>results.push(args)});
 const rendered=ui.render(data);return {ui,doc,calls,results,data,item,rendered,setFail:value=>fail=value,refreshes:()=>refreshes};
}
test('queued photos have a count and thumbnails; editing preserves the entry rather than sending again',async()=>{
 const f=queueFixture();assert.match(f.rendered.children[0].textContent,/1 photo/);assert.equal(walk(f.rendered).filter(n=>n.tag==='img')[0].src,'/api/photos/photo-1');
 await walk(f.rendered).find(n=>n.dataset.action==='edit').emit('click');assert.equal(f.doc.getElementById('queue-dialog').open,true);assert.equal(f.doc.getElementById('queue-message').value,'Original');assert.equal(f.calls.length,0);
 f.doc.getElementById('queue-message').value='Edited';await f.doc.getElementById('queue-form').emit('submit');
 assert.equal(f.calls.length,1);assert.equal(f.calls[0].path,'/api/queue/edit');assert.equal(f.calls[0].payload.queue_id,'queue-1');assert.equal(f.calls[0].payload.message,'Edited');assert.equal('photos' in f.calls[0].payload,false);assert.equal(f.doc.getElementById('queue-dialog').open,false);assert.equal(f.refreshes(),1);
});
test('delete requires its own review and cannot edit or delete a changed queue item',async()=>{
 const f=queueFixture();await walk(f.rendered).find(n=>n.dataset.action==='delete').emit('click');assert.equal(f.calls.length,0);assert.equal(f.doc.getElementById('queue-message').readOnly,true);
 f.data.pending_instructions=[{...f.item,revision:'b'.repeat(64)}];await f.doc.getElementById('queue-form').emit('submit');assert.equal(f.calls.length,0);assert.match(f.doc.getElementById('queue-error').textContent,/changed or left/);
 f.data.pending_instructions=[f.item];await f.doc.getElementById('queue-form').emit('submit');assert.equal(f.calls[0].path,'/api/queue/delete');assert.equal('message' in f.calls[0].payload,false);
});
test('a failed edit retains the draft and reuses its request identity on an explicit retry',async()=>{
 const f=queueFixture();f.setFail(true);await walk(f.rendered).find(n=>n.dataset.action==='edit').emit('click');f.doc.getElementById('queue-message').value='Keep this draft';await f.doc.getElementById('queue-form').emit('submit');
 assert.equal(f.doc.getElementById('queue-dialog').open,true);assert.equal(f.doc.getElementById('queue-message').value,'Keep this draft');assert.equal(f.ui.isBusy(),false);assert.equal(f.calls.length,1);
 await f.doc.getElementById('queue-form').emit('submit');assert.equal(f.calls[0].payload.command_id,f.calls[1].payload.command_id);
 f.ui.reset();assert.equal(f.doc.getElementById('queue-dialog').open,false);assert.equal(f.doc.getElementById('queue-message').value,'');
});
const app=fs.readFileSync(__dirname+'/web/app.js','utf8');
function composerFixture(result){
 const doc=document(),drafts=new Map([['chat-1','My photo message']]),photos=new Map([['chat-1',[{id:'one'},{id:'two'}]]]);doc.getElementById('instruction').value='My photo message';const calls=[];
 const context={el:id=>doc.getElementById(id),selected:'chat-1',selectedData:{control:{available:true,live:{}},last_recorded_turn:{turn_id:'turn-1'}},sending:false,connected:true,csrf:'test',photoUploads:new Map(),photoDrafts:photos,drafts,deliveryMode:'queue',authVersion:0,commandAttempts:new Map(),crypto:{randomUUID:()=> 'composer-identity'},queueUI:{reveal(){}},updateComposer(){},showCommandResult(){},refreshChat:async()=>{},api:async(path,options)=>{calls.push(JSON.parse(options.body));return result(context,doc);}};
 vm.runInNewContext(app.slice(app.indexOf('el("composer").addEventListener("submit"'),app.indexOf("el('connect-chat').addEventListener")),context);
 return {doc,context,calls,drafts,photos,submit:()=>doc.getElementById('composer').emit('submit')};
}
test('confirmed photo queue acceptance clears only the submitted composer text and attachments',async()=>{
 const f=composerFixture(async()=>({state:'queued',message:'Queued'}));await f.submit();assert.equal(f.doc.getElementById('instruction').value,'');assert.equal(f.drafts.has('chat-1'),false);assert.equal(f.photos.get('chat-1').length,0);assert.equal(f.context.commandAttempts.size,0);
 const newer=composerFixture(async(context,doc)=>{doc.getElementById('instruction').value='New draft';context.drafts.set('chat-1','New draft');context.photoDrafts.get('chat-1').push({id:'three'});return {state:'queued',message:'Queued'};});await newer.submit();assert.equal(newer.doc.getElementById('instruction').value,'New draft');assert.equal(newer.photos.get('chat-1')[0].id,'three');
});
test('uncertain submission keeps its draft and never retries without another user action',async()=>{
 const f=composerFixture(async()=>{throw Error('Acknowledgement unconfirmed');});await f.submit();assert.equal(f.calls.length,1);assert.equal(f.doc.getElementById('instruction').value,'My photo message');assert.equal(f.photos.get('chat-1').length,2);assert.equal(f.context.commandAttempts.size,1);
});
function questionFixture(count=1,fail=false){
 const doc=document();doc.getElementById('questions-dialog').open=true;const questions=Array.from({length:count},(_,i)=>({id:'q'+i,revision:'r'+i,title:'Question '+i,options:[],delivery:'unanswered'}));
 const context={document:doc,el:id=>doc.getElementById(id),note:(tag,text,cls)=>{const n=new Element(tag);n.textContent=text;n.className=cls;return n;},selected:'chat',selectedData:{questions,control:{available:true,live:{}}},connected:true,authVersion:0,csrf:'test',questionSignature:'',questionDrafts:new Map(),questionBusy:new Set(),questionErrors:new Map(),queueSteering:new Set(),queueUI:{reveal(){}},showCommandResult(){},refreshChat:async()=>{},api:async()=>{if(fail)throw Error('Unconfirmed');return {state:'steered',message:'Accepted'};}};
 vm.runInNewContext(app.slice(app.indexOf('function renderQuestions(){'),app.indexOf("el('questions-banner').addEventListener")),context);context.renderQuestions();return {context,doc};
}
test('accepted final answers close the question panel; remaining questions and errors keep it open',async()=>{
 for(const [count,fail,open] of [[1,false,false],[2,false,true],[1,true,true]]){
  const f=questionFixture(count,fail),form=walk(f.doc.getElementById('question-cards')).find(n=>n.tag==='form'),textarea=form.children.find(n=>n.tag==='textarea');textarea.value='My answer';await textarea.emit('input');await form.emit('submit');assert.equal(f.doc.getElementById('questions-dialog').open,open);
 }
 const f=questionFixture();f.context.selectedData.questions=[];f.context.questionSignature='';f.context.renderQuestions();assert.equal(f.doc.getElementById('questions-dialog').open,false);
});
