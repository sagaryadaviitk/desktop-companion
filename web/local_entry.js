/* Read-only entry points; local chats never enter the desktop control catalog. */
(function(root){
 'use strict';
 function create({document,api,embedded=false}){
  const list=document.getElementById('home-local-chats'),note=document.getElementById('home-local-note'),fork=document.getElementById('chat-continue-local');
  let generation=0,busy=false;
  function url(key,value){const params=new URLSearchParams();if(embedded)params.set('embedded','1');if(key)params.set(key,value);return '/local'+(params.size?'?'+params.toString():'');}
  for(const id of ['local-chats-open','local-home-open'])document.getElementById(id).href=url();
  function select(id){fork.hidden=!id;fork.href=id?url('from',id):url();}
  function reset(){generation++;busy=false;list.replaceChildren();note.textContent='';select(null);}
  async function refresh(){
   if(busy)return;busy=true;const version=generation;
   try{
    const result=await api('/api/local/chats');if(version!==generation)return;
    const chats=(result.chats||[]).filter(chat=>typeof chat.id==='string'&&/^local-[a-zA-Z0-9-]+$/.test(chat.id)).slice(0,4);
    list.replaceChildren();
    for(const chat of chats){
     const link=document.createElement('a');link.className='local-recent-card';link.href=url('chat',chat.id);link.target='_blank';link.rel='noopener noreferrer';
     const title=document.createElement('strong');title.textContent=chat.title||'Local chat';
     const detail=document.createElement('span');detail.textContent=chat.status==='working'?'Exo · Working':'Exo · Saved on this Mac';
     link.append(title,detail);list.append(link);
    }
    note.textContent=chats.length?'The same local chats are available on your Mac and paired phone.':'Start a local chat to use Exo on this Mac.';
   }catch(_error){if(version===generation)note.textContent='Local chats could not be loaded. Open Local chats to check Exo.';}
   finally{if(version===generation)busy=false;}
  }
  return {refresh,reset,select};
 }
 root.CompanionLocalEntry={create};
})(typeof window!=='undefined'?window:globalThis);
