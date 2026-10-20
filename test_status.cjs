const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
function fixture(){
 class Element{constructor(){this.dataset={};this.children=[];this.listeners={};this.open=false;}addEventListener(name,fn){this.listeners[name]=fn;}showModal(){this.open=true;}close(){this.open=false;}append(...items){this.children.push(...items);}replaceChildren(...items){this.children=items;}setAttribute(name,value){this[name]=value;}focus(){}}
 const nodes={};for(const id of ['system-dialog','system-summary','system-summary-title','system-summary-detail','system-checked','system-components','system-refresh','system-error','system-close','system-map','system-map-path','system-map-support','system-map-links','system-map-detail','system-map-detail-title','system-map-detail-state','system-map-detail-text','system-map-count','system-map-progress'])nodes[id]=new Element();
 let calls=[],version=1,fail=false,deferred=null,timer;
 const data={summary:{state:'warning',title:'Tunnel needs attention',detail:'Local work is available.'},components:[{id:'server',title:'Desktop service',state:'ready',detail:'Available',action:'restart'},{id:'tunnel',title:'Tunnel',state:'unexpected-value',detail:'Not verified'}],checked_at:1792516681};
 const context={window:{},document:{hidden:false,getElementById:id=>nodes[id],createElement:()=>new Element(),createElementNS:()=>new Element()},setInterval:fn=>timer=fn};
 vm.runInNewContext(fs.readFileSync(__dirname+'/web/status.js','utf8'),context);
 const flow=context.window.CompanionHealth.install({getVersion:()=>version,api:async(...args)=>{calls.push(args);if(deferred)await deferred;if(fail)throw new Error('Connection interrupted');return data;}});
 return {nodes,flow,calls,context,data,fail:()=>fail=true,tick:()=>timer(),pending:promise=>deferred=promise,signOut:()=>version++};
}
test('component checks run only while viewed, use only the read endpoint, and allowlist states',async()=>{
 const f=fixture();f.tick();assert.equal(f.calls.length,0);await f.flow.open();assert.deepEqual(f.calls,[['/api/components']]);assert.equal(f.nodes['system-components'].children.length,2);assert.equal(f.nodes['system-components'].children[1].dataset.state,'unknown');assert.equal(f.nodes['system-summary'].dataset.state,'warning');f.nodes['system-close'].listeners.click();f.tick();assert.equal(f.calls.length,1);
});
test('an interrupted check retains results but removes any claim of current healthy status',async()=>{
 const f=fixture();await f.flow.open();f.fail();await f.nodes['system-refresh'].listeners.click();assert.equal(f.nodes['system-summary-title'].textContent,'Last known status');assert.equal(f.nodes['system-components'].dataset.stale,'true');assert.equal(f.nodes['system-map'].dataset.stale,'true');assert.match(f.nodes['system-map-count'].textContent,/Last known/);assert.equal(f.nodes['system-components'].children.length,2);assert.equal(f.nodes['system-refresh'].disabled,false);assert.match(f.nodes['system-error'].textContent,/Connection interrupted/);
});
test('map nodes reveal the exact check without another request, and missing checks cannot count as ready',async()=>{
 const f=fixture();await f.flow.open();assert.equal(f.nodes['system-map-progress'].value,1);assert.equal(f.nodes['system-map-progress'].max,13);const tunnel=f.nodes['system-map-path'].children[2];tunnel.listeners.click();assert.equal(f.nodes['system-map-detail-title'].textContent,'Tunnel');assert.equal(f.nodes['system-map-detail-state'].textContent,'Unavailable');assert.equal(f.nodes['system-map-detail-text'].textContent,'Not verified');assert.equal(f.calls.length,1);assert.equal(tunnel['aria-pressed'],'true');assert.equal(f.nodes['system-map-links'].children.length,5);
});
test('map reflects a tunnel failure between healthy endpoints and retains unchanged node identity on refresh',async()=>{
 const f=fixture();f.data.components=['phone','cloudflare','tunnel','desktop','server','gateway','network','background','awake','lid','history','access','queue'].map(id=>({id,title:id,state:id==='tunnel'?'warning':'ready',detail:id+' check'}));await f.flow.open();assert.equal(f.nodes['system-map-count'].textContent,'12 / 13 ready');const tunnel=f.nodes['system-map-path'].children[2];assert.equal(f.nodes['system-map-links'].children[1].dataset.state,'warning');assert.equal(f.nodes['system-map-links'].children[3].dataset.state,'ready');await f.nodes['system-refresh'].listeners.click();assert.equal(f.nodes['system-map-path'].children[2],tunnel);f.flow.reset();assert.equal(f.nodes['system-map'].hidden,true);
});
test('logout clears component data and ignores an old in-flight reply',async()=>{
 const f=fixture();let finish;f.pending(new Promise(resolve=>finish=resolve));const opening=f.flow.open();f.signOut();f.flow.reset();finish();await opening;assert.equal(f.nodes['system-components'].children.length,0);assert.equal(f.nodes['system-summary-title'].textContent,'Checking your Mac');assert.equal(f.nodes['system-refresh'].disabled,false);
});
