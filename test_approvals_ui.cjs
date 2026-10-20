const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
 constructor(tag='div'){this.tag=tag;this.children=[];this.handlers={};this.dataset={};this.classList={add(){}};this.open=false;this.disabled=false;this.value='';this.scrollTop=0;}
 append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=[...nodes];}setAttribute(k,v){this[k]=v;}
 addEventListener(k,fn){this.handlers[k]=fn;}showModal(){this.open=true;}close(){this.open=false;}focus(){this.focused=true;}
 emit(k){return this.handlers[k]?.({preventDefault(){}});}
}
function walk(e){return [e,...e.children.flatMap(walk)];}
function fixture(submit=async()=>({state:'answered',message:'Accepted'})){
 const context={window:{}},document={createElement:t=>new Element(t)};
 vm.runInNewContext(fs.readFileSync(__dirname+'/web/approvals.js','utf8'),context);
 const nodes=Object.fromEntries(['dialog','banner','panel','close','summary'].map(id=>[id,new Element()])),calls=[];
 const ui=context.window.CompanionApprovals.create({...nodes,document,submit:async payload=>{calls.push(payload);return submit(payload);}});
 const request={id:'one',revision:'revision',kind:'command',title:'Approve command',message:'Review this',details:[{label:'Command',text:'<script>alert(1)</script>'}],fields:[],choices:[{id:'allow',label:'Allow once',scope:'Only this request'},{id:'decline',label:'Decline'}]};
 const update=(requests=[request],connected=true,thread='chat')=>ui.update({thread,visible:true,connected,requests});
 update();nodes.banner.emit('click');return {ui,request,update,calls,...nodes};
}
function button(f,label){return walk(f.panel).find(n=>n.tag==='button'&&n.textContent===label);}
test('approval is explicit, shows literal action details, and accepted last response closes the panel',async()=>{
 const f=fixture();assert.equal(f.calls.length,0);assert.equal(f.dialog.open,true);
 assert.equal(walk(f.panel).find(n=>n.tag==='pre').textContent,'<script>alert(1)</script>');
 const form=walk(f.panel).find(n=>n.tag==='form');await form.emit('submit');assert.equal(f.calls.length,0);
 await button(f,'Allow once').emit('click');assert.equal(f.calls.length,1);assert.equal(f.calls[0].choice,'allow');assert.equal(f.dialog.open,false);assert.equal(f.banner.hidden,true);
});
test('pending response cannot submit twice, loss of acknowledgement never resends on poll or reclick',async()=>{
 let resolve;const f=fixture(()=>new Promise(r=>resolve=r)),b=button(f,'Allow once');const pending=b.emit('click');await b.emit('click');assert.equal(f.calls.length,1);assert.equal(f.ui.busy(),true);resolve({state:'answered',message:'Accepted'});await pending;
 const g=fixture(async()=>{throw Error('Connection lost');});const c=button(g,'Allow once');await c.emit('click');g.update();await c.emit('click');assert.equal(g.calls.length,1);assert.equal(button(g,'Allow once').disabled,true);
});
test('unchanged polls preserve typed input and node identity; changed revision requires a new review',async()=>{
 const f=fixture();f.request.fields=[{id:'name',label:'Name',type:'string',required:true}];f.update();
 const input=walk(f.panel).find(n=>n.tag==='textarea');input.value='Kept draft';input.emit('input');f.update();assert.equal(walk(f.panel).find(n=>n.tag==='textarea'),input);assert.equal(f.ui.hasDraft(),true);
 await button(f,'Allow once').emit('click');assert.equal(f.calls[0].answers.name,'Kept draft');
 f.update([{...f.request,revision:'new'}]);f.banner.emit('click');assert.equal(walk(f.panel).find(n=>n.tag==='textarea').value,'');
});
test('required fields prevent accidental approval and denial does not require filling the form',async()=>{
 const f=fixture();f.request.fields=[{id:'value',label:'Value',type:'boolean',required:true}];f.update();await button(f,'Allow once').emit('click');assert.equal(f.calls.length,0);
 await button(f,'Decline').emit('click');assert.equal(f.calls[0].choice,'decline');assert.deepEqual(Object.keys(f.calls[0].answers),[]);
});
test('disconnect disables responses and changing chat invalidates old handlers without clearing unrelated work',async()=>{
 const f=fixture(),old=button(f,'Allow once');f.update([f.request],false);await old.emit('click');assert.equal(f.calls.length,0);
 f.update([f.request],true,'other');await old.emit('click');assert.equal(f.calls.length,0);assert.equal(f.dialog.open,false);
 f.ui.reset();assert.equal(f.banner.hidden,true);assert.equal(f.ui.hasDraft(),false);
});
test('a desktop-only verification request is visible without fake approval controls',()=>{
 const f=fixture();f.update([{...f.request,kind:'desktop',title:'Verify on your Mac',choices:[]}]);assert.ok(walk(f.panel).find(n=>n.textContent==='Requires your Mac'));assert.equal(walk(f.panel).filter(n=>n.tag==='button').length,0);
});
