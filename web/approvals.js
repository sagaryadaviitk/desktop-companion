"use strict";
// Live approval responses never become ordinary messages or queued follow-ups.
window.CompanionApprovals={
 create({dialog,banner,panel,close,summary,submit,changed=()=>{},document:doc=document}){
  let context=null,signature='',generation=0;
  const drafts=new Map(),busy=new Set(),attempts=new Map();
  const node=(tag,text,cls)=>{const n=doc.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  const key=r=>context.thread+':'+r.id+':'+r.revision;
  function reset(){generation++;context=null;signature='';drafts.clear();busy.clear();attempts.clear();banner.hidden=true;panel.replaceChildren();if(dialog.open)dialog.close();}
  function update(value){
   if(context?.thread!==value?.thread){generation++;signature='';if(dialog.open)dialog.close();}
   context=value;
   const requests=value?.requests||[];
   banner.hidden=!value?.visible||!requests.length;
   summary.textContent=`${requests.length} ${requests.length===1?'request needs':'requests need'} your response`;
   if(dialog.open)render();
  }
  function render(){
   const requests=context?.requests||[];
   if(context?.connected&&!requests.length){dialog.close();return;}
   const next=JSON.stringify([context,[...busy],[...attempts]]);
   if(next===signature)return;signature=next;
   const position=dialog.scrollTop;panel.replaceChildren();
   if(!context?.connected){panel.append(node('p','Live requests could not refresh. Reconnect before responding. Your form is kept.','approval-feedback'));return;}
   for(const request of requests){
    const id=key(request),version=generation,thread=context.thread;
    const card=node('section',undefined,'approval-card');card.dataset.request=request.id;
    card.append(node('span',request.kind==='input'?'LIVE QUESTION':'APPROVAL REQUEST','question-eyebrow'),node('h3',request.title));
    if(request.message)card.append(node('p',request.message));
    for(const detail of request.details||[]){const details=node('details',undefined,'approval-detail');details.open=true;details.append(node('summary',detail.label),node('pre',detail.text));card.append(details);}
    card.append(node('p',request.note,'panel-help'));
    const form=node('form',undefined,'approval-form'),inputs=new Map();
    const saved=drafts.get(id)||{};
    const status=attempts.get(id)|| (request.response_state?{state:request.response_state,message:request.response_message}:null);
    const locked=busy.has(id)||Boolean(status);
    for(const field of request.fields||[]){
     let input;
     if(field.enum||field.type==='boolean'){
      input=node('select');const empty=node('option','Choose…');empty.value='';input.append(empty);
      for(const value of field.enum||['true','false']){const option=node('option',field.type==='boolean'?(value==='true'?'Yes':'No'):value);option.value=value;input.append(option);}
     }else{input=node(field.type==='string'?'textarea':'input');if(field.type!=='string'){input.type='number';input.step=field.type==='integer'?'1':'any';}else{input.rows=3;input.maxLength=Math.min(4000,field.maxLength??4000);}}
     const label=node('label',field.label+(field.required?' *':''));input.id='approval-'+request.id+'-'+inputs.size;label.htmlFor=input.id;
     input.value=saved[field.id]??'';input.disabled=locked;input.setAttribute('autocomplete','off');
     input.addEventListener('input',()=>{drafts.set(id,{...(drafts.get(id)||{}),[field.id]:input.value});});
     form.append(label);if(field.description)form.append(node('p',field.description,'panel-help'));
     if(field.suggestions?.length){const options=node('div',undefined,'approval-suggestions');for(const value of field.suggestions){const button=node('button',value,'quiet-button');button.type='button';button.disabled=locked;button.addEventListener('click',()=>{input.value=value;drafts.set(id,{...(drafts.get(id)||{}),[field.id]:value});input.focus();});options.append(button);}form.append(options);}
     form.append(input);inputs.set(field.id,{field,input});
    }
    const feedback=node('p',busy.has(id)?'Sending your response…':status?.message||'','approval-feedback');feedback.setAttribute('role','status');
    const actions=node('div',undefined,'approval-actions');
    for(const choice of request.choices||[]){
     const button=node('button',choice.label,choice.id==='allow'||choice.id==='reply'?'primary-button':'quiet-button');button.type='button';button.disabled=locked;
     if(choice.danger)button.classList.add('danger-action');
     if(choice.scope){const scope=node('small',choice.scope);button.append(scope);}
     button.addEventListener('click',async()=>{
      if(busy.has(id)||attempts.has(id)||generation!==version||!context?.connected)return;
      const answers={};
      if(choice.id==='allow'||choice.id==='reply')for(const [name,{field,input}] of inputs){
       if(input.value===''){if(field.required){feedback.textContent='Complete every required field.';input.focus();return;}continue;}
       const value=field.type==='boolean'?input.value==='true':field.type==='number'||field.type==='integer'?Number(input.value):input.value;
       if(field.type==='integer'&&!Number.isInteger(value)){feedback.textContent='Enter a whole number.';input.focus();return;}
       answers[name]=value;
      }
      busy.add(id);signature='';render();
      try{
       const result=await submit({thread_id:thread,approval_id:request.id,revision:request.revision,choice:choice.id,answers});
       if(generation!==version)return;
       attempts.set(id,result);drafts.delete(id);
       if(result.state==='answered'){
        context={...context,requests:context.requests.filter(r=>r.id!==request.id||r.revision!==request.revision)};
        if(!context.requests.length)dialog.close();
       }
       changed(result);
      }catch(error){
       if(generation!==version)return;
       // Lost HTTP acknowledgement is never a reason to send the decision again.
       attempts.set(id,{state:error.result?.state||'unknown',message:error.message+' Refresh to check the desktop request. This response will not be resent.'});
       changed();
      }finally{busy.delete(id);if(generation===version){signature='';update(context);render();}}
     });actions.append(button);
    }
    form.addEventListener('submit',event=>event.preventDefault()); // Enter never approves.
    if(!request.choices?.length)form.append(node('p','Requires your Mac','approval-required'));
    form.append(actions,feedback);card.append(form);panel.append(card);
   }
   dialog.scrollTop=position;
  }
  banner.addEventListener('click',()=>{if(!context?.connected)return;signature='';dialog.showModal();render();});
  close.addEventListener('click',()=>dialog.close());
  return {update,reset,busy:()=>busy.size>0,hasDraft:()=>[...drafts.values()].some(a=>Object.values(a).some(v=>v!==''))};
 }
};
