const {test}=require('node:test');const assert=require('node:assert/strict');const vm=require('node:vm');const fs=require('node:fs');
function fixture(storage=new Map()){
 const nodes={'update-banner':{hidden:true},'update-reload':{addEventListener(n,f){this.click=f;}}};let draft=false,busy=false,interacting=false,reloads=0,confirmed=false,warnings=0;
 const context={window:{},document:{getElementById:id=>nodes[id]},sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)}};
 vm.runInNewContext(fs.readFileSync(__dirname+'/web/updates.js','utf8'),context);
 const updater=context.window.CompanionUpdates.install({loadedBuild:'a'.repeat(16),hasDraft:()=>draft,isBusy:()=>busy,isInteracting:()=>interacting,reload:()=>reloads++,confirmReload:()=>confirmed,onBusy:()=>warnings++});
 return {nodes,check:updater.check,reload:updater.reload,setDraft:v=>draft=v,setBusy:v=>busy=v,setInteracting:v=>interacting=v,confirm:()=>confirmed=true,reloads:()=>reloads,warnings:()=>warnings};
}
test('idle tabs update once per build even if stale responses persist across a reload',()=>{
 const storage=new Map(),f=fixture(storage);assert.equal(f.check('b'.repeat(16)),true);assert.equal(f.reloads(),1);f.check('b'.repeat(16));assert.equal(f.reloads(),1);
 const reloaded=fixture(storage);assert.equal(reloaded.check('b'.repeat(16)),false);assert.equal(reloaded.nodes['update-banner'].hidden,false);
});
test('drafts, uploads, operations, and focused inputs prevent automatic reload',()=>{
 for(const mode of ['setDraft','setBusy','setInteracting']){
  const f=fixture();f[mode](true);f.check('b'.repeat(16));assert.equal(f.reloads(),0);assert.equal(f.nodes['update-banner'].hidden,false);
  f[mode](false);assert.equal(f.check('b'.repeat(16)),true);
 }
});
test('manual update needs confirmation for a draft and waits for ongoing operations',()=>{
 const f=fixture();f.setBusy(true);f.nodes['update-reload'].click();assert.equal(f.reloads(),0);assert.equal(f.warnings(),1);
 f.setBusy(false);f.setDraft(true);f.nodes['update-reload'].click();assert.equal(f.reloads(),0);f.confirm();f.nodes['update-reload'].click();assert.equal(f.reloads(),1);
});
test('same, missing, and malformed builds never trigger a reload',()=>{
 const f=fixture();for(const build of ['a'.repeat(16),null,'bad','b'.repeat(100)]){assert.equal(f.check(build),false);assert.equal(f.nodes['update-banner'].hidden,true);}assert.equal(f.reloads(),0);
});

test('Home Screen Reload app uses the same draft and in-flight guards without needing an update banner',()=>{
 const f=fixture();f.setDraft(true);assert.equal(f.reload(),false);assert.equal(f.reloads(),0);f.confirm();assert.equal(f.reload(),true);assert.equal(f.reloads(),1);
 f.setBusy(true);assert.equal(f.reload(),false);assert.equal(f.reloads(),1);assert.equal(f.warnings(),1);
});
