const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function fixture({display=false,legacy=false,vibrate}={}){
 const frames=[],media={matches:display,addEventListener:(_,fn)=>media.change=fn},doc={documentElement:{dataset:{}},activeElement:null},navigator={standalone:legacy,vibrate};
 const root={window:{matchMedia:()=>media,innerHeight:800},document:doc,navigator,WeakMap,requestAnimationFrame:fn=>frames.push(fn)};
 vm.runInNewContext(fs.readFileSync(__dirname+'/web/shell.js','utf8'),root);return {ui:root.window.CompanionShell,doc,media,frames};
}
test('Home Screen mode detects display-mode and the iOS legacy flag and follows changes',()=>{
 for(const options of [{display:true},{legacy:true}]){const f=fixture(options);assert.equal(f.ui.standalone(),true);assert.equal(f.doc.documentElement.dataset.standalone,'true');}
 const f=fixture();assert.equal(f.ui.standalone(),false);f.media.matches=true;f.media.change();assert.equal(f.doc.documentElement.dataset.standalone,'true');
});
test('photo and polling updates do not repeatedly collapse an unchanged focused textarea',()=>{
 const f=fixture(),writes=[];let height='';const input={value:'',clientWidth:250,scrollHeight:90,style:{get height(){return height;},set height(value){height=value;writes.push(value);}}};
 f.ui.sizeInput(input);for(let i=0;i<10;i++)f.ui.sizeInput(input);assert.deepEqual(writes,['52px']);
 input.value='A caption';f.ui.sizeInput(input);assert.equal(height,'90px');const size=writes.length;f.ui.sizeInput(input);assert.equal(writes.length,size);
 input.clientWidth=180;input.scrollHeight=180;f.ui.sizeInput(input,120);assert.equal(height,'120px');
 input.value='';f.ui.sizeInput(input);assert.equal(height,'52px');
});
test('photo layout reanchors the current selection without moving focus or overwriting newer typing',()=>{
 const f=fixture(),ranges=[],input={isConnected:true,disabled:false,selectionStart:2,selectionEnd:4,selectionDirection:'forward',setSelectionRange:(...args)=>ranges.push(args)};
 f.doc.activeElement=input;f.ui.repaintCaret(input);input.selectionStart=6;input.selectionEnd=6;f.frames.shift()();assert.deepEqual(ranges,[[6,6,'forward']]);
 f.ui.repaintCaret(input);f.doc.activeElement={};f.frames.shift()();assert.equal(ranges.length,1);
 f.doc.activeElement=input;input.disabled=true;f.ui.repaintCaret(input);f.frames.shift()();assert.equal(ranges.length,1);
});
test('haptics use the supported vibration API and degrade safely when absent, rejected or disabled',()=>{
 assert.equal(fixture().ui.haptic(),false);assert.equal(fixture({vibrate:()=>false}).ui.haptic(),false);assert.equal(fixture({vibrate:()=>{throw Error('blocked');}}).ui.haptic(),false);
 const values=[],f=fixture({vibrate:n=>{values.push(n);return true;}});assert.equal(f.ui.haptic('start'),true);f.ui.haptic('complete');assert.deepEqual(values,[10,6]);
});
