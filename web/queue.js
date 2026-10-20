"use strict";
// Native queue controls. Editing keeps the existing entry and its attachments.
window.CompanionQueue={install({request,steer,refresh,onResult,isConnected,getChat,document:doc=document}){
 const el=id=>doc.getElementById(id),dialog=el('queue-dialog'),form=el('queue-form'),input=el('queue-message'),save=el('queue-save'),error=el('queue-error');
 const busy=new Set(),attempts=new Map();let editing=null,expanded=false;
 const make=(tag,text,cls)=>{const node=doc.createElement(tag);if(text)node.textContent=text;if(cls)node.className=cls;return node;};
 function photos(items){const gallery=make('div','','queued-photos');for(const [index,photo] of (items||[]).entries()){const img=make('img');img.src=photo.url;img.alt=`Photo ${index+1}: ${photo.name||'Attached photo'}`;img.loading='lazy';gallery.append(img);}return gallery;}
 function display(item){
  try{const raw=item.text.trim();if(raw.startsWith('<send_user_message_question_reply>')&&raw.endsWith('</send_user_message_question_reply>')){const answers=JSON.parse(raw.slice(34,-35));if(Array.isArray(answers))return answers.map(a=>a.question+'\nReply: '+a.answer).join('\n\n');}}catch(_){}
  return item.text;
 }
 function open(item,data,operation){
  if(!isConnected()||busy.has(item.id))return;
  editing={item,thread:data.id,operation};input.value=operation==='edit'?item.editable_text:display(item);input.readOnly=operation==='delete';input.maxLength=item.answer?4000:8000;
  el('queue-heading').textContent=operation==='edit'?(item.answer?'Edit queued reply':'Edit queued message'):'Delete queued message?';
  el('queue-help').textContent=operation==='edit'?'Save changes to this message in the desktop queue. Any attached photos stay with it.':'Remove this message from the queue before it is sent.';
  el('queue-edit-photos').replaceChildren(photos(item.photos));error.textContent='';save.textContent=operation==='edit'?'Save changes':'Delete message';save.dataset.kind=operation;
  save.disabled=false;el('queue-cancel').disabled=false;dialog.showModal();if(operation==='edit')input.focus();
 }
 function render(data){
  if(editing&&editing.thread!==data.id&&!busy.size){dialog.close();editing=null;}
  const items=data.pending_instructions||[];if(!items.length)return null;
  const details=make('details');details.open=expanded;details.addEventListener('toggle',()=>{expanded=details.open;});
  const count=items.reduce((sum,item)=>sum+(item.attachment_count??item.photos?.length??0),0);
  details.append(make('summary',`${items.length} queued message${items.length===1?'':'s'}${count?' · '+count+' photo'+(count===1?'':'s'):''}`));
  const content=make('div','','pending-content');
  for(const item of items){
   const row=make('section','','queued-row');row.append(make('p',display(item),'pending-text'));
   if(item.photos?.length)row.append(photos(item.photos));
   const total=item.attachment_count??item.photos?.length??0;
   if(total)row.append(make('span',`${total} photo${total===1?'':'s'} attached${total>(item.photos?.length||0)?' · preview available on Mac':''}`,'queue-attachment-note'));
   const actions=make('div','','queue-actions');
   for(const [action,label,enabled] of [['steer','Steer now',data.control?.live?.queue_steer&&item.can_steer],['edit','Edit',item.can_edit],['delete','Delete',item.can_delete]]){
    const button=make('button',label,'quiet-button queue-action');button.type='button';button.dataset.action=action;button.setAttribute('aria-label',`${label} queued message`);button.disabled=!isConnected()||!enabled||busy.has(item.id);button.title=enabled?label:'This action needs the connected desktop or supported message content.';
    button.addEventListener('click',async()=>{if(button.disabled)return;if(action!=='steer'){open(item,data,action);return;}button.disabled=true;button.textContent='Steering…';busy.add(item.id);try{await steer(item,data);}catch(e){onResult(e.message);}finally{busy.delete(item.id);await refresh();}});actions.append(button);
   }
   if(item.controls_reason)row.append(make('p',item.controls_reason,'panel-help'));row.append(actions);content.append(row);
  }
  details.append(content);return details;
 }
 form.addEventListener('submit',async event=>{
  event.preventDefault();if(!editing||busy.size||!isConnected())return;
  const state=editing,{item,thread,operation}=state,message=input.value.trim();if(operation==='edit'&&!message){error.textContent='Add a message before saving.';return;}
  const current=getChat();if(current?.id!==thread){error.textContent='Return to this chat before saving.';return;}
  const live=current.pending_instructions?.find(q=>q.id===item.id);
  if(!live||live.revision!==item.revision){error.textContent='This message changed or left the queue. Your edit is kept here; copy it before closing.';return;}
  const fingerprint=JSON.stringify([thread,item.id,item.revision,operation,operation==='edit'?message:null]);
  let identity=attempts.get(fingerprint);if(!identity){identity=crypto.randomUUID();attempts.set(fingerprint,identity);}
  busy.add(item.id);save.disabled=true;el('queue-cancel').disabled=true;input.disabled=true;error.textContent=operation==='edit'?'Saving…':'Deleting…';
  try{
   const payload={command_id:identity,thread_id:thread,queue_id:item.id,revision:item.revision};if(operation==='edit')payload.message=message;
   const result=await request('/api/queue/'+operation,payload);
   attempts.delete(fingerprint);if(editing===state){dialog.close();editing=null;}onResult(result.message,true);
  }catch(e){if(editing===state)error.textContent=e.message+' Refresh the chat before retrying if the result is uncertain.';}
  finally{busy.delete(item.id);save.disabled=false;el('queue-cancel').disabled=false;input.disabled=false;await refresh();}
 });
 el('queue-cancel').addEventListener('click',()=>{if(!busy.size){dialog.close();editing=null;}});
 dialog.addEventListener('cancel',event=>{if(busy.size)event.preventDefault();});
 return {render,reveal(){expanded=true;},isBusy:()=>busy.size>0,reset(){editing=null;expanded=false;busy.clear();attempts.clear();input.value='';error.textContent='';el('queue-edit-photos').replaceChildren();if(dialog.open)dialog.close();}};
}};
