const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
const context={window:{},URL,AbortController};vm.runInNewContext(fs.readFileSync(__dirname+'/web/media.js','utf8'),context);const media=context.window.CompanionMedia;
test('media reads time out without retrying and clear their deadline after JSON completes',async()=>{
 let timeout,cleared=0,calls=0;
 const fetcher=(_path,{signal})=>{calls++;return new Promise((_,reject)=>signal.addEventListener('abort',()=>reject(Object.assign(new Error('timeout'),{name:'AbortError'}))));};
 const pending=media.readJSON('/api/media',{fetcher,setTimer:fn=>{timeout=fn;return 1;},clearTimer:()=>cleared++});
 timeout();await assert.rejects(pending,{name:'AbortError'});assert.equal(calls,1);assert.equal(cleared,1);
 const result=await media.readJSON('/api/media',{fetcher:async()=>({ok:true,json:async()=>({items:[]})}),setTimer:()=>2,clearTimer:()=>cleared++});
 assert.equal(result.data.items.length,0);assert.equal(cleared,2);
});
test('media resources accept only exact same-origin allowlisted item endpoints',()=>{
 const origin='https://phone.example',id='ep012-wifi-pilot-v2';
 assert.equal(media.resource(`/api/media/${id}/video?v=123`,id,'video',origin),`/api/media/${id}/video?v=123`);
 assert.equal(media.resource(`${origin}/api/media/${id}/captions`,id,'captions',origin),`/api/media/${id}/captions`);
 for(const target of ['https://other.example/api/media/'+id+'/video','//other.example/api/media/'+id+'/video','file:///private/video.mp4','javascript:alert(1)','/api/media/other/video','/api/media/'+id+'/download','/api/media/'+id+'/video#secret','/api/media/'+id+'/video/../poster'])assert.equal(media.resource(target,id,'video',origin),null,target);
 for(const invalid of ['../private','a/b','CAPS','',null,'a'.repeat(81)])assert.equal(media.validId(invalid),false);
});
test('review facts stay readable for short clips and omit unknown values',()=>{
 assert.equal(media.duration(50),'0:50');assert.equal(media.duration(3599.7),'60:00');assert.equal(media.duration(-1),'');assert.equal(media.duration(null),'');
 assert.equal(media.fileSize(7998933),'7.6 MB');assert.equal(media.fileSize(1024),'1 KB');assert.equal(media.fileSize(undefined),'');
});
test('library entry point opens separately without touching the chat composer',()=>{
 const html=fs.readFileSync(__dirname+'/web/index.html','utf8'),link=html.match(/<a id="media-library-open"[^>]+>/)?.[0];
 assert.match(link,/href="\/media"/);assert.match(link,/target="_blank"/);assert.match(link,/rel="noopener noreferrer"/);
 const shell=fs.readFileSync(__dirname+'/web/media.html','utf8');assert.match(shell,/<video[^>]+controls[^>]+playsinline[^>]+preload="metadata"/);assert.doesNotMatch(shell,/<video[^>]+autoplay/);assert.doesNotMatch(shell,/EP012|Wi-Fi|\/Users\//);assert.doesNotMatch(shell,/<script(?![^>]+src=)[^>]*>/);
});
