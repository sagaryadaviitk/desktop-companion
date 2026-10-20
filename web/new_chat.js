"use strict";
window.CompanionNewChat={install({api,post,preparePhoto,isConnected,onCreated,getVersion}){
 const el=id=>document.getElementById(id),dialog=el('new-chat-dialog'),input=el('new-chat-prompt'),target=el('new-chat-target');
 let photos=[],uploading=false,busy=false,loadingTargets=false,draftId=crypto.randomUUID(),attempt=null,targets=[],version=0,openRequest=0,createdResult=null;
 function result(message){el('new-chat-result').textContent=message;}
 function update(){
  const locked=busy||Boolean(attempt)||Boolean(createdResult);target.disabled=locked||loadingTargets||!targets.length;input.disabled=locked;
  el('new-chat-close').disabled=busy;el('new-chat-create').disabled=busy||loadingTargets||uploading||!isConnected()||Boolean(createdResult)&&!attempt||!attempt&&(!target.value||!input.value.trim()&&!photos.length);
  el('new-chat-create').textContent=busy?'Starting…':attempt?'Check previous request':'Start chat';
  el('new-add-photo').disabled=locked||uploading||photos.length>=3||!isConnected();
  const destination=targets.find(t=>t.id===target.value);el('new-chat-destination').textContent=loadingTargets?'Loading projects…':destination?'Starts in '+destination.name+(destination.id==='personal'?'': ' · '+destination.folder):'Choose a destination';
  const panel=el('new-photo-drafts');panel.replaceChildren();panel.hidden=!photos.length&&!uploading;
  for(const photo of photos){const card=document.createElement('div');card.className='photo-draft';const image=document.createElement('img');image.src=photo.url;image.alt=photo.name;const remove=document.createElement('button');remove.type='button';remove.textContent='×';remove.className='remove-photo';remove.disabled=locked;remove.setAttribute('aria-label','Remove '+photo.name);
   remove.addEventListener('click',async()=>{photos=photos.filter(p=>p.id!==photo.id);update();try{await post('/api/photos/remove',{draft_id:draftId,photo_id:photo.id});}catch(error){result(error.message);}});card.append(image,remove);panel.append(card);}
  if(uploading){const note=document.createElement('span');note.textContent='Adding photo…';note.className='photo-progress';panel.append(note);}
  el('new-chat-open').hidden=!createdResult?.thread_id;
 }
 function witness(value){try{if(value)sessionStorage.setItem('companion-new-chat',JSON.stringify({command_id:value.command_id}));else sessionStorage.removeItem('companion-new-chat');}catch(_){}}
 function reset(){version++;photos=[];uploading=false;busy=false;loadingTargets=false;attempt=null;createdResult=null;draftId=crypto.randomUUID();input.value='';el('new-chat-another').hidden=true;witness(null);result('');update();}
 async function open(projectKey=''){
  const previous=target.value,preserveDestination=Boolean(input.value.trim()||photos.length||attempt);
  dialog.showModal();loadingTargets=true;result('');update();const epoch=version,auth=getVersion(),request=++openRequest;
  try{
   const data=await api('/api/chat-targets');if(epoch!==version||request!==openRequest||auth!==getVersion()||!dialog.open)return;
   if(!data.available)throw new Error('New chat is unavailable while the desktop is disconnected.');
   targets=data.targets||[];target.replaceChildren();
   for(const choice of targets){const option=new Option(choice.name+(targets.filter(t=>t.project_key===choice.project_key).length>1?' · '+choice.folder:'')+(choice.available?'':' · Unavailable'),choice.id);option.disabled=!choice.available;target.append(option);}
   target.value=attempt?.target_id||(preserveDestination&&targets.some(t=>t.id===previous&&t.available)?previous:targets.find(t=>t.project_key===projectKey&&t.available)?.id||(!projectKey||projectKey==='personal'?'personal':''));
   if(!target.value)result('This project is unavailable for new chats. Choose another destination explicitly.');
   if(!attempt){let saved;try{saved=JSON.parse(sessionStorage.getItem('companion-new-chat'));}catch(_){}
    if(saved?.command_id){const previous=await api('/api/chats/creation?id='+encodeURIComponent(saved.command_id));if(epoch!==version||request!==openRequest||auth!==getVersion())return;createdResult=previous;result(previous.thread_id?'Your previous chat was created. Open it to check delivery before starting another.':previous.message);el('new-chat-another').hidden=false;}
   }
  }catch(error){if(epoch===version&&request===openRequest&&auth===getVersion()){targets=[];result(error.message);}}
  finally{if(epoch===version&&request===openRequest&&auth===getVersion()){loadingTargets=false;update();}}
 }
 async function addFiles(files){
  if(busy||attempt||createdResult||uploading||!isConnected())return;const epoch=version,auth=getVersion();uploading=true;update();
  try{for(const file of files){if(photos.length>=3){result('Attach up to three photos.');break;}const data_url=await preparePhoto(file);if(epoch!==version||auth!==getVersion())return;const photo=await post('/api/photos',{draft_id:draftId,name:file.name||'Pasted photo',data_url});if(epoch!==version||auth!==getVersion())return;photos.push(photo);update();}}
  catch(error){if(epoch===version)result(error.message);}finally{if(epoch===version){uploading=false;update();}}
 }
 CompanionAttachments.install({addFiles,canAdd:()=>isConnected()&&!busy&&!attempt&&!createdResult&&!uploading,onError:result,getContext:()=>getVersion()+':'+version+':'+draftId,ids:{'photo-menu':'new-photo-menu','add-photo':'new-add-photo','choose-photo':'new-choose-photo','photo-input':'new-photo-input','instruction':'new-chat-prompt','paste-photo':'new-paste-photo'}});
 input.addEventListener('input',()=>{result('');update();});target.addEventListener('change',update);
 el('new-chat-close').addEventListener('click',()=>{if(!busy)dialog.close();});dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
 el('new-chat-open').addEventListener('click',async()=>{if(!createdResult?.thread_id)return;dialog.close();await onCreated(createdResult,null);});
 el('new-chat-another').addEventListener('click',()=>{reset();el('new-chat-another').hidden=true;});
 el('new-chat-form').addEventListener('submit',async event=>{
  event.preventDefault();if(busy||loadingTargets||uploading||!isConnected()||createdResult?.thread_id&&!attempt)return;
  const epoch=version,auth=getVersion();attempt=attempt||{command_id:crypto.randomUUID(),target_id:target.value,message:input.value.trim(),photos:photos.map(p=>p.id),draft_id:draftId};
  witness(attempt);busy=true;update();result('Creating your chat and connecting to the desktop…');
  try{
   let response;
   for(let i=0;i<3;i++){
    response=await post('/api/chats/create',attempt);if(epoch!==version||auth!==getVersion())return;
    if(response.delivery?.state!=='pending')break;
    result(response.delivery.message);if(i<2)await new Promise(done=>setTimeout(done,1000));
   }
   createdResult=response;
   if(response.delivery?.state==='unknown'||response.delivery?.state==='rejected'||response.delivery?.state==='blocked'){
    result((response.delivery.message||'Delivery could not be confirmed.')+' Open the chat before sending again.');el('new-chat-another').hidden=false;return;
   }
   const draft=response.delivery?.state==='sent'?null:{message:attempt.message,photos:[...photos]};
   dialog.close();witness(null);await onCreated(response,draft);reset();el('new-chat-another').hidden=true;
  }catch(error){if(epoch===version&&auth===getVersion()){
   if(error.result?.state==='rejected'||error.status===400||error.status===403){attempt=null;witness(null);}
   result(error.message+' Your question and photos are kept. An unchanged retry checks the same request.');
  }}finally{if(epoch===version){busy=false;update();}}
 });
 update();return {open,reset,hasDraft:()=>Boolean(input.value.trim()||photos.length||attempt),isBusy:()=>busy||uploading};
}};
