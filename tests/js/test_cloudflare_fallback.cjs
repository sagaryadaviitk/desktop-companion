const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');
const template=fs.readFileSync(__dirname+'/../../cloudflare_fallback/worker.mjs.template','utf8');const html=fs.readFileSync(__dirname+'/../../cloudflare_fallback/offline.html','utf8');
const modulePromise=import('data:text/javascript;base64,'+Buffer.from(template.replace('__COMPANION_OFFLINE_HTML__',JSON.stringify(html))).toString('base64'));
test('unreachable HTML homepage gets static help and preserves failure status',async()=>{
 const worker=(await modulePromise).default,original=global.fetch;let calls=0;
 try{global.fetch=async()=>{calls++;return new Response('error 1033',{status:530});};const r=await worker.fetch(new Request('https://agents.example.com/?chat=private-id',{headers:{Accept:'text/html'}}));assert.equal(r.status,503);assert.equal(r.headers.get('Cache-Control'),'no-store, max-age=0');assert.match(await r.text(),/temporarily unreachable/);assert.equal(calls,1);}finally{global.fetch=original;}
});
test('API and mutation responses pass through unchanged with exactly one attempt',async()=>{
 const worker=(await modulePromise).default,original=global.fetch;
 try{for(const request of [new Request('https://agents.example.com/api/chats'),new Request('https://agents.example.com/api/command',{method:'POST',body:'private prompt'}),new Request('https://agents.example.com/',{method:'POST',body:'private prompt'})]){const expected=new Response('unconfirmed',{status:530});let calls=0;global.fetch=async received=>{assert.equal(received,request);calls++;return expected;};assert.equal(await worker.fetch(request),expected);assert.equal(calls,1);}}finally{global.fetch=original;}
});
test('healthy and authentication responses are neither replaced nor cached',async()=>{
 const worker=(await modulePromise).default,original=global.fetch;
 try{for(const status of [200,302,401,403]){const expected=new Response('original',{status});global.fetch=async(_,options)=>{assert.equal(options.cf.cacheTtl,0);return expected;};assert.equal(await worker.fetch(new Request('https://agents.example.com/',{headers:{Accept:'text/html'}})),expected);}}finally{global.fetch=original;}
});
test('static preview cannot forward private cookies or request bodies',async()=>{
 const worker=(await modulePromise).default,original=global.fetch;
 try{global.fetch=()=>{throw Error('Must never relay');};const r=await worker.fetch(new Request('https://companion.example.workers.dev/',{headers:{Cookie:'private-cookie'}}));const body=await r.text();assert.equal(r.status,503);assert.equal(body.includes('private-cookie'),false);assert.equal(body.includes('<script'),false);}finally{global.fetch=original;}
});
