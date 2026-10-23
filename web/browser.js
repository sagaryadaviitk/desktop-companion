"use strict";
window.CompanionBrowser={
 point(rect,x,y){return {x:Math.max(0,Math.min(1,(x-rect.left)/rect.width)),y:Math.max(0,Math.min(1,(y-rect.top)/rect.height))};},
 gesture(start,end,rect,height){
  const dx=end.x-start.x,dy=end.y-start.y;
  if(Math.hypot(dx,dy)<14)return {action:'click',...this.point(rect,end.x,end.y)};
  return {action:'scroll',x:.5,y:.5,dx:Math.max(-1500,Math.min(1500,-dx*height/rect.height)),dy:Math.max(-1500,Math.min(1500,-dy*height/rect.height))};
 },
 install(){
  const el=id=>document.getElementById(id),screen=el('screen'),loadedBuild=document.querySelector('meta[name=companion-build]').content;
  let selected=null,frame=null,csrf='',busy=false,reading=false,listing=false,version=0,retryAt=0,review=false,updating=false,blob=null,gesture=null,readyAt=0,lastCatalog=0;
  function notice(message,error=false){el('notice').textContent=message;el('notice').hidden=!message;el('notice').classList.toggle('error',error);}
  function controls(){const enabled=Boolean(frame&&!busy&&!review&&!updating&&Date.now()-readyAt<7000);for(const button of document.querySelectorAll('[data-control]'))button.disabled=!enabled;el('viewport').classList.toggle('stale',!enabled);el('freshness').textContent=updating?'Update available':busy?'Applying action…':review?'Check page before continuing':enabled?'Live view':'View outdated';}
  function result(message,error=false){el('action-result').textContent=message;el('action-result').classList.toggle('error',error);}
  function clearImage(){gesture=null;frame=null;readyAt=0;screen.hidden=true;screen.removeAttribute('src');if(blob)URL.revokeObjectURL(blob);blob=null;controls();}
  function lock(){version++;selected=null;clearImage();el('workspace').hidden=true;el('empty').hidden=true;el('pair').hidden=false;el('text').value='';el('address').value='';notice('Pair this browser with your Mac to view its pages.');poll.pause();}
  async function request(path,payload){
   const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),payload?15000:12000);
   try{
    const response=await fetch(path,{cache:'no-store',credentials:'same-origin',signal:controller.signal,...(payload?{method:'POST',headers:{'Content-Type':'application/json','X-Companion-CSRF':csrf,'X-Companion-UI':loadedBuild},body:JSON.stringify(payload)}:{})});
    const data=await response.json().catch(()=>null);
    if(response.status===401){lock();throw Error('Your browser pairing expired.');}
    if(!response.ok){if([429,503].includes(response.status)){const wait=Number(response.headers.get('Retry-After'));if(Number.isFinite(wait))retryAt=Date.now()+Math.max(0,Math.min(60000,wait*1000));}throw Object.assign(Error(data?.error||'The page could not be reached.'),{uncertain:data?.state==='unknown',rejected:data?.state==='rejected',update:data?.update_required});}
    if(!data)throw Error('The page returned an unexpected response.');return data;
   }catch(error){if(error.name==='AbortError')throw Object.assign(Error('The browser connection timed out.'),{uncertain:Boolean(payload)});throw error;}
   finally{clearTimeout(timer);}
  }
  async function readFrame(){
   if(!selected||reading||busy||updating||gesture)return true;
   const current=selected,epoch=version,began=Date.now();let pendingBlob=null;reading=true;
   try{
    const data=await request('/api/browser/frame?id='+encodeURIComponent(current));
    if(current!==selected||epoch!==version)return true;
    if(data.id!==current||typeof data.frame!=='string'||typeof data.image!=='string'||data.image.length>7100000||!['image/jpeg','image/png'].includes(data.image_mime||'image/jpeg'))throw Error('The page image was invalid.');
    const bytes=Uint8Array.from(atob(data.image),c=>c.charCodeAt(0)),next=pendingBlob=URL.createObjectURL(new Blob([bytes],{type:data.image_mime||'image/jpeg'}));
    await new Promise((resolve,reject)=>{const image=new Image();image.onload=resolve;image.onerror=()=>reject(Error('This page image could not be displayed.'));image.src=next;});
    if(current!==selected||epoch!==version||gesture){URL.revokeObjectURL(next);pendingBlob=null;return true;}
    const prior=blob;blob=next;pendingBlob=null;screen.src=blob;screen.hidden=false;el('loading').hidden=true;frame=data;readyAt=began;
    if(prior)URL.revokeObjectURL(prior);el('page-title').textContent=data.title; if(document.activeElement!==el('address'))el('address').value=data.url;
    notice('');controls();return true;
   }catch(error){if(epoch===version&&current===selected){frame=null;controls();notice(error.message+' Refresh to try again.',true);}return false;}
   finally{if(pendingBlob)URL.revokeObjectURL(pendingBlob);reading=false;}
  }
  async function list(){
   if(listing)return false;listing=true;const epoch=version;
   try{
    const data=await request('/api/browser');if(epoch!==version)return false;
    if(data.build&&data.build!==loadedBuild){updating=true;controls();el('reload-app').hidden=false;notice('An interface update is ready. Reload when your typed text is safe.');return false;}
    csrf=data.csrf||'';lastCatalog=Date.now();el('pair').hidden=true;
    const tabs=Array.isArray(data.tabs)?data.tabs.filter(t=>typeof t.id==='string'&&typeof t.title==='string'&&typeof t.url==='string'):[];
    el('empty').hidden=Boolean(data.available&&tabs.length);el('workspace').hidden=!data.available||!tabs.length;
    if(!data.available||!tabs.length){version++;selected=null;clearImage();el('empty-title').textContent=data.available?'No desktop pages are open':'Finish browser setup on your Mac';el('empty-detail').textContent=data.available?'Open a web page in the desktop app’s browser, then refresh this list.':data.reason||'Browser sharing has not been enabled on this Mac.';notice('');return false;}
    const chosen=tabs.some(t=>t.id===selected)?selected:tabs[0].id;
    el('tabs').replaceChildren(...tabs.map(tab=>{const option=document.createElement('option');option.value=tab.id;option.textContent=tab.title;return option;}));el('tabs').value=chosen;
    if(chosen!==selected){selected=chosen;version++;review=false;clearImage();el('text').value='';el('loading').hidden=false;}
    return await readFrame();
   }catch(error){if(epoch===version){frame=null;controls();notice(error.message+' Refresh to reconnect.',true);}return false;}
   finally{listing=false;}
  }
  async function act(action){
   if(!frame||busy||review||updating||Date.now()-readyAt>=7000){controls();return;}
   const current=selected,epoch=++version,receipt=frame;busy=true;frame=null;controls();result('Applying to the page on your Mac…');
   try{
    const response=await request('/api/browser/action',{id:current,frame:receipt.frame,...action});
    if(current!==selected||epoch!==version)return;
    if(action.action==='text')el('text').value='';result(response.message||'Action acknowledged.');
   }catch(error){if(current===selected&&epoch===version){review=error.uncertain||(!error.rejected&&!error.update);if(error.update){updating=true;el('reload-app').hidden=false;}result(error.message+(review?' Check the page, then tap Refresh to continue.':''),true);}}
   finally{busy=false;if(current===selected&&epoch===version){await readFrame();controls();}}
  }
  const enabled=()=>!document.hidden&&navigator.onLine!==false&&el('pair').hidden;
  const poll=CompanionConversation.poll({enabled,read:()=>selected&&Date.now()-lastCatalog<10000?readFrame():list(),delay:()=>selected?1500:10000,retryAfter:()=>Math.max(0,retryAt-Date.now())});
  const clock=setInterval(controls,1000);
  el('refresh').addEventListener('click',async()=>{if(busy)return;review=false;await list();controls();});
  el('tabs').addEventListener('change',()=>{selected=el('tabs').value;version++;review=false;clearImage();el('text').value='';el('loading').hidden=false;readFrame();});
  el('zoom').addEventListener('click',()=>{const zoomed=el('viewport').classList.toggle('zoomed');el('zoom').setAttribute('aria-pressed',String(zoomed));el('zoom-hint').hidden=!zoomed;});
  el('navigation').addEventListener('submit',event=>{event.preventDefault();act({action:'navigate',url:el('address').value.trim()});});
  for(const name of ['back','forward'])el(name).addEventListener('click',()=>act({action:name}));
  el('reload-page').addEventListener('click',()=>act({action:'reload'}));
  for(const [id,direction]of [['scroll-up',-1],['scroll-down',1]])el(id).addEventListener('click',()=>act({action:'scroll',dx:0,dy:direction*Math.min(1200,(frame?.height||600)*.7)}));
  el('typing-form').addEventListener('submit',event=>{event.preventDefault();if(el('text').value)act({action:'text',text:el('text').value});});
  el('show-text').addEventListener('click',()=>{const show=el('text').type==='password';el('text').type=show?'text':'password';el('show-text').textContent=show?'Hide':'Show';el('show-text').setAttribute('aria-pressed',String(show));});
  for(const button of document.querySelectorAll('[data-key]'))button.addEventListener('click',()=>act({action:'key',key:button.dataset.key}));
  screen.addEventListener('pointerdown',event=>{if(!frame||busy||review||!event.isPrimary)return;gesture={x:event.clientX,y:event.clientY,id:event.pointerId,frame:frame.frame,version,pan:el('viewport').classList.contains('zoomed'),left:el('viewport').scrollLeft||0,top:el('viewport').scrollTop||0};screen.setPointerCapture(event.pointerId);});
  screen.addEventListener('pointermove',event=>{if(!gesture||gesture.id!==event.pointerId||!gesture.pan)return;el('viewport').scrollLeft=gesture.left+gesture.x-event.clientX;el('viewport').scrollTop=gesture.top+gesture.y-event.clientY;});
  screen.addEventListener('pointerup',event=>{if(!gesture||gesture.id!==event.pointerId)return;const start=gesture;gesture=null;if(start.pan&&Math.hypot(event.clientX-start.x,event.clientY-start.y)>=14)return;if(start.version!==version||start.frame!==frame?.frame){result('The page changed. Refresh and tap again.',true);return;}act(CompanionBrowser.gesture(start,{x:event.clientX,y:event.clientY},screen.getBoundingClientRect(),frame?.height||600));});
  screen.addEventListener('pointercancel',()=>{gesture=null;});screen.addEventListener('dragstart',event=>event.preventDefault());
  document.addEventListener('visibilitychange',()=>{if(document.hidden){poll.pause();version++;gesture=null;frame=null;if(busy){review=true;result('An action was in progress. Check the page and tap Refresh before continuing.',true);}controls();}else poll.wake();});
  window.addEventListener('offline',()=>{poll.pause();version++;gesture=null;frame=null;if(busy){review=true;result('An action may have run. Check the page and tap Refresh before continuing.',true);}controls();notice('Offline. The last view is kept; controls resume after a fresh check.');});window.addEventListener('online',()=>poll.wake());
  window.addEventListener('pagehide',()=>{poll.stop();clearInterval(clock);el('text').value='';clearImage();});
  window.addEventListener('pageshow',event=>{if(event.persisted)location.reload();});
  el('reload-app').addEventListener('click',()=>{if(busy)return;if(el('text').value&&!window.confirm('Reload and discard the text entered below the page?'))return;location.reload();});
  controls();list();
 }
};
if(typeof document!=='undefined')document.addEventListener('DOMContentLoaded',()=>CompanionBrowser.install(),{once:true});
