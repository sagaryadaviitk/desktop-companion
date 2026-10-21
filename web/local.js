"use strict";
(function(root){
  const validChatId=value=>typeof value==='string'&&/^local-[a-zA-Z0-9_-]{1,100}$/.test(value);
  const validSourceId=value=>typeof value==='string'&&/^[a-zA-Z0-9_-]{1,100}$/.test(value)&&!value.startsWith('local-');
  const clean=value=>typeof value==='string'?value:'';
  const working=value=>['working','running','queued','pending'].includes(value);
  function filterChats(chats,query=''){const needle=clean(query).trim().toLocaleLowerCase();return (chats||[]).filter(chat=>validChatId(chat.id)&&(!needle||[chat.title,chat.model].some(value=>clean(value).toLocaleLowerCase().includes(needle))));}
  function originInfo(detail){const value=detail?.origin||detail?.chat?.origin,id=value?.source_id||value?.source?.id;return validSourceId(id)?{...value,source_id:id,source_title:clean(value.source_title)||clean(value.source?.title)||'Desktop conversation'}:null;}
  function selectedSkills(values,available){
    const allowed=new Set((available||[]).map(item=>item.id));
    return [...new Set(values||[])].filter(id=>typeof id==='string'&&allowed.has(id)).slice(0,3);
  }
  function pollDelay(failures,busy){return failures?Math.min(30000,1500*2**Math.min(failures-1,5)):busy?1200:5000;}
  // Persist request identity only. Conversation text and credentials never enter browser storage.
  function requests({storage,uuid=()=>root.crypto.randomUUID()}={}){
    const key='companion-local-pending';let current=null;
    try{const saved=JSON.parse(storage?.getItem(key)||'null');if(saved&&typeof saved.request_id==='string'&&/^[a-zA-Z0-9_-]{16,100}$/.test(saved.request_id)&&['create','fork','message','archive'].includes(saved.kind)&&(!saved.chat_id||validChatId(saved.chat_id)))current={request_id:saved.request_id,kind:saved.kind,...(saved.chat_id?{chat_id:saved.chat_id}:{})};}catch(_){}
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
    let status=null,chats=[],detail=null,selected=null,csrf='',authenticated=false,mutation=false,reading=false,failures=0,timer=null,selectionVersion=0,chatSignature='',statusSignature='',originSignature='',initialized=false,forkSource=null,forkPreview=null,previewReading=false,previewVersion=0,forkNeedsRefresh=false;
    const drafts=new Map();let storage;try{storage=root.sessionStorage;}catch(_){}
    const params=new URLSearchParams(root.location.search),pending=requests({storage}),requested=params.get('chat'),from=params.get('from');
    if(validChatId(requested))selected=requested;
    if(validSourceId(from))forkSource=from;
    if(document.documentElement?.dataset)document.documentElement.dataset.embedded=String(params.get('embedded')==='1');
    let channel;try{if(root.BroadcastChannel){channel=new root.BroadcastChannel('companion-local-change');channel.onmessage=()=>{if(!document.hidden)refresh();};}}catch(_){}
    const node=(tag,text,className)=>{const item=document.createElement(tag);if(text!==undefined)item.textContent=text;if(className)item.className=className;return item;};
    function notice(message,error=false,pair=false){el('local-notice').hidden=!message;el('local-notice-text').textContent=message;el('local-notice').dataset.error=String(error);el('local-pair').hidden=!pair;}
    function result(id,message,error=false){el(id).textContent=message;el(id).dataset.error=String(error);}
    function api(path,options={}){return requestJSON(path,{csrf,build,...options});}
    function lock(){authenticated=false;csrf='';status=null;chats=[];detail=null;forkPreview=null;previewReading=false;initialized=false;statusSignature='';drafts.clear();selectionVersion++;previewVersion++;el('local-message').value='';el('local-messages').replaceChildren();el('local-chats').replaceChildren();el('local-context-messages').replaceChildren();el('local-api-profiles').replaceChildren();renderOrigin();el('local-workspace').hidden=true;el('local-startup').hidden=false;el('local-startup-heading').textContent='Connect this browser';el('local-startup-copy').textContent='Use your existing Companion pairing to open your local chats.';el('local-startup-link').textContent='Open Companion to pair';el('local-connection').textContent='Browser pairing needed';el('local-new-panel').close();el('local-settings').close();historyDrawer(false);notice('Pair this browser with your Mac to use the local assistant. Your existing Companion pairing works here.',false,true);}
    function historyDrawer(open){el('local-sidebar').dataset.open=String(open);el('local-history-open').setAttribute('aria-expanded',String(open));el('local-history-backdrop').hidden=!open;}
    function announce(){try{channel?.postMessage({kind:'changed'});}catch(_){}}
    function sourceURL(id){return '/?chat='+encodeURIComponent(id);}
    function renderOrigin(){
      const origin=originInfo(detail);el('local-origin').hidden=!origin;
      if(!origin){originSignature='';el('local-origin-messages').replaceChildren();el('local-origin-link').textContent='';el('local-origin-context').open=false;return;}
      el('local-origin-link').textContent=origin.source_title;el('local-origin-link').href=sourceURL(origin.source_id);el('local-origin-note').textContent=(Number.isInteger(origin.message_count)?origin.message_count+' messages copied. ':'')+'The original desktop chat is unchanged.';
      const signature=JSON.stringify([origin.source_id,origin.messages]);el('local-origin-context').hidden=!origin.messages?.length;
      if(signature!==originSignature){originSignature=signature;el('local-origin-context').open=false;el('local-origin-messages').replaceChildren();for(const message of origin.messages||[]){if(!['user','assistant'].includes(message.role))continue;const row=node('div',undefined,'local-context-message');row.append(node('strong',message.role==='user'?'You':'Desktop assistant'),node('p',clean(message.content)));el('local-origin-messages').append(row);}}
    }
    function uncertainty(){
      const previous=pending.get();el('local-uncertain').hidden=!previous;
      if(previous){el('local-uncertain-text').textContent=['create','fork'].includes(previous.kind)?'A previous chat request needs checking. Refresh the list and check whether it was created before starting another.':'A previous request needs checking. Review the latest conversation before sending another instruction. This page will not send it again automatically.';el('local-dismiss-request').disabled=mutation||working(detail?.chat?.status)||Boolean(detail?.pending);}
    }
    function controls(){
      const busy=working(detail?.chat?.status)||Boolean(detail?.pending),unresolved=Boolean(pending.get()),ready=Boolean(authenticated&&status?.enabled&&status?.ready);
      const chatModel=(status?.models||[]).find(model=>model.id===detail?.chat?.model),chatReady=ready&&chatModel?.ready===true;
      el('local-message').disabled=!detail?.chat||detail.chat.status==='archived';
      el('local-send').disabled=!chatReady||!detail?.chat||busy||status?.busy||mutation||unresolved||!el('local-message').value.trim();
      el('local-archive').disabled=!detail?.chat||busy||mutation||unresolved;
      const chosen=(status?.models||[]).find(model=>model.id===el('local-model').value);
      el('local-create').disabled=!ready||mutation||unresolved||chosen?.ready!==true||Boolean(forkSource&&(!forkPreview||previewReading||forkNeedsRefresh));
      el('local-model').disabled=mutation||!(status?.models||[]).length;el('local-title-input').disabled=mutation;el('local-workspace-choice').disabled=mutation;
      el('local-new').disabled=!authenticated||mutation;el('local-empty-new').disabled=!authenticated||mutation;
      el('local-empty-state').hidden=Boolean(selected||detail?.chat);el('local-messages').hidden=!selected&&!detail?.chat;
      el('local-settings-open').disabled=!authenticated;el('local-history-open').disabled=!authenticated;
      el('local-create-close').disabled=mutation;el('local-context-refresh').disabled=previewReading||mutation;
      if(unresolved&&!mutation&&el('local-new-panel').open&&!el('local-create-result').textContent)result('local-create-result','A previous request needs checking. Close this panel and review your latest chats before creating another.',true);
      el('local-create').textContent=mutation?'Creating…':forkSource?'Create local branch':'Start chat';
      el('local-send').setAttribute('aria-label',mutation?'Sending message':'Send message');el('local-send').setAttribute('aria-busy',String(mutation));el('local-archive').hidden=!detail?.chat;
      const count=el('local-skills').querySelectorAll('input:checked').length,workspace=(status?.workspaces||[]).find(item=>item.id===el('local-workspace-choice').value);
      el('local-options-summary').textContent=[count?count+' skill'+(count===1?'':'s'):'',workspace?.name].filter(Boolean).join(' · ')||'Optional';
      el('local-model-note').textContent=chosen?.ready!==true?'This model is not ready on your Mac.':chosen?.tools!==true?'Chat only · File and API tools are unavailable.':'Ready for chat and saved skills. Tool access follows your workspace and configured connections.';
      el('local-working').hidden=!busy;
      el('local-working').textContent=detail?.pending?.message||'Working on your Mac…';
      const chatWorkspace=(status?.workspaces||[]).find(item=>item.id===detail?.chat?.workspace),profiles=status?.api_profiles||[];
      const scopeHelp=['Text only',chatWorkspace?'Workspace read access':detail?.chat?.workspace?'Workspace unavailable':'No workspace selected',profiles.length?profiles.some(profile=>profile.methods?.length)?'API methods configured':'No API methods configured':'No API profiles configured'].join(' · ');
      el('local-composer-help').textContent=!detail?.chat?'Text only · Select a chat to begin.':busy?'Working on your Mac · You can draft your next message.':!chatReady?'This chat’s model is unavailable. You can keep drafting.':status?.busy?'Your Mac is busy · Your draft is kept.':chatModel?.tools!==true?'Chat only · File and API tools are unavailable.':scopeHelp;
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
      el('local-connection').textContent=!status?.enabled?'Setup needed on this Mac':status?.ready?status?.busy?'Working on this Mac':'Ready on this Mac':'Local model unavailable';el('local-sidebar').dataset.ready=String(Boolean(status?.ready));
      if(status?.error)notice(status.error,true);else if(!status?.enabled)notice('Set up the local assistant on your Mac to start using it here.');else if(!status?.ready)notice('EXO or the selected local model is unavailable. Check it on your Mac, then refresh.');else notice('');
      controls();
    }
    function renderChats(){
      const visible=filterChats(chats,el('local-search').value);el('local-chat-count').textContent=String(visible.length);el('local-chats').replaceChildren();
      for(const chat of visible){const button=node('button',undefined,'local-chat-button');button.type='button';button.setAttribute('aria-current',String(chat.id===selected));const title=node('strong',chat.title||'Local chat');button.append(title,node('small',working(chat.status)?'Working on your Mac':(status?.models||[]).find(model=>model.id===chat.model)?.name||chat.model));button.addEventListener('click',()=>choose(chat.id));el('local-chats').append(button);}
      if(!visible.length)el('local-chats').append(node('p',chats.length?'No chats match your search.':'Your new local chats will appear here.','local-empty'));
    }
    function renderDetail(){
      if(!detail?.chat)return;const chat=detail.chat;el('local-chat-title').textContent=chat.title||'Local chat';document.title=(chat.title||'Local assistant')+' · Local · Desktop Companion';
      const skillNames=(chat.skills||[]).map(id=>(status?.skills||[]).find(skill=>skill.id===id)?.name||id);
      const model=(status?.models||[]).find(item=>item.id===chat.model);
      el('local-chat-detail').textContent=[model?.name||chat.model,model?.tools!==true?'Chat only':'',...skillNames,chat.status==='archived'?'Archived':''].filter(Boolean).join(' · ');renderOrigin();
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
        if(!detail.messages?.length)panel.append(node('p',originInfo(detail)?'Context copied. Send a message to continue locally.':'This chat is ready. Send a text message to begin.','local-empty'));
        if(detail.error)panel.append(node('p',clean(detail.error),'local-help'));
        panel.scrollTop=nearBottom?panel.scrollHeight:previous;
      }
      controls();
    }
    function renderPreview(){
      el('local-fork-review').hidden=!forkSource;
      el('local-create-heading').textContent=forkSource?'Continue this chat locally':'New local chat';
      el('local-create-intro').textContent=forkSource?'Review the context, then choose a model for your local branch.':'A fresh conversation, powered by your Mac.';
      if(!forkSource)return;
      el('local-fork-title').textContent=forkPreview?.source?.title||'Loading desktop conversation…';
      el('local-fork-count').textContent=forkPreview?(forkPreview.message_count??forkPreview.messages.length)+' messages · '+Number(forkPreview.char_count||0).toLocaleString()+' characters'+(forkPreview.truncated?' · Latest context only':''):'Checking the latest visible messages…';
      el('local-fork-notice').textContent=forkPreview?.notice||'Only the visible context shown here will be copied. Your desktop chat stays unchanged.';
      el('local-context-messages').replaceChildren();
      for(const message of forkPreview?.messages||[]){if(!['user','assistant'].includes(message.role))continue;const row=node('div',undefined,'local-context-message');row.append(node('strong',message.role==='user'?'You':'Desktop assistant'),node('p',clean(message.content)));el('local-context-messages').append(row);}
      if(!forkPreview)el('local-context-messages').append(node('p','Context preview is loading.','local-help'));
    }
    async function loadPreview(){
      if(!forkSource||previewReading)return;const source=forkSource,version=++previewVersion;previewReading=true;controls();result('local-create-result','');
      try{const data=await api('/api/local/fork-preview?source_chat_id='+encodeURIComponent(source));
        if(source!==forkSource||version!==previewVersion)return;
        if(data.source?.id!==source||typeof data.revision!=='string'||!data.revision||!Array.isArray(data.messages))throw new Error('The copied context could not be verified. Refresh the preview before creating a branch.');
        forkPreview=data;forkNeedsRefresh=false;renderPreview();
      }catch(error){if(version===previewVersion){forkPreview=null;forkNeedsRefresh=true;if(error.status===401)lock();else result('local-create-result',error.message,true);}}
      finally{if(version===previewVersion){previewReading=false;controls();}}
    }
    function closeCreate(){if(mutation)return;el('local-new-panel').close();forkSource=null;forkPreview=null;forkNeedsRefresh=false;previewReading=false;previewVersion++;const url=new URL(root.location.href);url.searchParams.delete('from');root.history.replaceState(null,'',url.pathname+url.search);}
    function openNew(){if(!authenticated||mutation)return;forkSource=null;forkPreview=null;forkNeedsRefresh=false;previewVersion++;previewReading=false;renderPreview();result('local-create-result','');historyDrawer(false);if(!el('local-new-panel').open)el('local-new-panel').showModal();el('local-model').focus();const url=new URL(root.location.href);url.searchParams.delete('from');root.history.replaceState(null,'',url.pathname+url.search);controls();}
    async function openFork(){historyDrawer(false);renderPreview();if(!el('local-new-panel').open)el('local-new-panel').showModal();await loadPreview();}
    async function readChat(){
      if(!selected)return;const id=selected,version=selectionVersion,data=await api('/api/local/chat?id='+encodeURIComponent(id));
      if(id!==selected||version!==selectionVersion)return;
      if(!data.chat||data.chat.id!==id)throw new Error('The local conversation returned an unexpected response.');detail=data;renderDetail();
    }
    async function choose(id,provided){
      if(!validChatId(id))return;if(selected)drafts.set(selected,el('local-message').value);selected=id;detail=null;selectionVersion++;chatSignature='';el('local-message').value=drafts.get(id)||'';result('local-send-result','');
      el('local-chat-title').textContent='Loading local chat…';el('local-chat-detail').textContent='';el('local-messages').replaceChildren(node('p','Loading your conversation…','local-empty'));
      const url=new URL(root.location.href);url.searchParams.set('chat',id);root.history.replaceState(null,'',url.pathname+url.search);historyDrawer(false);renderOrigin();renderChats();controls();
      try{if(provided?.chat?.id===id){detail=provided;renderDetail();}else await readChat();}catch(error){if(error.status===401)lock();else notice(error.message,true);}
    }
    function schedule(){clearTimeout(timer);if(!document.hidden)timer=setTimeout(refresh,pollDelay(failures,working(detail?.chat?.status)||Boolean(detail?.pending)||status?.busy));}
    async function refresh(){
      if(reading)return;reading=true;el('local-refresh').disabled=true;
      try{
        if(!authenticated){const auth=await api('/api/auth/status');if(!auth.authenticated){lock();return;}authenticated=true;}
        const [session,nextStatus,list]=await Promise.all([api('/api/status'),api('/api/local/status'),api('/api/local/chats')]);
        if(!Array.isArray(list.chats)||!Array.isArray(nextStatus.models))throw new Error('Your Mac returned an unexpected local status.');
        csrf=session.csrf||'';status=nextStatus;chats=list.chats;el('local-workspace').hidden=false;el('local-startup').hidden=true;failures=0;renderStatus();renderChats();
        if(!initialized){initialized=true;if(forkSource)await openFork();else if(!selected&&chats.length)await choose(chats[0].id);else if(from&&!validSourceId(from))notice('This desktop chat link is not valid. Choose a local chat or start a new one.',true);}
        if(selected)await readChat();
      }catch(error){failures++;if(error.status===401)lock();else{el('local-connection').textContent='Connection interrupted · Draft kept';notice(error.message,true);if(!initialized){el('local-startup-heading').textContent='Connection interrupted';el('local-startup-copy').textContent=error.message;}}}
      finally{reading=false;el('local-refresh').disabled=false;controls();schedule();}
    }
    async function mutate(kind,chat_id,path,payload){
      const attempt=pending.begin(kind,chat_id);mutation=true;controls();
      try{const data=await api(path,{method:'POST',body:{request_id:attempt.request_id,...payload}});
        if(['create','fork'].includes(kind)&&!validChatId(data.chat?.id)||kind==='message'&&data.state!=='accepted')throw Object.assign(new Error('The local request acknowledgement needs checking. Refresh the conversation before sending another request.'),{uncertain:true});
        if(kind==='message'&&detail?.chat?.id===chat_id)detail.chat.status='working';
        pending.clear();announce();return data;}
      catch(error){if(!error.uncertain)pending.clear();if(error.status===401)lock();throw error;}
      finally{mutation=false;controls();}
    }
    el('local-refresh').addEventListener('click',refresh);
    el('local-new').addEventListener('click',openNew);el('local-empty-new').addEventListener('click',openNew);
    el('local-create-close').addEventListener('click',closeCreate);el('local-new-panel').addEventListener('cancel',event=>{event.preventDefault();closeCreate();});
    el('local-context-refresh').addEventListener('click',loadPreview);
    el('local-history-open').addEventListener('click',()=>historyDrawer(el('local-sidebar').dataset.open!=='true'));el('local-history-close').addEventListener('click',()=>historyDrawer(false));el('local-history-backdrop').addEventListener('click',()=>historyDrawer(false));
    el('local-search').addEventListener('input',renderChats);
    el('local-settings-open').addEventListener('click',()=>el('local-settings').showModal());el('local-settings-close').addEventListener('click',()=>el('local-settings').close());
    el('local-model').addEventListener('change',controls);
    el('local-workspace-choice').addEventListener('change',controls);
    el('local-skills').addEventListener('change',()=>{const count=el('local-skills').querySelectorAll('input:checked').length;result('local-skill-feedback',count===3?'Three skills selected. Remove one to choose another.':count?`${count} skill${count===1?'':'s'} selected.`:'');controls();});
    el('local-message').addEventListener('input',()=>{if(selected)drafts.set(selected,el('local-message').value);controls();});
    el('local-create-form').addEventListener('submit',async event=>{
      event.preventDefault();if(el('local-create').disabled)return;const branching=Boolean(forkSource);result('local-create-result',branching?'Creating your local branch…':'Creating your local chat…');
      const payload={model:el('local-model').value,skills:selectedSkills([...el('local-skills').querySelectorAll('input:checked')].map(input=>input.value),status.skills)},title=el('local-title-input').value.trim(),workspace=el('local-workspace-choice').value;
      if(title)payload.title=title;if(workspace)payload.workspace=workspace;
      if(branching){payload.source_chat_id=forkSource;payload.context_revision=forkPreview.revision;}
      try{const data=await mutate(branching?'fork':'create',null,branching?'/api/local/fork':'/api/local/chats',payload);el('local-title-input').value='';closeCreate();result('local-create-result','');chats=[data.chat,...chats.filter(chat=>chat.id!==data.chat.id)];renderChats();await choose(data.chat.id,data);el('local-message').focus();await refresh();}
      catch(error){if(branching&&error.status===409){forkNeedsRefresh=true;result('local-create-result',error.message+' Refresh the context before creating a branch.',true);controls();}else result('local-create-result',error.message+(error.uncertain?' Close this panel and check the chat list before trying again.':''),true);}
    });
    el('local-message-form').addEventListener('submit',async event=>{
      event.preventDefault();if(el('local-send').disabled)return;const chat_id=selected,raw=el('local-message').value,message=raw.trim();result('local-send-result','Sending to the local model…');
      try{await mutate('message',chat_id,'/api/local/message',{chat_id,message});if(drafts.get(chat_id)===raw)drafts.set(chat_id,'');if(selected===chat_id){if(el('local-message').value===raw)el('local-message').value='';result('local-send-result','Accepted by your Mac.');}await refresh();}
      catch(error){result('local-send-result',error.message+(error.uncertain?' Review the conversation before trying again.':''),true);}
    });
    el('local-archive').addEventListener('click',async()=>{
      if(el('local-archive').disabled||!root.confirm('Archive this local chat? Its saved conversation will be kept.'+(el('local-message').value.trim()?' The unsent draft will be cleared.':'')))return;const chat_id=selected;
      try{await mutate('archive',chat_id,'/api/local/archive',{chat_id});drafts.delete(chat_id);
        if(selected===chat_id){selected=null;detail=null;selectionVersion++;chatSignature='';el('local-message').value='';el('local-chat-title').textContent='Start a local chat';el('local-chat-detail').textContent='Choose a conversation or start something new.';el('local-messages').replaceChildren(node('p','Chat archived. Choose another local chat or start a new one.','local-empty'));renderOrigin();const url=new URL(root.location.href);url.searchParams.delete('chat');root.history.replaceState(null,'',url.pathname+url.search);result('local-send-result','Local chat archived.');}
        await refresh();
      }catch(error){result('local-send-result',error.message,true);}
    });
    el('local-check-request').addEventListener('click',async()=>{const previous=pending.get();if(previous?.chat_id&&selected!==previous.chat_id)await choose(previous.chat_id);await refresh();});
    el('local-dismiss-request').addEventListener('click',()=>{if(el('local-dismiss-request').disabled)return;pending.clear();result('local-send-result','Previous result checked. Review your draft before sending a new instruction.');controls();});
    document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!document.hidden)refresh();});
    root.addEventListener('online',refresh);
    root.addEventListener('pagehide',()=>clearTimeout(timer));
    root.addEventListener('beforeunload',event=>{if([...drafts.values()].some(value=>value.trim())){event.preventDefault();event.returnValue='';}});
    function viewport(){const height=root.visualViewport?.height;if(Number.isFinite(height)&&height>200)document.documentElement?.style?.setProperty('--local-height',height+'px');}
    root.visualViewport?.addEventListener('resize',viewport);viewport();
    uncertainty();refresh();
  }
  const api={validChatId,validSourceId,filterChats,originInfo,selectedSkills,pollDelay,requests,requestJSON,install};
  root.CompanionLocal=api;if(typeof module==='object'&&module.exports)module.exports=api;
  if(root.document?.getElementById('local-app'))install();
})(typeof window==='object'?window:globalThis);
