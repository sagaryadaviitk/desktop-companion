const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/web/attachments.js','utf8');
function fixture(read){
 const nodes={},errors=[],added=[];let enabled=true,context='chat-a';
 for(const id of ['photo-menu','add-photo','choose-photo','photo-input','instruction','paste-photo'])nodes[id]={hidden:true,listeners:{},attributes:{},files:[],addEventListener(n,f){this.listeners[n]=f;},setAttribute(n,v){this.attributes[n]=v;},click(){this.clicked=true;},contains(){return false;},focus(){this.focused=true;}};
 const root={window:{},document:{getElementById:id=>nodes[id],addEventListener(){}},navigator:{clipboard:{read}},File:class {constructor(parts,name,options){Object.assign(this,{parts,name,...options});}}};
 vm.runInNewContext(source,root);root.window.CompanionAttachments.install({addFiles:async files=>added.push(...files),canAdd:()=>enabled,onError:e=>errors.push(e),getContext:()=>context});
 return {nodes,errors,added,api:root.window.CompanionAttachments,disable(){enabled=false;},switch(){context='chat-b';}};
}
test('native image paste uses shared uploader and never fetches HTML images',()=>{
 const f=fixture();const file={type:'image/png'};let prevented=false;
 f.nodes.instruction.listeners.paste({clipboardData:{items:[{kind:'file',type:'image/png',getAsFile:()=>file}],getData:()=>''},preventDefault(){prevented=true;}});
 assert.equal(f.added[0],file);assert.equal(prevented,true);
 assert.equal(f.api.files({items:[],files:[],getData:()=>'<img src="https://private/">'}).length,0);
});
test('mixed text retains browser insertion and image items fall back to files',()=>{
 const f=fixture();let prevented=false;const file={type:'image/jpeg'};
 f.nodes.instruction.listeners.paste({clipboardData:{items:[{kind:'file',type:'image/jpeg',getAsFile:()=>null}],files:[file],getData:()=> 'caption'},preventDefault(){prevented=true;}});
 assert.equal(prevented,false);assert.equal(f.added[0],file);
});
test('explicit paste reads only on user action, reports permission failures, and rejects changed chat',async()=>{
 let count=0;const f=fixture(async()=>{count++;return [{types:['image/png'],getType:async()=>({})}];});
 assert.equal(count,0);await f.nodes['paste-photo'].listeners.click();assert.equal(count,1);assert.equal(f.added[0].type,'image/png');
 const denied=fixture(async()=>{throw new Error('permission');});await denied.nodes['paste-photo'].listeners.click();assert.match(denied.errors[0],/not allowed/);
 const switched=fixture(async()=>{switched.switch();return [{types:['image/png'],getType:async()=>({})}];});await switched.nodes['paste-photo'].listeners.click();assert.equal(switched.added.length,0);assert.match(switched.errors[0],/chat changed/);
});
test('offline paste cannot upload and plus menu keeps separate library and paste actions',()=>{
 const f=fixture();f.nodes['add-photo'].listeners.click();assert.equal(f.nodes['photo-menu'].hidden,false);f.nodes['choose-photo'].listeners.click();assert.equal(f.nodes['photo-input'].clicked,true);assert.equal(f.nodes['photo-menu'].hidden,true);
 f.disable();f.nodes.instruction.listeners.paste({clipboardData:{files:[{type:'image/png'}],getData:()=>''},preventDefault(){}});assert.equal(f.added.length,0);assert.match(f.errors[0],/Reconnect/);
});
