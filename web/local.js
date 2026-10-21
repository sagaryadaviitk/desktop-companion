"use strict";
(function(root){
  const validChatId=value=>typeof value==='string'&&/^local-[a-zA-Z0-9_-]{1,100}$/.test(value);
  const clean=value=>typeof value==='string'?value:'';
  const working=value=>['working','running','queued','pending'].includes(value);
  function selectedSkills(values,available){
    const allowed=new Set((available||[]).map(item=>item.id));
    return [...new Set(values||[])].filter(id=>typeof id==='string'&&allowed.has(id)).slice(0,3);
  }
  function pollDelay(failures,busy){return failures?Math.min(30000,1500*2**Math.min(failures-1,5)):busy?1200:5000;}
  // Persist request identity only. Conversation text and credentials never enter browser storage.
  function requests({storage,uuid=()=>root.crypto.randomUUID()}={}){
    const key='companion-local-pending';let current=null;
    try{const saved=JSON.parse(storage?.getItem(key)||'null');if(saved&&typeof saved.request_id==='string'&&/^[a-zA-Z0-9_-]{16,100}$/.test(saved.request_id)&&['create','message','archive'].includes(saved.kind)&&(!saved.chat_id||validChatId(saved.chat_id)))current={request_id:saved.request_id,kind:saved.kind,...(saved.chat_id?{chat_id:saved.chat_id}:{})};}catch(_){}
    function persist(){try{if(current)storage?.setItem(key,JSON.stringify(current));else storage?.removeItem(key);}catch(_){}}
    return {
      get:()=>current?{...current}:null,
      begin(kind,chat_id){if(current)throw new Error('Check the previous request before sending another.');current={request_id:uuid(),kind,...(chat_id?{chat_id}:{})};persist();return {...current};},
      clear(){current=null;persist();}
    };
  }
  async function requestJSON(path,{method='GET',body,csrf='',build='',fetcher=root.fetch,timeout=method==='GET'?8000:20000}={}){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),timeout);
    try{
      const response=await fetcher(path,{method,credentials:'same-origin',cache:'no-store',signal:controller.signal,
        headers:{'X-Companion-UI':build,...(method==='GET'?{}:{'Content-Type':'application/json','X-Companion-CSRF':csrf})},...(body===undefined?{}:{body:JSON.stringify(body)})});
      const data=await response.json().catch(()=>null);
      if(!response.ok)throw Object.assign(new Error(clean(data?.error)||clean(data?.message)||`Your Mac could not complete the request (${response.status}).`),{status:response.status,uncertain:method!=='GET'&&response.status>=500});
      if(!data||typeof data!=='object')throw Object.assign(new Error('Your Mac returned an unexpected response.'),{uncertain:method!=='GET'});
      return data;
    }catch(error){
      if(error.name==='AbortError')throw Object.assign(new Error('The connection timed out. Refresh to check the latest result.'),{uncertain:method!=='GET'});
      if(error instanceof TypeError)throw Object.assign(new Error('Your Mac could not be reached. Your draft is kept.'),{uncertain:method!=='GET'});
      throw error;
    }finally{clearTimeout(timer);}
  }
  function install(){
    const el=id=>document.getElementById(id),build=document.querySelector('meta[name="companion-build"]')?.content||'';
    let status=null,chats=[],detail=null,selected=null,csrf='',authenticated=false,mutation=false,reading=false,failures=0,timer=null,selectionVersion=0,chatSignature='',statusSignature='',initialized=false;
    const drafts=new Map();let storage;try{storage=root.sessionStorage;}catch(_){}
    const pending=requests({storage}),requested=new URLSearchParams(root.location.search).get('chat');
    if(validChatId(requested))selected=requested;
    const node=(tag,text,className)=>{const item=document.createElement(tag);if(text!==undefined)item.textContent=text;if(className)item.className=className;return item;};
    function notice(message,error=false,pair=false){el('local-notice').hidden=!message;el('local-notice-text').textContent=message;el('local-notice').dataset.error=String(error);el('local-pair').hidden=!pair;}
    function result(id,message,error=false){el(id).textContent=message;el(id).dataset.error=String(error);}
    function api(path,options={}){return requestJSON(path,{csrf,build,...options});}
    function lock(){authenticated=false;csrf='';status=null;chats=[];detail=null;drafts.clear();selectionVersion++;el('local-message').value='';el('local-messages').replaceChildren();el('local-chats').replaceChildren();el('local-workspace').hidden=true;el('local-connection').textContent='Browser pairing needed';notice('Pair this browser with your Mac to use the local assistant. Your existing Companion pairing works here.',false,true);}
    function uncertainty(){
      const previous=pending.get();el('local-uncertain').hidden=!previous;
      if(previous){el('local-uncertain-text').textContent=previous.kind==='create'?'A previous local chat request needs checking. Refresh the list and check whether it was created before starting another.':'A previous request needs checking. Review the latest conversation before sending another instruction. This page will not send it again automatically.';el('local-dismiss-request').disabled=mutation||working(detail?.chat?.status)||Boolean(detail?.pending);}
    }
    function controls(){
      const busy=working(detail?.chat?.status)||Boolean(detail?.pending),unresolved=Boolean(pending.get()),ready=Boolean(authenticated&&status?.enabled&&status?.ready);
      const chatModel=(status?.models||[]).find(model=>model.id===detail?.chat?.model),chatReady=ready&&chatModel?.ready===true;
      el('local-message').disabled=!detail?.chat||detail.chat.status==='archived';
      el('local-send').disabled=!chatReady||!detail?.chat||busy||status?.busy||mutation||unresolved||!el('local-message').value.trim();
      el('local-archive').disabled=!detail?.chat||busy||mutation||unresolved;
      const chosen=(status?.models||[]).find(model=>model.id===el('local-model').value);
      el('local-create').disabled=!ready||mutation||unresolved||chosen?.ready!==true;
      el('local-model').disabled=mutation||!(status?.models||[]).length;el('local-title-input').disabled=mutation;el('local-workspace-choice').disabled=mutation;
      el('local-create').textContent=mutation?'Working…':'Create local chat';
      el('local-send').textContent=mutation?'Sending…':'Send';
      el('local-working').hidden=!busy;
      el('local-working').textContent=detail?.pending?.message||'Working on your Mac…';
      el('local-composer-help').textContent=!detail?.chat?'Text only. Select a local chat to begin.':busy?'Working on your Mac. You can draft your next message.':!chatReady?'This chat’s model is unavailable. You can keep drafting.':status?.busy?'The local worker is busy. Your draft is kept.':chatModel?.tools!==true?'Text only. This is a chat-only model. File and API tools are unavailable.':'Text only. Files and API tools use this chat’s saved workspace and connections.';
      for(const checkbox of el('local-skills').querySelectorAll('input'))checkbox.disabled=mutation||(!checkbox.checked&&el('local-skills').querySelectorAll('input:checked').length>=3);
      uncertainty();
    }
    function renderStatus(){
      const signature=JSON.stringify([status?.models,status?.skills,status?.workspaces,status?.api_profiles]);
      if(signature!==statusSignature){
        statusSignature=signature;const model=el('local-model').value,workspace=el('local-workspace-choice').value,skills=[...el('local-skills').querySelectorAll('input:checked')].map(input=>input.value);
        el('local-model').replaceChildren();for(const item of status?.models||[]){const option=node('option',(item.name||item.id)+(item.tools!==true?' · Chat only':'')+(item.ready!==true?' · Not ready':''));option.value=item.id;option.disabled=item.ready!==true;el('local-model').append(option);}
        if(!el('local-model').options.length){const option=node('option','No local models available');option.value='';el('local-model').append(option);}
        const choices=status?.models||[];el('local-model').value=choices.some(item=>item.id===model)?model:choices.some(item=>item.id===status?.model)?status.model:choices.find(item=>item.ready===true)?.id||'';el('local-model').disabled=!choices.length;
        const empty=node('option','No workspace');empty.value='';el('local-workspace-choice').replaceChildren(empty);for(const item of status?.workspaces||[]){const option=node('option',item.name||item.id);option.value=item.id;el('local-workspace-choice').append(option);}el('local-workspace-choice').value=workspace;
        const legend=node('legend','Skills · choose up to 3');el('local-skills').replaceChildren(legend);const retained=selectedSkills(skills,status?.skills);
        for(const item of status?.skills||[]){const label=node('label',undefined,'local-skill'),input=node('input'),text=node('span',item.name||item.id);input.type='checkbox';input.value=item.id;input.checked=retained.includes(item.id);if(item.description)text.append(node('small',item.description));label.append(input,text);el('local-skills').append(label);}
        if(!(status?.skills||[]).length)el('local-skills').append(node('p','No local skills are configured.','local-help'));
        el('local-api-profiles').replaceChildren();for(const profile of status?.api_profiles||[]){const card=node('div',undefined,'local-tool-profile');card.append(node('strong',profile.name||profile.id),node('p',(profile.methods||[]).map(method=>typeof method==='string'?method:method.name||method.method||'').filter(Boolean).join(' · ')||'No methods enabled'));el('local-api-profiles').append(card);}
        if(!(status?.api_profiles||[]).length)el('local-api-profiles').append(node('p','No API tools configured on this Mac.','local-help'));
      }
      el('local-connection').textContent=!status?.enabled?'Local assistant is not configured':status?.ready?status?.busy?'Local model connected · Worker busy':'Local model connected':'Local model unavailable';
      if(status?.error)notice(status.error,true);else if(!status?.enabled)notice('Set up the local assistant on your Mac to start using it here.');else if(!status?.ready)notice('EXO or the selected local model is unavailable. Check it on your Mac, then refresh.');else notice('');
      controls();
    }
    function renderChats(){
      el('local-chat-count').textContent=String(chats.length);el('local-chats').replaceChildren();
      for(const chat of chats){if(!validChatId(chat.id))continue;const button=node('button',undefined,'local-chat-button');button.type='button';button.setAttribute('aria-current',String(chat.id===selected));button.append(node('strong',chat.title||'Local chat'),node('small',[chat.model,working(chat.status)?'Working':chat.status==='archived'?'Archived':''].filter(Boolean).join(' · ')));button.addEventListener('click',()=>choose(chat.id));el('local-chats').append(button);}
      if(!chats.length)el('local-chats').append(node('p','No local chats yet. Start one above.','local-help'));
    }
    function renderDetail(){
      if(!detail?.chat)return;const chat=detail.chat;el('local-chat-title').textContent=chat.title||'Local chat';document.title=(chat.title||'Local assistant')+' · Local · Desktop Companion';
      const skillNames=(chat.skills||[]).map(id=>(status?.skills||[]).find(skill=>skill.id===id)?.name||id);
      const model=(status?.models||[]).find(item=>item.id===chat.model);
      el('local-chat-detail').textContent=[chat.model,model?.tools!==true?'Chat only':'',...skillNames,chat.status==='archived'?'Archived':''].filter(Boolean).join(' · ');
      const signature=JSON.stringify([chat.id,detail.messages,detail.error]);
      if(signature!==chatSignature){
        chatSignature=signature;const panel=el('local-messages'),nearBottom=panel.scrollHeight-panel.scrollTop-panel.clientHeight<100,previous=panel.scrollTop;panel.replaceChildren();
        for(const message of detail.messages||[]){
          if(!['user','assistant','tool'].includes(message.role))continue;
          if(message.role==='tool'){const card=node('details',undefined,'local-tool-message');card.append(node('summary','Tool · '+(message.tool_name||'Local operation')),node('pre',clean(message.content)));panel.append(card);continue;}
          const article=node('article',undefined,'local-message local-message-'+message.role),header=node('header',message.role==='user'?'You':'Local assistant'),body=node('div',undefined,'local-message-body');
          if(message.created_at){const date=new Date(typeof message.created_at==='number'&&message.created_at<1e12?message.created_at*1000:message.created_at);if(!Number.isNaN(date.getTime())){const time=node('time',date.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}));time.dateTime=date.toISOString();header.append(time);}}
          if(message.role==='assistant'&&root.ChatMarkdown)root.ChatMarkdown.render(body,clean(message.content));else body.textContent=clean(message.content);
          article.append(header,body);panel.append(article);
        }
        if(!detail.messages?.length)panel.append(node('p','This chat is ready. Send a text message to begin.','local-empty'));
        if(detail.error)panel.append(node('p',clean(detail.error),'local-help'));
        panel.scrollTop=nearBottom?panel.scrollHeight:previous;
      }
      controls();
    }
    async function readChat(){
      if(!selected)return;const id=selected,version=selectionVersion,data=await api('/api/local/chat?id='+encodeURIComponent(id));
      if(id!==selected||version!==selectionVersion)return;
      if(!data.chat||data.chat.id!==id)throw new Error('The local conversation returned an unexpected response.');detail=data;renderDetail();
    }
    async function choose(id){
      if(!validChatId(id))return;if(selected)drafts.set(selected,el('local-message').value);selected=id;detail=null;selectionVersion++;chatSignature='';el('local-message').value=drafts.get(id)||'';result('local-send-result','');
      el('local-chat-title').textContent='Loading local chat…';el('local-chat-detail').textContent='';el('local-messages').replaceChildren(node('p','Loading your conversation…','local-empty'));
      const url=new URL(root.location.href);url.searchParams.set('chat',id);root.history.replaceState(null,'',url.pathname+url.search);renderChats();controls();
      try{await readChat();}catch(error){if(error.status===401)lock();else notice(error.message,true);}
    }
    function schedule(){clearTimeout(timer);if(!document.hidden)timer=setTimeout(refresh,pollDelay(failures,working(detail?.chat?.status)||Boolean(detail?.pending)||status?.busy));}
    async function refresh(){
      if(reading)return;reading=true;el('local-refresh').disabled=true;
      try{
        if(!authenticated){const auth=await api('/api/auth/status');if(!auth.authenticated){lock();return;}authenticated=true;}
        const [session,nextStatus,list]=await Promise.all([api('/api/status'),api('/api/local/status'),api('/api/local/chats')]);
        if(!Array.isArray(list.chats)||!Array.isArray(nextStatus.models))throw new Error('Your Mac returned an unexpected local status.');
        csrf=session.csrf||'';status=nextStatus;chats=list.chats;el('local-workspace').hidden=false;failures=0;renderStatus();renderChats();
        if(!initialized){initialized=true;if(chats.length)el('local-new-panel').open=false;}
        if(selected)await readChat();
      }catch(error){failures++;if(error.status===401)lock();else{el('local-connection').textContent='Connection interrupted · Draft kept';notice(error.message,true);}}
      finally{reading=false;el('local-refresh').disabled=false;controls();schedule();}
    }
    async function mutate(kind,chat_id,path,payload){
      const attempt=pending.begin(kind,chat_id);mutation=true;controls();
      try{const data=await api(path,{method:'POST',body:{request_id:attempt.request_id,...payload}});
        if(kind==='create'&&!validChatId(data.chat?.id)||kind==='message'&&data.state!=='accepted')throw Object.assign(new Error('The local request acknowledgement needs checking. Refresh the conversation before sending another request.'),{uncertain:true});
        if(kind==='message'&&detail?.chat?.id===chat_id)detail.chat.status='working';
        pending.clear();return data;}
      catch(error){if(!error.uncertain)pending.clear();if(error.status===401)lock();throw error;}
      finally{mutation=false;controls();}
    }
    el('local-refresh').addEventListener('click',refresh);
    el('local-new').addEventListener('click',()=>{el('local-new-panel').open=true;el('local-new-panel').scrollIntoView({block:'nearest',behavior:'smooth'});el('local-title-input').focus();});
    el('local-model').addEventListener('change',controls);
    el('local-skills').addEventListener('change',()=>{const count=el('local-skills').querySelectorAll('input:checked').length;result('local-skill-feedback',count===3?'Three skills selected. Remove one to choose another.':count?`${count} skill${count===1?'':'s'} selected.`:'');controls();});
    el('local-message').addEventListener('input',()=>{if(selected)drafts.set(selected,el('local-message').value);controls();});
    el('local-create-form').addEventListener('submit',async event=>{
      event.preventDefault();if(el('local-create').disabled)return;result('local-create-result','Creating your local chat…');
      const payload={model:el('local-model').value,skills:selectedSkills([...el('local-skills').querySelectorAll('input:checked')].map(input=>input.value),status.skills)},title=el('local-title-input').value.trim(),workspace=el('local-workspace-choice').value;
      if(title)payload.title=title;if(workspace)payload.workspace=workspace;
      try{const data=await mutate('create',null,'/api/local/chats',payload);if(!validChatId(data.chat?.id))throw new Error('Chat creation needs checking. Refresh your local chats before creating another.');el('local-title-input').value='';el('local-new-panel').open=false;result('local-create-result','');await choose(data.chat.id);await refresh();}
      catch(error){result('local-create-result',error.message,true);}
    });
    el('local-message-form').addEventListener('submit',async event=>{
      event.preventDefault();if(el('local-send').disabled)return;const chat_id=selected,raw=el('local-message').value,message=raw.trim();result('local-send-result','Sending to the local model…');
      try{await mutate('message',chat_id,'/api/local/message',{chat_id,message});if(drafts.get(chat_id)===raw)drafts.set(chat_id,'');if(selected===chat_id){if(el('local-message').value===raw)el('local-message').value='';result('local-send-result','Accepted by your Mac.');}await refresh();}
      catch(error){result('local-send-result',error.message+(error.uncertain?' Review the conversation before trying again.':''),true);}
    });
    el('local-archive').addEventListener('click',async()=>{
      if(el('local-archive').disabled||!root.confirm('Archive this local chat? Its saved conversation will be kept.'+(el('local-message').value.trim()?' The unsent draft will be cleared.':'')))return;const chat_id=selected;
      try{await mutate('archive',chat_id,'/api/local/archive',{chat_id});drafts.delete(chat_id);
        if(selected===chat_id){selected=null;detail=null;selectionVersion++;chatSignature='';el('local-message').value='';el('local-chat-title').textContent='Start a local chat';el('local-chat-detail').textContent='Choose a model and any skills you need.';el('local-messages').replaceChildren(node('p','Chat archived. Choose another local chat or start a new one.','local-empty'));const url=new URL(root.location.href);url.searchParams.delete('chat');root.history.replaceState(null,'',url.pathname+url.search);result('local-send-result','Local chat archived.');}
        await refresh();
      }catch(error){result('local-send-result',error.message,true);}
    });
    el('local-check-request').addEventListener('click',async()=>{const previous=pending.get();if(previous?.chat_id&&selected!==previous.chat_id)await choose(previous.chat_id);await refresh();});
    el('local-dismiss-request').addEventListener('click',()=>{if(el('local-dismiss-request').disabled)return;pending.clear();result('local-send-result','Previous result checked. Review your draft before sending a new instruction.');controls();});
    document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!document.hidden)refresh();});
    root.addEventListener('online',refresh);
    root.addEventListener('pagehide',()=>clearTimeout(timer));
    uncertainty();refresh();
  }
  const api={validChatId,selectedSkills,pollDelay,requests,requestJSON,install};
  root.CompanionLocal=api;if(typeof module==='object'&&module.exports)module.exports=api;
  if(root.document?.getElementById('local-app'))install();
})(typeof window==='object'?window:globalThis);
