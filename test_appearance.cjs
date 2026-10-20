const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
function appearance(saved=null,dark=false,blocked=false){
 const handlers={},media={matches:dark,addEventListener:(_,fn)=>media.change=fn},buttons=['light','dark','system'].map(value=>({dataset:{themeChoice:value},setAttribute(name,value){this[name]=value;},addEventListener(_,fn){this.click=fn;}}));
 const root={dataset:{}},meta={},note={},stored=new Map(saved?[['companion-appearance',saved]]:[]);
 const context={window:{matchMedia:()=>media,addEventListener:(name,fn)=>handlers[name]=fn},localStorage:{getItem:k=>{if(blocked)throw Error('Storage disabled');return stored.get(k);},setItem:(k,v)=>{if(blocked)throw Error('Storage disabled');stored.set(k,v);}},document:{documentElement:root,querySelector:()=>meta,querySelectorAll:()=>buttons,getElementById:()=>note,addEventListener:(name,fn)=>handlers[name]=fn}};
 vm.runInNewContext(fs.readFileSync(__dirname+'/web/appearance.js','utf8'),context);return {theme:context.window.CompanionAppearance,root,meta,note,stored,handlers,media,buttons};
}
test('system theme is applied before the page renders and follows device changes',()=>{
 const f=appearance(null,true);assert.equal(f.root.dataset.theme,'dark');assert.equal(f.meta.content,'#181818');f.handlers.DOMContentLoaded();assert.equal(f.buttons[2]['aria-pressed'],'true');f.media.matches=false;f.media.change();assert.equal(f.root.dataset.theme,'light');
});
test('explicit theme survives reload and system changes, and syncs across same-origin tabs',()=>{
 const f=appearance('dark');f.handlers.DOMContentLoaded();f.buttons[0].click();assert.equal(f.stored.get('companion-appearance'),'light');f.media.matches=true;f.media.change();assert.equal(f.root.dataset.theme,'light');assert.equal(appearance(f.stored.get('companion-appearance')).root.dataset.theme,'light');f.handlers.storage({key:'companion-appearance',newValue:'dark'});assert.equal(f.root.dataset.theme,'dark');
});
test('blocked storage and unknown preferences cannot break boot or set arbitrary theme values',()=>{
 const f=appearance('<private>',true,true);assert.equal(f.root.dataset.theme,'dark');assert.equal(f.theme.set('bad-value'),false);assert.equal(f.theme.set('light'),true);assert.equal(f.root.dataset.theme,'light');
});
const context={window:{}};vm.runInNewContext(fs.readFileSync(__dirname+'/web/status.js','utf8'),context);const status=context.window.CompanionStatus;
test('battery distinguishes plugged-in, actively charging, and low battery without hiding percentage',()=>{
 const base={source:'adapter',battery_percent:80,lid_closed:false};assert.equal(status.power(base).tone,'warning');assert.equal(status.power(base).charging,false);assert.equal(status.power({...base,charging:true}).tone,'positive');assert.equal(status.power({...base,charging:true}).charging,true);assert.equal(status.power({...base,battery_percent:19,charging:true}).tone,'danger');assert.equal(status.power({...base,battery_percent:20,source:'battery'}).tone,'neutral');assert.equal(status.power({...base,battery_percent:0}).battery,'Mac 0%');
});
test('unknown or disconnected telemetry does not report healthy battery or charging',()=>{
 assert.equal(status.power().battery,'Mac —');assert.equal(status.power({source:'battery',battery_percent:NaN}).tone,'neutral');assert.equal(status.power({source:'battery',battery_percent:70,charging:true}).charging,false);const stale=status.power({source:'adapter',battery_percent:50,charging:true},true);assert.equal(stale.tone,'neutral');assert.equal(stale.charging,false);assert.match(stale.title,/Last known/);
});
test('weekly colors use remaining allowance, with explicit low and stale boundaries',()=>{
 for(const [value,tone] of [[100,'positive'],[26,'positive'],[25,'warning'],[11,'warning'],[10,'danger'],[0,'danger'],[undefined,'neutral'],[-1,'neutral'],[101,'neutral']])assert.equal(status.allowance(value).tone,tone,String(value));assert.equal(status.allowance(99,true).tone,'neutral');
});
