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
  const chatID=new URLSearchParams(location.search).get('chat')||'';
  let selected=null,selectionRevision='',tabs=[],frame=null,csrf='',busy=false,selecting=false,reading=false,listing=false,version=0,retryAt=0,review=false,updating=false,blob=null,gesture=null,readyAt=0,lastCatalog=0,scale=1;
  el('chat-back').href=chatID?'/?chat='+encodeURIComponent(chatID):'/';
  el('back-label').textContent=chatID?'Chat':'Companion';
  if(chatID){el('heading').textContent='Browser for this chat';el('context-detail').textContent='Your chat and draft stay open in the other tab. Choose its desktop page below, then tap, scroll, zoom or type.';}
  function notice(message,error=false){el('notice').textContent=message;el('notice').hidden=!message;el('notice').classList.toggle('error',error);}
  function controls(){
   const enabled=Boolean(frame&&!busy&&!selecting&&!review&&!updating&&Date.now()-readyAt<7000);
   for(const button of document.querySelectorAll('[data-control]'))button.disabled=!enabled;
   el('tabs').disabled=busy||selecting||updating;
   el('viewport').classList.toggle('stale',!enabled);
   el('freshness').textContent=updating?'Update available':selecting?'Selecting page…':busy?'Applying action…':review?'Check page before continuing':enabled?'Live view':selected?'View outdated':'Choose a page';
  }
  function result(message,error=false){el('action-result').textContent=message;el('action-result').classList.toggle('error',error);}
  function imageSize(){screen.style.width='min('+(scale*100)+'%, calc(var(--view-height) * '+(scale*(frame?frame.width/frame.height:1.6))+'))';}
  function zoom(value){
   gesture=null;scale=Math.max(1,Math.min(4,value));imageSize();
   el('viewport').classList.toggle('zoomed',scale>1);el('zoom').textContent=scale===1?'Fit':scale+'×';
   el('zoom').setAttribute('aria-pressed',String(scale>1));el('zoom-hint').hidden=scale===1;
   el('zoom-out').disabled=scale===1;el('zoom-in').disabled=scale===4;
   if(scale===1){el('viewport').scrollLeft=0;el('viewport').scrollTop=0;}
  }
  function clearImage(){gesture=null;frame=null;readyAt=0;screen.hidden=true;screen.removeAttribute('src');if(blob)URL.revokeObjectURL(blob);blob=null;el('page-title').textContent='';el('desktop-size').textContent='';controls();}
  function lock(){version++;selected=null;selectionRevision='';tabs=[];clearImage();el('workspace').hidden=true;el('empty').hidden=true;el('pair').hidden=false;el('text').value='';el('address').value='';notice('Pair this browser with your Mac to view its pages.');poll.pause();}
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
   if(!selected||reading||busy||selecting||updating||gesture)return true;
   const current=selected,revision=selectionRevision,epoch=version,began=Date.now();let pendingBlob=null;reading=true;
   try{
    const query='?id='+encodeURIComponent(current)+(chatID?'&chat='+encodeURIComponent(chatID):'')+'&selection_revision='+encodeURIComponent(revision);
    const data=await request('/api/browser/frame'+query);
    if(current!==selected||revision!==selectionRevision||epoch!==version)return true;
    if(data.id!==current||data.chat_id!==chatID||data.selection_revision!==revision||typeof data.frame!=='string'||typeof data.image!=='string'||data.image.length>7100000||!['image/jpeg','image/png'].includes(data.image_mime||'image/jpeg')||!Number.isFinite(data.width)||data.width<=0||!Number.isFinite(data.height)||data.height<=0)throw Error('The page image did not match this chat’s selected desktop page.');
    const bytes=Uint8Array.from(atob(data.image),c=>c.charCodeAt(0)),next=pendingBlob=URL.createObjectURL(new Blob([bytes],{type:data.image_mime||'image/jpeg'}));
    await new Promise((resolve,reject)=>{const image=new Image();image.onload=resolve;image.onerror=()=>reject(Error('This page image could not be displayed.'));image.src=next;});
    if(current!==selected||revision!==selectionRevision||epoch!==version||gesture){URL.revokeObjectURL(next);pendingBlob=null;return true;}
    const prior=blob;blob=next;pendingBlob=null;screen.src=blob;screen.hidden=false;el('loading').hidden=true;frame=data;readyAt=began;imageSize();
    if(prior)URL.revokeObjectURL(prior);el('page-title').textContent=data.title;el('desktop-size').textContent=data.width+' × '+data.height+' on Mac';
    if(document.activeElement!==el('address'))el('address').value=data.url;
    notice('');controls();return true;
   }catch(error){if(epoch===version&&current===selected){frame=null;lastCatalog=0;controls();notice(error.message+' Refresh to try again.',true);}return false;}
   finally{if(pendingBlob)URL.revokeObjectURL(pendingBlob);reading=false;}
  }
  function catalog(data){
   if(data.build&&data.build!==loadedBuild){updating=true;controls();el('reload-app').hidden=false;notice('An interface update is ready. Reload when your typed text is safe.');return false;}
   if(chatID&&data.chat?.id!==chatID)throw Error('This chat could not be confirmed. Return to your chat and open its browser again.');
   if(chatID){const title=data.chat.title||'this chat';el('context-title').textContent=title;el('heading').textContent='Browser · '+title;document.title='Browser · '+title;}
   csrf=data.csrf||'';lastCatalog=Date.now();el('pair').hidden=true;
   tabs=Array.isArray(data.tabs)?data.tabs.filter(t=>typeof t.id==='string'&&typeof t.title==='string'&&typeof t.url==='string'):[];
   el('empty').hidden=Boolean(data.available&&tabs.length);el('workspace').hidden=!data.available||!tabs.length;
   const choice=data.selection;
   const chosen=data.available&&choice?.available===true&&choice.source==='manual'&&typeof choice.revision==='string'&&choice.revision&&tabs.some(tab=>tab.id===choice.id)?choice.id:null;
   const revision=chosen?choice.revision:'';
   if(chosen!==selected||revision!==selectionRevision){selected=chosen;selectionRevision=revision;version++;review=false;clearImage();el('text').value='';el('address').value='';el('loading').hidden=false;zoom(1);}
   if(!data.available||!tabs.length){
    el('empty-title').textContent=data.available?'No desktop pages are open':'Finish browser setup on your Mac';
    el('empty-detail').textContent=data.available?'Open a web page in the desktop app’s browser, then refresh this list.':data.reason||'Browser sharing has not been enabled on this Mac.';
    notice('');controls();return false;
   }
   const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent='Choose a desktop page…';placeholder.disabled=true;
   el('tabs').replaceChildren(placeholder,...tabs.map(tab=>{const option=document.createElement('option');option.value=tab.id;option.textContent=tab.title+' · '+tab.url;return option;}));el('tabs').value=chosen||'';
   el('selection-note').textContent=chosen?(chatID?'You selected this page for this chat.':'You selected this desktop page.'):(choice?.available===false?'Your selected page is no longer open. Choose a page to continue.':chatID?'Choose the desktop page you want to use with this chat.':'Choose the desktop page you want to view.');
   el('loading').textContent=chosen?'Loading the desktop view…':'Choose a page above to view it here.';
   if(!chosen){clearImage();el('loading').hidden=false;notice('');}controls();return true;
  }
  async function list(){
   if(listing||selecting)return false;listing=true;const epoch=version;
   try{
    const data=await request('/api/browser'+(chatID?'?chat='+encodeURIComponent(chatID):''));if(epoch!==version)return false;
    if(!catalog(data))return false;
    return selected?await readFrame():true;
   }catch(error){if(epoch===version){frame=null;controls();notice(error.message+' Refresh to reconnect.',true);}return false;}
   finally{listing=false;}
  }
  async function choose(){
   if(busy||selecting||updating){el('tabs').value=selected||'';return;}
   const id=el('tabs').value;
   if(!tabs.some(tab=>tab.id===id)){el('tabs').value=selected||'';return;}
   const epoch=++version;selected=null;selectionRevision='';review=false;selecting=true;clearImage();el('text').value='';el('address').value='';el('loading').hidden=false;el('loading').textContent='Selecting the desktop page…';controls();result('');
   try{
    const data=await request('/api/browser/select',{chat_id:chatID,id});if(epoch!==version)return;
    catalog(data);
   }catch(error){if(epoch===version){lastCatalog=0;result(error.message+' Refresh the page list to check your selection.',true);}}
   finally{selecting=false;controls();}
   if(selected)await readFrame();
  }
  async function act(action){
   if(!frame||busy||selecting||review||updating||Date.now()-readyAt>=7000){controls();return;}
   const current=selected,revision=selectionRevision,epoch=++version,receipt=frame;busy=true;frame=null;controls();result('Applying to the page on your Mac…');
   try{
    const response=await request('/api/browser/action',{id:current,frame:receipt.frame,chat_id:chatID,selection_revision:revision,...action});
    if(current!==selected||revision!==selectionRevision||epoch!==version)return;
    if(action.action==='text'&&el('text').value===action.text)el('text').value='';result(response.message||'Action acknowledged.');
   }catch(error){if(current===selected&&epoch===version){review=error.uncertain||(!error.rejected&&!error.update);if(error.update){updating=true;el('reload-app').hidden=false;}result(error.message+(review?' Check the page, then tap Refresh to continue.':''),true);}}
   finally{busy=false;if(current===selected&&epoch===version){await readFrame();controls();}}
  }
  const enabled=()=>!document.hidden&&navigator.onLine!==false&&el('pair').hidden;
  const poll=CompanionConversation.poll({enabled,read:()=>selected&&Date.now()-lastCatalog<10000?readFrame():list(),delay:()=>selected?1500:10000,retryAfter:()=>Math.max(0,retryAt-Date.now())});
  const clock=setInterval(controls,1000);
  el('refresh').addEventListener('click',async()=>{if(busy||selecting)return;review=false;await list();controls();});
  el('tabs').addEventListener('change',choose);
  el('zoom').addEventListener('click',()=>zoom(scale===1?2:1));
  el('zoom-in').addEventListener('click',()=>zoom(scale+.5));el('zoom-out').addEventListener('click',()=>zoom(scale-.5));
  el('focus-view').addEventListener('click',()=>{gesture=null;const focused=document.body.classList.toggle('view-focused');el('focus-view').setAttribute('aria-pressed',String(focused));el('focus-view').textContent=focused?'Exit focus':'Focus view';});
  el('navigation').addEventListener('submit',event=>{event.preventDefault();act({action:'navigate',url:el('address').value.trim()});});
  for(const name of ['back','forward'])el(name).addEventListener('click',()=>act({action:name}));
  el('reload-page').addEventListener('click',()=>act({action:'reload'}));
  for(const [id,direction]of [['scroll-up',-1],['scroll-down',1]])el(id).addEventListener('click',()=>act({action:'scroll',dx:0,dy:direction*Math.min(1200,(frame?.height||600)*.7)}));
  el('typing-form').addEventListener('submit',event=>{event.preventDefault();if(el('text').value)act({action:'text',text:el('text').value});});
  el('show-text').addEventListener('click',()=>{const show=el('text').type==='password';el('text').type=show?'text':'password';el('show-text').textContent=show?'Hide':'Show';el('show-text').setAttribute('aria-pressed',String(show));});
  for(const button of document.querySelectorAll('[data-key]'))button.addEventListener('click',()=>act({action:'key',key:button.dataset.key}));
  screen.addEventListener('pointerdown',event=>{if(!frame||busy||review||!event.isPrimary)return;gesture={x:event.clientX,y:event.clientY,id:event.pointerId,frame:frame.frame,version,pan:scale>1,left:el('viewport').scrollLeft||0,top:el('viewport').scrollTop||0};screen.setPointerCapture(event.pointerId);});
  screen.addEventListener('pointermove',event=>{if(!gesture||gesture.id!==event.pointerId||!gesture.pan)return;el('viewport').scrollLeft=gesture.left+gesture.x-event.clientX;el('viewport').scrollTop=gesture.top+gesture.y-event.clientY;});
  screen.addEventListener('pointerup',event=>{if(!gesture||gesture.id!==event.pointerId)return;const start=gesture;gesture=null;if(start.pan&&Math.hypot(event.clientX-start.x,event.clientY-start.y)>=14)return;if(start.version!==version||start.frame!==frame?.frame){result('The page changed. Refresh and tap again.',true);return;}act(CompanionBrowser.gesture(start,{x:event.clientX,y:event.clientY},screen.getBoundingClientRect(),frame?.height||600));});
  screen.addEventListener('pointercancel',()=>{gesture=null;});screen.addEventListener('dragstart',event=>event.preventDefault());
  document.addEventListener('visibilitychange',()=>{if(document.hidden){poll.pause();version++;gesture=null;frame=null;if(busy){review=true;result('An action was in progress. Check the page and tap Refresh before continuing.',true);}controls();}else poll.wake();});
  window.addEventListener('offline',()=>{poll.pause();version++;gesture=null;frame=null;if(busy){review=true;result('An action may have run. Check the page and tap Refresh before continuing.',true);}controls();notice('Offline. The last view is kept; controls resume after a fresh check.');});window.addEventListener('online',()=>poll.wake());
  window.addEventListener('pagehide',()=>{poll.stop();clearInterval(clock);el('text').value='';clearImage();});
  window.addEventListener('pageshow',event=>{if(event.persisted)location.reload();});
  el('reload-app').addEventListener('click',()=>{if(busy||selecting)return;if(el('text').value&&!window.confirm('Reload and discard the text entered below the page?'))return;location.reload();});
  zoom(1);controls();list();
 }
};
if(typeof document!=='undefined')document.addEventListener('DOMContentLoaded',()=>CompanionBrowser.install(),{once:true});
