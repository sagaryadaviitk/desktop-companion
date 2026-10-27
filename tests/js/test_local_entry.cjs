const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
 constructor(){this.children=[];this.hidden=false;this.textContent='';}
 append(...children){this.children.push(...children);}replaceChildren(...children){this.children=children;}
}
function fixture(api,embedded=false){
 const ids=['home-local-chats','home-local-note','chat-continue-local','local-chats-open','local-home-open'];
 const elements=Object.fromEntries(ids.map(id=>[id,new Element()]));
 const document={getElementById:id=>elements[id],createElement:()=>new Element()};
 const context={window:{},URLSearchParams};vm.runInNewContext(fs.readFileSync(__dirname+'/../../web/local_entry.js','utf8'),context);
 return {ui:context.window.CompanionLocalEntry.create({document,api,embedded}),elements};
}
test('recent local chats have safe direct links and literal titles without entering native controls',async()=>{
 const calls=[];const {ui,elements:e}=fixture(async path=>{calls.push(path);return {chats:[{id:'local-123',title:'<script>literal</script>',status:'working'},{id:'javascript:alert(1)',title:'Invalid'}]};});
 await ui.refresh();assert.deepEqual(calls,['/api/local/chats']);const [card]=e['home-local-chats'].children;
 assert.equal(e['home-local-chats'].children.length,1);assert.equal(card.href,'/local?chat=local-123');assert.equal(card.children[0].textContent,'<script>literal</script>');assert.equal(card.target,'_blank');assert.equal(card.rel,'noopener noreferrer');
 ui.select('native-source');assert.equal(e['chat-continue-local'].href,'/local?from=native-source');assert.equal(e['chat-continue-local'].hidden,false);
 ui.select(null);assert.equal(e['chat-continue-local'].hidden,true);
});
test('embedded navigation preserves the native host destination',async()=>{
 const {ui,elements:e}=fixture(async()=>({chats:[{id:'local-one'}]}),true);await ui.refresh();ui.select('native/id?');
 assert.equal(e['local-chats-open'].href,'/local?embedded=1');assert.equal(e['home-local-chats'].children[0].href,'/local?embedded=1&chat=local-one');assert.equal(e['chat-continue-local'].href,'/local?embedded=1&from=native%2Fid%3F');
});
test('sign-out invalidates an in-flight catalog without restoring private titles',async()=>{
 let finish;const {ui,elements:e}=fixture(()=>new Promise(resolve=>finish=resolve));const request=ui.refresh();ui.reset();finish({chats:[{id:'local-private',title:'Private'}]});await request;
 assert.equal(e['home-local-chats'].children.length,0);assert.equal(e['home-local-note'].textContent,'');assert.equal(e['chat-continue-local'].hidden,true);
});
test('local service failure remains contained and a later catalog refresh recovers',async()=>{
 let failed=true;const {ui,elements:e}=fixture(async()=>{if(failed)throw Error('Unavailable');return {chats:[]};});await ui.refresh();assert.match(e['home-local-note'].textContent,/could not be loaded/);failed=false;await ui.refresh();assert.match(e['home-local-note'].textContent,/Start a local chat/);
});
