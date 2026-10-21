"use strict";
let readRetryAt=0;
const el = id => document.getElementById(id);
let chats = [], selected = null, selectedData = null, csrf = "", refreshing = false, sending = false, lastSignature = "", currentStatus=null, pairingTimer=null;
let messageMap = new Map(), nextBefore = null, loadedEarlier = false, loadingEarlier = false;
const drafts=new Map();
let projects=[], projectSignature="", panelSignature="", activeView="conversation", initialSelectionPending=true, conversationNeedsScroll=true;
let chatSignature="",pendingSignature="",selectionVersion=0,authVersion=0,connected=false,starting=false;
const commandAttempts=new Map();
const messageSignatures=new WeakMap();
let inboxView="recent",homeSignature="",deliveryMode="queue",markingAll=false,modelSaving=false;
const readAcknowledgements=new Map(),modelAttempts=new Map();
const readRequests=new Set();
let displayedRevision=null,readFrame=0,refreshPromise=null,chatRequestVersion=0;
let catalogRevision='',conversationRevision='',chatRefreshPromise=null;
const deliveryReceipts=CompanionConversation.receipts();
let conversationScroll=null,approvalUI=null,photoStorageBusy=false;
const loadedBuild=document.querySelector('meta[name=companion-build]').content;
let updater=null;
function hasUnsentDraft(){return Boolean(approvalUI?.hasDraft())||Boolean(newChatFlow?.hasDraft())||[...drafts.values()].some(value=>value.trim())||[...photoDrafts.values()].some(value=>value.length)||[...questionDrafts.values()].some(value=>typeof value==='string'?value.trim():value?.answer||value?.text);}
function executionStatus(chat){if(!connected&&chat.last_recorded_turn?.status==='inProgress')return 'unknown';return currentStatus?.execution?.[chat.id]?.status||(chat.last_recorded_turn?.status==='inProgress'?'unknown':chat.last_recorded_turn?.status==='failed'?'failed':chat.last_recorded_turn?.status==='interrupted'?'paused':'idle');}
const questionDrafts=new Map(),questionBusy=new Set();
const questionErrors=new Map();
let questionSignature='';
const queueSteering=new Set();
const photoDrafts=new Map(),photoUploads=new Map();
let resultTimer=null;
let newChatFlow=null,healthFlow=null,queueUI=null,connectingChat=false,renamingChat=false;
function showCommandResult(message,temporary=false){clearTimeout(resultTimer);el("command-result").textContent=message;if(temporary)resultTimer=setTimeout(()=>{if(el("command-result").textContent===message)el("command-result").textContent="";},7000);}
const mobile=()=>window.matchMedia("(max-width:760px)").matches;
const recordedLabels={inProgress:"Working now",completed:"Completed",interrupted:"Interrupted",failed:"Failed"};
if(new URLSearchParams(location.search).get("embedded")==="1")document.body.classList.add("embedded");
const shortTime = value => value ? new Date(value).toLocaleString([], {month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}) : "Time unavailable";

async function api(path, options = {}) {
  const version=authVersion,controller=new AbortController(),reading=!options.method||options.method==='GET',timer=setTimeout(()=>controller.abort(),reading?8000:20000);
  try{
    const response=await fetch(path,{cache:"no-store",...options,headers:{...options.headers,'X-Companion-UI':loadedBuild},signal:controller.signal});
    if(response.status===304&&path.startsWith('/api/chat?')){if(version!==authVersion)throw new Error('The browser session changed.');return {unchanged:true,checked_at:Number(response.headers.get('X-Companion-Execution-Checked-At'))};}
    if(response.status===304&&path==='/api/chats'){if(version!==authVersion)throw new Error('The browser session changed.');return {unchanged:true};}
    const data=await response.json().catch(()=>null);
    if(version!==authVersion)throw new Error("The browser session changed. Please reconnect.");
    if(!response.ok){
      if(reading&&[429,503].includes(response.status)){
        const header=response.headers.get('Retry-After'),seconds=Number(header);
        const wait=header?(Number.isFinite(seconds)?seconds*1000:Date.parse(header)-Date.now()):0;
        if(Number.isFinite(wait))readRetryAt=Date.now()+Math.max(0,Math.min(60000,wait));
      }
      if(response.status===401&&data?.auth_required)lockScreen();throw Object.assign(new Error(data?.error||data?.message||(response.status===429?"Too many attempts. Wait a moment and try again.":`Your Mac could not complete the request (${response.status}). Try again.`)),{result:data,status:response.status});
    }
    if(!data)throw new Error("The connection returned an unexpected response. Try again.");
    if(path.startsWith('/api/chat?'))data.view_revision=response.headers.get('ETag')||'';
    return data;
  }catch(error){
    if(error.name==="AbortError")throw new Error("The connection timed out. Check your connection and try again.");
    if(error instanceof TypeError)throw new Error("Your Mac could not be reached. Check your connection and try again.");
    throw error;
  }finally{clearTimeout(timer);}
}

function lockScreen(){
  authVersion++;selectionVersion++;deliveryReceipts.reset();chatRefreshPromise=null;connected=false;currentStatus=null;chatSignature="";pendingSignature="";commandAttempts.clear();
  inboxView="recent";homeSignature="";deliveryMode="queue";readAcknowledgements.clear();modelAttempts.clear();
  displayedRevision=null;readRequests.clear();refreshPromise=null;cancelAnimationFrame(readFrame);el('pull-refresh').hidden=true;
  catalogRevision='';conversationRevision='';
  photoDrafts.clear();photoUploads.clear();queueSteering.clear();clearTimeout(resultTimer);renderPhotoDrafts();questionDrafts.clear();questionBusy.clear();questionErrors.clear();questionSignature='';el('questions-banner').hidden=true;el('question-cards').replaceChildren();
  for(const id of ['usage-dialog','model-dialog','phone-dialog','photo-storage-dialog','questions-dialog','host-dialog','new-chat-dialog','account-dialog','chat-actions-dialog','system-dialog'])if(el(id).open)el(id).close();
  newChatFlow?.reset();el('new-chat').disabled=true;
  healthFlow?.reset();
  queueUI?.reset();approvalUI?.reset();el('photo-storage-drafts').replaceChildren();el('photo-storage-status').textContent='';
  chats=[];projects=[];selected=null;selectedData=null;csrf="";lastSignature="";panelSignature="";projectSignature="";initialSelectionPending=true;messageMap.clear();nextBefore=null;loadedEarlier=false;
  el("chats").replaceChildren();el("messages").replaceChildren();el("pending").replaceChildren();el("instruction").value="";
  drafts.clear();
  el('inbox-message').textContent='';
  el("search").value="";el("chat-filter").value="active";el("projects").hidden=true;el("projects-toggle").setAttribute("aria-pressed","false");
  renderPower();
  el('weekly-value').textContent='Checking…';el('weekly-usage').dataset.tone='neutral';el('weekly-usage').classList.remove('low','stale');el('inbox-overview').replaceChildren();el('home-chats').replaceChildren();
  for(const id of ["projects","activity-panel","changes-panel","details-panel"])el(id).replaceChildren();
  el("project-filter").replaceChildren(new Option("All projects & folders",""));el("workspace-summary").textContent="";
  el("title").textContent="Choose a conversation";el("activity").textContent="";el("command-result").textContent="";
  el("instruction").disabled=true;el("send").disabled=true;
  document.body.classList.remove("booting","ready","chat-open");document.body.classList.add("signed-out");
  setView("conversation");updateComposer();
}

function showNotice(message){el("notice-text").textContent=message;el("notice").hidden=!message;}
function updateComposer(){
  el('chat-options').disabled=!selectedData;
  const panel=el("messages"),atBottom=conversationScroll?.following()??panel.scrollHeight-panel.scrollTop-panel.clientHeight<100;
  const input=el("instruction"),available=Boolean(selectedData?.control?.available);
  const photoCount=(photoDrafts.get(selected)||[]).length,uploading=photoUploads.get(selected)||0;
  document.querySelector('.composer-area').classList.toggle('has-draft',Boolean(input.value.trim()||photoCount));
  el('add-photo').disabled=!available||!connected||sending||uploading>0||photoCount>=3;renderPhotoDrafts();
  // A temporary connection failure must never prevent drafting. Sending still
  // requires a fresh, available control channel below.
  input.disabled=!selectedData||Boolean(selectedData.archived);
  if(!available||!connected){
    pendingSignature='';
    for(const button of el('pending').querySelectorAll('.queue-action'))button.disabled=true;
  }
  el('queue-save').disabled=!available||!connected||Boolean(queueUI?.isBusy());
  const live=selectedData?.control?.live||{},canSteer=live.steer===true,canSend=live.send_now===true;
  if(deliveryMode==='queue'&&canSend||deliveryMode==='steer'&&!canSteer&&canSend)deliveryMode='send';
  if(deliveryMode==='send'&&!canSend)deliveryMode='queue';
  const prior=commandAttempts.get(selected),retrying=prior?.retrying&&prior.message===input.value.trim()&&JSON.stringify(prior.photos)===JSON.stringify((photoDrafts.get(selected)||[]).map(photo=>photo.id));
  el("send").disabled=!available||!connected||sending||uploading>0||!input.value.trim()&&!photoCount||deliveryMode==='steer'&&!canSteer&&!retrying;
  const label=retrying?'Check previous send':deliveryMode==='steer'?'Steer active turn':deliveryMode==='send'?'Send message':'Queue follow-up';
  el("send").setAttribute("aria-label",sending?'Sending…':label);el('send').querySelector('.send-label').textContent=label;
  el('mode-steer').disabled=!canSteer||!connected||sending;el('mode-steer').title=canSteer?'Send to the active turn':live.steer_reason||live.reason||'Live steering needs a shared desktop connection.';
  el('mode-queue').disabled=sending;
  el('mode-queue').textContent=canSend?'Send':'Queue';
  for(const mode of ['queue','steer'])el('mode-'+mode).setAttribute('aria-pressed',String(mode==='queue'?deliveryMode!=='steer':deliveryMode==='steer'));
  if(selectedData)el('control-status').textContent=!connected?'You can keep drafting. Reconnect to send your message.':!available?(selectedData.control.reason||'Sending is unavailable. You can keep drafting.'):retrying?'Check the previous request before sending a new instruction. This retry keeps its original identity and delivery mode.':deliveryMode==='steer'?'Send guidance into the active desktop turn.':deliveryMode==='send'?'Send now and start the next turn in this desktop chat.':canSteer?'Wait until the current turn finishes. Choose Steer to redirect it now.':(live.reason||'Follow-ups wait in this chat’s desktop queue.');
  el('connect-chat').hidden=!available||!connected||live.connected===true||live.model_selection===true;el('connect-chat').disabled=connectingChat||sending;
  const model=live.model||selectedData?.details?.model;
  const modelName=(currentStatus?.models||[]).find(item=>item.id===model)?.name||model;
  el('model-trigger').textContent=modelName&&modelName!=='Unknown'?modelName+' ⌄':'Model & controls ⌄';el('model-trigger').disabled=!selected;
  const count=input.value.length;el("composer-meta").hidden=count<7000;el("draft-count").textContent=count>=7000?`${count.toLocaleString()} / 8,000`:"";el("draft-count").classList.toggle("limit",count>=8000);
  CompanionShell.sizeInput(input);
  if(atBottom&&activeView==="conversation")jumpLatest();
}
function jumpLatest(){const panel=el("messages");if(conversationScroll)conversationScroll.bottom();else panel.scrollTop=panel.scrollHeight;conversationNeedsScroll=false;updateJump();scheduleMarkViewed();}
function updateJump(){const panel=el("messages");el("jump-latest").hidden=!selected||activeView!=="conversation"||panel.scrollHeight-panel.scrollTop-panel.clientHeight<140;}

function inConversation(){return selected&&activeView==='conversation'&&!document.hidden&&!document.querySelector('dialog[open]')&&!document.body.classList.contains('home-open')&&(!mobile()||document.body.classList.contains('chat-open'));}
function scheduleMarkViewed(){cancelAnimationFrame(readFrame);readFrame=requestAnimationFrame(()=>markViewed());}
function applyReadAcknowledgements(){for(const chat of chats)if(readAcknowledgements.has(chat.id)&&readAcknowledgements.get(chat.id)===chat.message_revision)chat.unread=false;}
async function markViewed(){
  const revision=selectedData?.message_revision,id=selected;
  const panel=el('messages'),version=authVersion,key=version+':'+id;
  if(!connected||!revision||displayedRevision!==revision||!inConversation()||panel.clientHeight===0||panel.scrollHeight-panel.scrollTop-panel.clientHeight>100||readRequests.has(key)||readAcknowledgements.get(id)===revision)return;
  readRequests.add(key);
  try{await api('/api/read-state',{method:'POST',headers:{'Content-Type':'application/json','X-Companion-CSRF':csrf},body:JSON.stringify({thread_id:id,revision,operation:'read'})});if(version!==authVersion)return;readAcknowledgements.set(id,revision);const chat=chats.find(chat=>chat.id===id);if(chat?.message_revision===revision)chat.unread=false;renderChats();renderHome();}
  catch(_){/* A newer revision must be displayed before it can be marked seen. */}
  finally{readRequests.delete(key);if(version===authVersion&&id===selected&&selectedData?.message_revision!==revision)scheduleMarkViewed();}
}

function mainChats(){return chats.filter(chat=>!chat.archived&&!chat.subagent);}
function renderHome(){
  const main=mainChats(),unread=main.filter(chat=>chat.unread),working=main.filter(chat=>executionStatus(chat)==='working');
  el('unread-count').textContent=unread.length;
  el('reply-count').textContent=main.filter(chat=>questionCount(chat)>0||approvalCount(chat)>0).length;
  for(const view of ['recent','unread','working','reply'])el('quick-'+view).setAttribute('aria-pressed',String(inboxView===view));
  const project=el('project-filter').selectedOptions[0]?.textContent||'All projects',filter=el('chat-filter').selectedOptions[0]?.textContent||'Main chats';
  el('filter-summary').textContent=project.replace(' & folders','')+' · '+filter;
  el('mark-all-read').disabled=markingAll||!connected||!visibleChats().some(chat=>chat.unread);
  el('mark-all-read').textContent=markingAll?'Marking…':'Mark seen';
  const visible=visibleChats();
  const signature=JSON.stringify([visible.map(chat=>[chat.id,chat.title,chat.preview,chat.preview_role,chat.last_activity_at,chat.updated_at,chat.unread,questionCount(chat),approvalCount(chat),chat.project_keys,executionStatus(chat)]),statsKey(main),projects.map(p=>[p.id,p.name,p.chat_count]),inboxView,el('project-filter').value,el('chat-filter').value,el('search').value]);
  if(signature===homeSignature)return;homeSignature=signature;
  const stats=[[String(unread.length),'Unread updates'],[String(working.length),'Working now'],[String(projects.filter(p=>p.kind==='project').length),'Projects']];
  el('inbox-overview').replaceChildren();el('home-stats').replaceChildren();
  for(const [index,[number,label]]of stats.entries())for(const id of ['inbox-overview','home-stats']){
    const button=document.createElement('button');button.className='home-stat';button.type='button';
    const value=document.createElement('strong');value.textContent=number;const name=document.createElement('span');name.textContent=id==='inbox-overview'?(index===0?'Unread':index===1?'Working':'Projects'):label;button.append(value,name);
    button.addEventListener('click',()=>{if(index===2){el('browse-options').open=true;el('projects').hidden=false;el('projects-toggle').setAttribute('aria-pressed','true');goHome();}else chooseInbox(index===0?'unread':'working');});el(id).append(button);
  }
  el('home-chats').replaceChildren();
  const ordered=visible;
  for(const chat of ordered){const button=chatCard(chat,true);el('home-chats').append(button);}
  if(!ordered.length){const empty=document.createElement('p');empty.className='empty-message';empty.textContent=inboxView==='unread'?'You’re caught up. New replies will appear here.':'No matching conversations. Try another project or filter.';el('home-chats').append(empty);}
  el('home-chats-heading').textContent=inboxView==='reply'?'Needs your response':inboxView==='unread'?'Unread conversations':inboxView==='working'?'Working now':'Recent conversations';
  const fresh=visible.filter(chat=>chat.unread).length;el('home-updated').textContent=fresh?`${fresh} with unread updates`:inboxView==='unread'?'You’re caught up':`${visible.length} conversations`;
}
function statsKey(chats){return [chats.length,chats.filter(chat=>chat.unread).length,chats.filter(chat=>executionStatus(chat)==='working').length];}

function projectLabel(chat){const keys=chat.project_keys||[chat.project_key],filter=el('project-filter').value;return projects.find(project=>project.id===filter&&keys.includes(project.id))||projects.find(project=>project.kind==='project'&&keys.includes(project.id))||projects.find(project=>keys.includes(project.id))||{id:chat.project_key||'',name:chat.workspace||'Personal chat',kind:'folder'};}
function chatCard(chat,home=false){
  const button=document.createElement('button');button.type='button';button.className=(home?'home-chat':'chat')+(!home&&chat.id===selected&&!document.body.classList.contains('home-open')?' selected':'')+(chat.unread?' unread':'');button.dataset.chat=chat.id;
  button.setAttribute('aria-pressed',String(!home&&chat.id===selected&&!document.body.classList.contains('home-open')));button.title=chat.title;
  const heading=document.createElement('span');heading.className='chat-title';heading.textContent=chat.title;
  if(chat.unread){const dot=document.createElement('span');dot.className='unread-dot';dot.setAttribute('aria-label','Unread updates');heading.prepend(dot);}
  const preview=document.createElement('span');preview.className='chat-preview';preview.textContent=chat.preview?(chat.preview_role==='user'?'You: ':'')+chat.preview:'Open the recorded conversation';
  const meta=document.createElement('span');meta.className='chat-meta';const workspace=document.createElement('span'),project=projectLabel(chat);let tint=0;for(const char of project.id)tint=(tint*31+char.charCodeAt(0))>>>0;workspace.className='project-label project-color-'+(tint%5);workspace.textContent=project.name;workspace.title=(project.kind==='project'?'Project: ':'Folder: ')+project.name;const time=document.createElement('time');time.textContent=shortTime(chat.last_activity_at||chat.last_message_at||chat.updated_at);meta.append(workspace,time);
  const tags=document.createElement('span');tags.className='chat-tags';
  const status=executionStatus(chat),badge=document.createElement('span');badge.className='status-tag '+(status==='working'?'inProgress':status);badge.textContent=approvalCount(chat)?'Needs response':status==='working'?'Working':status==='failed'?'Failed':status==='paused'?'Paused':status==='unknown'?'Status unavailable':'Idle';badge.title=status==='unknown'?'Open this chat to check its live desktop connection.':'Desktop state: '+status;button.classList.add(status==='working'?'working-chat':'idle-chat');
  const headingRow=document.createElement('span');headingRow.className='chat-heading-row';headingRow.append(heading,badge);
  if(chat.archived||chat.subagent){const kind=document.createElement('span');kind.textContent=chat.archived?'Archived':'Subagent';tags.append(kind);}
  if(chat.unread){const newTag=document.createElement('span');newTag.className='new-tag';newTag.textContent='Unread';tags.append(newTag);}
  if(approvalCount(chat)){const approval=document.createElement('span');approval.className='approval-tag';approval.textContent=`${approvalCount(chat)} live request${approvalCount(chat)===1?'':'s'}`;tags.prepend(approval);}
  if(questionCount(chat)){const reply=document.createElement('span');reply.className='reply-tag';reply.textContent=`${questionCount(chat)} question${questionCount(chat)===1?'':'s'}`;tags.prepend(reply);}
  button.append(headingRow,preview,meta,tags);button.addEventListener('click',()=>selectChat(chat.id));return button;
}

function chooseInbox(view){inboxView=view;el('chat-filter').value=view==='working'?'working':'active';goHome();renderChats();renderHome();}
function goHome(){if(el('approvals-dialog').open)el('approvals-dialog').close();el('approvals-banner').hidden=true;document.activeElement?.blur();const wasChat=new URLSearchParams(location.search).has('chat');document.body.classList.remove('chat-open');document.body.classList.add('home-open');el('home-screen').hidden=false;const url=new URL(location.href);url.searchParams.delete('chat');if(wasChat)history.pushState({companion:true,view:'list'},'',url.pathname+url.search);renderHome();renderChats();}

function renderUsage(){
  const usage=currentStatus?.usage,weekly=usage?.weekly||[],main=weekly.find(item=>item.id==='codex')||weekly[0];
  const presentation=CompanionStatus.allowance(main?.remaining_percent,usage?.stale);
  el('weekly-value').textContent=presentation.value!==null?`${presentation.value}% left`:usage?.loading||!usage?'Checking…':'Unavailable';
  el('weekly-usage').dataset.tone=presentation.tone;
  el('weekly-usage').setAttribute('aria-label','Weekly allowance: '+el('weekly-value').textContent+(usage?.stale?' · Last known reading':''));
  el('weekly-usage').classList.toggle('stale',Boolean(usage?.stale));
  el('weekly-usage').title=main?.resets_at?`Resets ${shortTime(main.resets_at*1000)}${usage.stale?' · Last known reading':''}`:usage?.reason||'Weekly account allowance';
  const panel=el('usage-details');panel.replaceChildren();
  for(const item of weekly){const card=document.createElement('div');card.className='usage-card';card.dataset.tone=CompanionStatus.allowance(item.remaining_percent,usage.stale).tone;const heading=document.createElement('strong');heading.textContent=`${item.remaining_percent}% remaining`;const label=document.createElement('p');label.textContent=(item.id==='codex'?'Account weekly allowance':item.label)+(usage.stale?' · Last known reading':'');const meter=document.createElement('meter');meter.min=0;meter.max=100;meter.value=item.remaining_percent;meter.setAttribute('aria-label',item.label+' remaining');const reset=document.createElement('p');reset.textContent=item.resets_at?'Resets '+new Date(item.resets_at*1000).toLocaleString():'Reset time not reported';card.append(heading,label,meter,reset);panel.append(card);}
  const note=document.createElement('p');note.textContent=weekly.length?`Last checked ${shortTime(usage.checked_at*1000)}. ${usage.stale?'Reconnecting to refresh this reading.':'Refreshes about once a minute.'}`:usage?.reason||'Checking your local account…';panel.append(note);
}

function renderPower(power={},stale=false){
  const presentation=CompanionStatus.power(power,stale),button=el('host-power');
  button.dataset.tone=presentation.tone;button.title=presentation.title+' · Click for Mac status';button.setAttribute('aria-label',presentation.title+' · Mac status');
  el('host-battery').textContent=presentation.battery;el('host-lid').textContent=presentation.lid;el('host-power-icon').toggleAttribute('hidden',!presentation.charging);
}

function renderHostStatus(){
  const panel=el('host-details'),power=currentStatus?.power||{},check=currentStatus?.computer_use||{};
  panel.replaceChildren();
  const add=(tag,value)=>{const node=document.createElement(tag);node.textContent=value;panel.append(node);return node;};
  add('h3','Background work');
  add('p',CompanionStatus.power(power).title);
  add('p',power.closed_lid_enabled===true?'Closed-lid background operation is enabled. Shell work and connected tools can continue while the screen locks.':'Closed-lid background operation could not be verified.');
  add('h3','Desktop apps while locked');
  if(check.update_available)add('p',`Locked Use update available: ${check.installed_version||'not detected'} → ${check.available_version}. On your Mac, open Raycast → Desktop Companion → Unattended mode → Update Locked Use… and complete the macOS prompt.`);
  add('p',check.state==='blocked'?'Last native app test: blocked. macOS automatic unlock did not complete.':check.state==='passed'?'Last native app test: passed.':'Native app access while locked has not been verified.');
  if(check.checked_at)add('p',`Checked ${shortTime(check.checked_at*1000)}${check.stale?' · Previous test; not a current access guarantee.':''}`);
  if(check.state==='blocked')add('p','Recovery: manually unlock once, confirm Locked use is enabled, then lock again and start a fresh native-app test from your phone. Local keyboard or pointer input can pause automatic unlock. Companion cannot clear that macOS/ChatGPT safeguard.');
  if(check.locked_use_installed===true)add('p','The app’s Locked Use component was installed at that check. This alone does not prove native app access.');
  add('p','ChatGPT Settings → Computer Use → Locked use controls this separately. ChatGPT also requires an active, trusted Computer Use turn. Keeping the Mac awake or sending a message does not guarantee that native Chrome or another desktop app can be opened while locked.');
  const link=add('a','Open the official Locked Use guide');link.href='https://learn.chatgpt.com/docs/computer-use#locked-use';link.target='_blank';link.rel='noopener noreferrer';
}
el('host-power').addEventListener('click',()=>{renderHostStatus();el('host-dialog').showModal();});
el('host-close').addEventListener('click',()=>el('host-dialog').close());

function renderModelDialog(){
  const live=selectedData?.control?.live||{},models=currentStatus?.models||[],choice=el('model-choice'),previous=choice.value;
  const current=live.model||selectedData?.details?.model||'';choice.replaceChildren();
  for(const model of models)choice.append(new Option(model.name,model.id));
  if(current&&!models.some(model=>model.id===current))choice.prepend(new Option(current,current));
  choice.value=models.some(model=>model.id===previous)?previous:current;
  if(!choice.value&&models.length)choice.value=models[0].id;
  const available=Boolean(live.model_selection&&connected&&!selectedData?.archived&&models.length);
  choice.disabled=!available;el('effort-choice').disabled=!available;el('model-apply').disabled=!available||modelSaving;
  el('live-control-help').textContent=!models.length?'Loading the models available to your account…':available?'Model changes are connected to this desktop chat. '+(live.steer?'Steer can send a correction to its active turn.':'Steer becomes available when the chat is running.'):(live.model_reason||live.reason||'Live model changes need the shared desktop connection.')+' The model shown is the saved selection; this dialog cannot change it while disconnected.';
  renderEfforts();
}

async function steerQueued(instruction,data){
  if(!connected||queueSteering.has(instruction.id))return;
  const target=data.id,version=authVersion;queueSteering.add(instruction.id);renderQuestions();
  try{
    const result=await api('/api/queue/steer',{method:'POST',headers:{'Content-Type':'application/json','X-Companion-CSRF':csrf},body:JSON.stringify({thread_id:target,queue_id:instruction.id,revision:instruction.revision,expected_turn_id:data.control.live?.execution?.turn_id||data.last_recorded_turn?.turn_id})});
    if(version===authVersion){deliveryReceipts.begin(target,'queue:'+instruction.id,instruction.text,instruction.photos||[]);deliveryReceipts.settle(target,'queue:'+instruction.id,result);if(target===selected){renderMessages();showCommandResult(result.message,true);}}
    return result;
  }finally{queueSteering.delete(instruction.id);pendingSignature='';questionSignature='';if(version===authVersion&&target===selected)await refreshChat();}
}

queueUI=CompanionQueue.install({getChat:()=>selectedData,isConnected:()=>Boolean(connected&&selectedData?.control?.available),
 request:(path,payload)=>api(path,{method:'POST',headers:{'Content-Type':'application/json','X-Companion-CSRF':csrf},body:JSON.stringify(payload)}),
 steer:steerQueued,refresh:async()=>{pendingSignature='';await refreshChat();},onResult:showCommandResult});

let photoSignature='';
function renderPhotoDrafts(){
  const photos=photoDrafts.get(selected)||[],uploading=photoUploads.get(selected)||0,panel=el('photo-drafts');
  const signature=JSON.stringify([selected,photos,uploading,sending]);if(signature===photoSignature)return;photoSignature=signature;
  panel.replaceChildren();panel.hidden=!photos.length&&!uploading;
  for(const photo of photos){const card=document.createElement('div');card.className='photo-draft';const img=document.createElement('img');img.src=photo.url;img.alt=photo.name;const remove=document.createElement('button');remove.type='button';remove.className='remove-photo';remove.textContent='×';remove.disabled=sending;remove.setAttribute('aria-label','Remove '+photo.name);remove.addEventListener('click',async()=>{const target=selected;photoDrafts.set(target,(photoDrafts.get(target)||[]).filter(p=>p.id!==photo.id));updateComposer();try{await api('/api/photos/remove',{method:'POST',headers:{'Content-Type':'application/json','X-Companion-CSRF':csrf},body:JSON.stringify({thread_id:target,photo_id:photo.id})});}catch(error){showCommandResult(error.message);}});card.append(img,remove);panel.append(card);}
  if(uploading){const note=document.createElement('span');note.className='photo-progress';note.textContent='Adding photo…';note.setAttribute('role','status');panel.append(note);}
}

async function preparePhoto(file){
  if(!file.type.startsWith('image/')||file.size>25*1024*1024)throw new Error('Choose a photo under 25 MB.');
  const url=URL.createObjectURL(file),image=new Image();
  try{
    image.src=url;await image.decode();
    if(image.width*image.height>50_000_000)throw new Error('This photo is too large. Choose a smaller image.');
    const scale=Math.min(1,2048/Math.max(image.width,image.height)),canvas=document.createElement('canvas');canvas.width=Math.round(image.width*scale);canvas.height=Math.round(image.height*scale);
    const context=canvas.getContext('2d');context.fillStyle='#fff';context.fillRect(0,0,canvas.width,canvas.height);context.drawImage(image,0,0,canvas.width,canvas.height);
    const result=canvas.toDataURL('image/jpeg',.88);if(result.length>5_590_000)throw new Error('This photo is too large. Choose a smaller image.');return result;
  }catch(error){throw new Error(error.message.includes('large')?error.message:'This photo cannot be opened in your browser. Try a JPEG, PNG, or a screenshot.');}
  finally{URL.revokeObjectURL(url);}
}
async function addPhotoFiles(files){
  const target=selected,version=authVersion;
  if(!target||!files.length||!connected||!selectedData?.control?.available||sending)return;
  if(photoUploads.get(target)){showCommandResult('A photo is still being added. Try pasting again in a moment.');return;}
  photoUploads.set(target,1);updateComposer();
  try{for(const file of files){if((photoDrafts.get(target)||[]).length>=3){showCommandResult('Attach up to three photos per message.');break;}const data_url=await preparePhoto(file);if(version!==authVersion)return;const photo=await api('/api/photos',{method:'POST',headers:{'Content-Type':'application/json','X-Companion-CSRF':csrf},body:JSON.stringify({thread_id:target,name:file.name||'Pasted photo',data_url})});if(version!==authVersion)return;photoDrafts.set(target,[...(photoDrafts.get(target)||[]),photo]);if(selected===target)updateComposer();}}
  catch(error){if(version===authVersion&&selected===target)showCommandResult(error.message);}
  finally{photoUploads.delete(target);if(version===authVersion&&selected===target)updateComposer();}
}
if(window.CompanionAttachments)CompanionAttachments.install({addFiles:addPhotoFiles,canAdd:()=>Boolean(selected&&connected&&selectedData?.control?.available&&!sending),onError:showCommandResult,getContext:()=>authVersion+':'+selected});
else el('add-photo').addEventListener('click',()=>el('photo-input').click());

approvalUI=CompanionApprovals.create({dialog:el('approvals-dialog'),banner:el('approvals-banner'),panel:el('approval-cards'),close:el('approvals-close'),summary:el('approvals-summary'),
  submit:payload=>api('/api/approvals/respond',{method:'POST',headers:{'Content-Type':'application/json','X-Companion-CSRF':csrf},body:JSON.stringify(payload)}),
  changed:result=>{if(result)showCommandResult(result.message,true);refreshChat(true);}});
function questionCount(chat){const value=connected?currentStatus?.execution?.[chat.id]?.question_count:null;return Number.isInteger(value)?value:chat.question_count||0;}
function approvalCount(chat){return connected?currentStatus?.execution?.[chat.id]?.approval_count||0:0;}
function renderApprovals(){approvalUI.update({thread:selected,visible:Boolean(selected&&!document.body.classList.contains('home-open')),connected:Boolean(connected&&selectedData?.control?.live?.connected&&!selectedData?.archived),requests:selectedData?.control?.live?.execution?.approvals||[]});}
async function renderPhotoStorage(){
 const version=authVersion,panel=el('photo-storage-drafts');
 try{
  const data=await api('/api/photos/storage');if(version!==authVersion)return;
  const mb=n=>(n/1048576).toFixed(1)+' MB';
  el('photo-storage-status').textContent=`${mb(data.used_bytes)} of ${mb(data.capacity_bytes)} used · ${mb(data.protected_bytes)} kept for submitted messages`;
  panel.replaceChildren();
  if(!data.drafts.length){const p=document.createElement('p');p.textContent='No unsent photos in this browser. Submitted attachments are protected and cannot be purged here.';p.className='panel-help';panel.append(p);}
  for(const photo of data.drafts){
   const row=document.createElement('div');row.className='photo-storage-row';
   const img=document.createElement('img');img.src=photo.url;img.alt=photo.name;img.loading='lazy';
   const text=document.createElement('span');text.textContent=photo.name+' · '+shortTime(photo.created_at*1000);
   const remove=document.createElement('button');remove.type='button';remove.className='quiet-button';remove.textContent='Remove';
   remove.addEventListener('click',async()=>{
    if(photoStorageBusy)return;photoStorageBusy=true;remove.disabled=true;
    try{await api('/api/photos/drafts/remove',{method:'POST',headers:{'Content-Type':'application/json','X-Companion-CSRF':csrf},body:JSON.stringify({photo_id:photo.id})});if(version!==authVersion)return;
     for(const [id,photos]of photoDrafts)photoDrafts.set(id,photos.filter(p=>p.id!==photo.id));updateComposer();await renderPhotoStorage();
    }catch(error){if(version===authVersion)el('photo-storage-status').textContent=error.message;}
    finally{photoStorageBusy=false;remove.disabled=false;}
   });row.append(img,text,remove);panel.append(row);
  }
 }catch(error){if(version===authVersion)el('photo-storage-status').textContent=error.message;}
}
el('photo-storage-open').addEventListener('click',()=>{el('account-dialog').close();el('photo-storage-dialog').showModal();renderPhotoStorage();});
el('photo-storage-close').addEventListener('click',()=>el('photo-storage-dialog').close());
function renderQuestions(){
  renderApprovals();
  const questions=selectedData?.questions||[],banner=el('questions-banner');
  banner.hidden=!selected||!questions.length||document.body.classList.contains('home-open');
  const unanswered=questions.filter(q=>q.delivery==='unanswered').length,queued=questions.filter(q=>q.delivery==='queued').length;
  el('questions-summary').textContent=unanswered?`${unanswered} question${unanswered===1?'':'s'} for you`:queued?`${queued} repl${queued===1?'y':'ies'} queued`:'Reply delivery status';
  banner.dataset.state=questions.some(q=>q.delivery==='unknown')?'warning':'normal';
  if(!el('questions-dialog').open)return;
  const available=Boolean(connected&&selectedData?.control?.available&&!selectedData.archived);
  const signature=JSON.stringify([selected,questions,available,selectedData?.control?.live?.queue_steer,[...queueSteering],[...questionBusy],[...questionErrors]]);
  if(signature===questionSignature)return;questionSignature=signature;
  const panel=el('question-cards'),position=el('questions-dialog').scrollTop;
  // Keep the user's input node and selection intact when a five-second poll changes nothing.
  panel.replaceChildren();
  const note=(tag,value,className)=>{const node=document.createElement(tag);node.textContent=value;if(className)node.className=className;return node;};
  if(!selectedData){panel.append(note('p','This chat could not refresh. Close this panel and reconnect; your drafts are kept.','question-feedback'));return;}
  if(!questions.length){el('questions-dialog').close();return;}
  for(const [index,question] of questions.entries()){
    const key=selected+':'+question.id+':'+question.revision,submitted=question.delivery!=='unanswered',busy=questionBusy.has(key);
    const card=document.createElement('section');card.className='question-card';card.dataset.question=question.id;
    card.append(note('span',`QUESTION ${index+1} OF ${questions.length}`,'question-eyebrow'),note('h3',question.title));
    const form=document.createElement('form');form.className='question-form';
    const textarea=document.createElement('textarea');textarea.id='question-answer-'+index;textarea.rows=3;textarea.maxLength=4000;textarea.placeholder=question.options.length?'Or write your own answer…':'Type your answer…';textarea.value=questionDrafts.get(key)||'';
    if(question.queued_answer)textarea.value=question.queued_answer;
    textarea.disabled=submitted||busy;textarea.setAttribute('aria-label','Your answer to question '+(index+1));
    const options=document.createElement('fieldset');options.className='question-options';options.append(note('legend','Suggested answers'));
    for(const [optionIndex,option] of question.options.entries()){
      const label=document.createElement('label');label.className='question-option';
      const radio=document.createElement('input');radio.type='radio';radio.name='question-'+index;radio.value=option;radio.disabled=submitted||busy;radio.checked=textarea.value===option;
      radio.addEventListener('change',()=>{textarea.value=option;questionDrafts.set(key,option);update();});label.append(radio,note('span',option));options.append(label);
    }
    if(question.options.length)form.append(options);
    const label=note('label',question.options.length?'Your answer':'Reply');label.htmlFor=textarea.id;
    const submit=note('button',busy?'Sending…':submitted?'Reply submitted':'Send reply','primary-button');submit.type='submit';
    const feedback=note('p',questionErrors.get(key)||(question.delivery==='queued'?(selectedData.control.live?.queue_steer?'Reply queued. Use Steer reply now to deliver it to the active turn.':'Reply queued until this turn finishes. Manage this queued reply on your Mac.'):question.delivery==='unknown'?'Delivery is unconfirmed. Check the desktop queue and conversation before trying again.':question.delivery==='steered'||question.delivery==='sent'?'Reply delivered to the desktop chat.':submitted?'A reply was already attempted. Check the conversation on your Mac.':!available?'Replies are unavailable until this chat reconnects. Your draft is kept.':''),'question-feedback');feedback.setAttribute('role','status');
    function update(){submit.disabled=!available||submitted||busy||!textarea.value.trim();}
    textarea.addEventListener('input',()=>{questionDrafts.set(key,textarea.value);for(const radio of options.querySelectorAll('input'))radio.checked=radio.value===textarea.value;update();});
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(submit.disabled||!connected||questionBusy.has(key))return;
      const target=selected,version=authVersion,answer=textarea.value.trim();questionErrors.delete(key);questionBusy.add(key);renderQuestions();
      try{
        const result=await api('/api/questions/answer',{method:'POST',headers:{'Content-Type':'application/json','X-Companion-CSRF':csrf},body:JSON.stringify({thread_id:target,question_id:question.id,revision:question.revision,answer})});
        if(version!==authVersion)return;
        if(selected===target){const current=selectedData?.questions?.find(q=>q.id===question.id&&q.revision===question.revision);if(current)current.delivery=result.state;questionDrafts.delete(key);showCommandResult(result.message,true);if(selectedData?.questions?.every(q=>['queued','steered','sent'].includes(q.delivery)))el('questions-dialog').close();if(result.state==='queued')queueUI.reveal();}
      }catch(error){if(version===authVersion)questionErrors.set(key,error.message+' Your draft is kept. An unchanged retry will not send twice.');}
      finally{questionBusy.delete(key);if(version===authVersion&&selected===target){await refreshChat();renderQuestions();}}
    });
    form.append(label,textarea,submit,feedback);
    if(question.delivery==='queued'&&question.queued_message&&selectedData.control.live?.queue_steer){const instruction=question.queued_message,steer=note('button',queueSteering.has(instruction.id)?'Steering…':'Steer reply now','quiet-button');steer.type='button';steer.disabled=!available||!instruction.can_steer||queueSteering.has(instruction.id);steer.addEventListener('click',async()=>{steer.disabled=true;steer.textContent='Steering…';try{await steerQueued(instruction,selectedData);}catch(error){questionErrors.set(key,error.message);renderQuestions();}});form.append(steer);}
    card.append(form);panel.append(card);update();
  }
  el('questions-dialog').scrollTop=position;
}
el('questions-banner').addEventListener('click',()=>{questionSignature='';el('questions-dialog').showModal();renderQuestions();});
el('questions-close').addEventListener('click',()=>el('questions-dialog').close());
function renderEfforts(changed=false){const choice=el('effort-choice'),previous=choice.value,model=(currentStatus?.models||[]).find(model=>model.id===el('model-choice').value),current=selectedData?.control?.live?.effort||selectedData?.details?.reasoning_effort;choice.replaceChildren();choice.append(new Option(current?`Keep current (${current})`:'Keep chat setting',''));for(const effort of model?.efforts||[])choice.append(new Option(effort.charAt(0).toUpperCase()+effort.slice(1),effort));if(changed&&model?.efforts.includes(model.default_effort))choice.value=model.default_effort;else if(model?.efforts.includes(previous))choice.value=previous;}

let viewportFrame=0,viewportBaseline=window.innerHeight;
function fitViewport(){
  cancelAnimationFrame(viewportFrame);viewportFrame=requestAnimationFrame(()=>{
    const viewport=window.visualViewport,unzoomed=!viewport||Math.abs(viewport.scale-1)<.05;
    // Pinch zoom must keep its native panning and magnification behavior.
    if(!unzoomed)return;
    const panel=el("messages"),atBottom=conversationScroll?.following()??panel.scrollHeight-panel.scrollTop-panel.clientHeight<100;
    const editing=document.activeElement?.matches("input,textarea,select");
    const height=viewport?.height||window.innerHeight;
    if(!editing)viewportBaseline=height;else viewportBaseline=Math.max(viewportBaseline,window.innerHeight,height);
    document.body.classList.toggle("keyboard-open",Boolean(editing&&viewportBaseline-height>120));
    document.documentElement.style.setProperty("--app-height",height+"px");
    document.documentElement.style.setProperty("--viewport-top",(viewport?.offsetTop||0)+"px");
    updateComposer();if(atBottom&&activeView==="conversation")jumpLatest();else updateJump();
  });
}
window.visualViewport?.addEventListener("resize",fitViewport);window.visualViewport?.addEventListener("scroll",fitViewport);
window.addEventListener("resize",fitViewport);window.addEventListener("orientationchange",()=>{viewportBaseline=0;fitViewport();});
document.addEventListener("focusin",fitViewport);document.addEventListener("focusout",fitViewport);
CompanionShell.installKeyboard({isMobile:mobile});
conversationScroll=CompanionConversation.scroller(el("messages"),{changed:()=>{updateJump();scheduleMarkViewed();}});
el("messages").addEventListener("scroll",()=>{updateJump();markViewed();},{passive:true});el("jump-latest").addEventListener("click",jumpLatest);
fitViewport();

function visibleChats(){
  const query = el("search").value.trim().toLowerCase();
  const project=el("project-filter").value,filter=el("chat-filter").value;
  return chats.filter(chat=> (inboxView!=='unread'||chat.unread)&&(inboxView!=='reply'||questionCount(chat)>0||approvalCount(chat)>0)&&(!project||(chat.project_keys||[chat.project_key]).includes(project))&&`${chat.title} ${chat.workspace} ${projectLabel(chat).name} ${chat.preview||''} ${chat.id}`.toLowerCase().includes(query)&&
    (filter==="all"||filter==="active"&&!chat.archived&&!chat.subagent||filter==="archived"&&chat.archived||filter==="pinned"&&chat.pinned||filter==="subagents"&&chat.subagent||filter==="working"&&!chat.archived&&executionStatus(chat)==="working"));
}

function renderProjects(){
  const signature=JSON.stringify(projects);
  if(signature===projectSignature)return;projectSignature=signature;
  const selectedProject=el("project-filter").value;
  el("project-filter").replaceChildren(new Option("All projects & folders",""));el("projects").replaceChildren();
  let group="";
  for(const project of projects){
    el("project-filter").append(new Option(`${project.name} · ${project.chat_count}`,project.id));
    const kind=project.kind==="project"?"Saved projects":"Other folders";
    if(kind!==group){const label=document.createElement("p");label.className="chat-group";label.textContent=kind;el("projects").append(label);group=kind;}
    const button=document.createElement("button");button.type="button";button.className="project-card";button.title=project.name;
    const title=document.createElement("span");title.textContent=project.name;
    const count=document.createElement("span");count.textContent=project.chat_count;
    button.append(title,count);button.addEventListener("click",()=>{el("project-filter").value=project.id;el("projects").hidden=true;el("projects-toggle").setAttribute("aria-pressed","false");applyFilter();});el("projects").append(button);
  }
  if(projects.some(project=>project.id===selectedProject))el("project-filter").value=selectedProject;
}

function renderChats() {
  const visible=visibleChats();
  renderHome();
  el("chat-count").textContent = `${visible.length}`;
  el("workspace-summary").textContent=`${projects.filter(project=>project.kind==="project").length} projects · ${chats.length} chats · ${chats.filter(chat=>chat.archived).length} archived`;
  const signature=JSON.stringify([visible,visible.map(executionStatus),visible.map(approvalCount),visible.map(questionCount),selected,inboxView,el('project-filter').value,projects.map(p=>[p.id,p.name]),document.body.classList.contains('home-open')]);if(signature===chatSignature)return;chatSignature=signature;
  const panel=el("chats"),oldTop=panel.scrollTop,focused=document.activeElement?.dataset.chat;
  panel.replaceChildren();
  // The catalog already uses the desktop's saved recency order. Badges never change it.
  const ordered = visible;
  for (const chat of ordered) {
    el("chats").append(chatCard(chat));
  }
  if (!visible.length) {const text=document.createElement("p");text.className="empty-message";text.textContent=inboxView==='unread'?'You’re caught up. New replies will appear here.':"No matching conversations. Try another project or filter.";el("chats").append(text);}
  panel.scrollTop=oldTop;if(focused)[...panel.querySelectorAll("button")].find(button=>button.dataset.chat===focused)?.focus({preventScroll:true});
}

function applyFilter(){
  el('questions-banner').hidden=true;el('approvals-banner').hidden=true;
  inboxView=el('chat-filter').value==='working'?'working':'recent';
  initialSelectionPending=false;
  renderChats();
  if(selected&&!visibleChats().some(chat=>chat.id===selected)){
    selected=null;selectedData=null;selectionVersion++;lastSignature="";panelSignature="";messageMap.clear();nextBefore=null;pendingSignature="";
    for(const id of ["messages","activity-panel","changes-panel","details-panel","pending"])el(id).replaceChildren();
    el("title").textContent="Choose a conversation";el("activity").textContent="Select a chat from this project or filter.";el("instruction").value="";el("instruction").disabled=true;el("send").disabled=true;el("control-status").textContent="Choose an existing chat to queue a follow-up.";
    const url=new URL(location.href);url.searchParams.delete("chat");history.replaceState(null,"",url.pathname+url.search);
    document.body.classList.remove("chat-open");
    document.body.classList.add('home-open');el('home-screen').hidden=false;
    el("command-result").textContent="";renderChats();updateComposer();updateJump();
  }
}

function setView(view){
  activeView=view;
  for(const name of ["conversation","activity","changes","details"]){
    el("tab-"+name).setAttribute("aria-selected",String(name===view));
    el("tab-"+name).tabIndex=name===view?0:-1;
    el(name==="conversation"?"reading-area":name+"-panel").hidden=name!==view;
  }
  if(view==="conversation"&&conversationNeedsScroll){el("messages").scrollTop=el("messages").scrollHeight;conversationNeedsScroll=false;}
  updateJump();
  if(view==='conversation')markViewed();
}

function renderPanels(data){
  const signature=JSON.stringify([data.id,data.last_recorded_turn,data.activity,data.plans,data.changes,data.details,data.control?.live,data.archived,data.subagent,currentStatus?.computer_use]);
  if(signature===panelSignature)return;panelSignature=signature;
  const positions=["activity-panel","changes-panel","details-panel"].map(id=>[id,el(id).scrollTop]);
  const text=(tag,value,className)=>{const item=document.createElement(tag);item.textContent=value;if(className)item.className=className;return item;};
  const activity=el("activity-panel");activity.replaceChildren(text("h3","Recorded activity"),text("p","Updates automatically while this chat is open. These are saved events from your Mac; live working status is checked separately.","panel-help"));
  const turn=data.last_recorded_turn;
  if(turn){const card=text("div",recordedLabels[turn.status]||turn.status,"turn-status "+turn.status);activity.append(card);}
  for(const plan of data.plans||[]){const card=document.createElement("div");card.className="plan-card";card.append(text("h4","Saved plan"));ChatMarkdown.render(card,plan.text);activity.append(card);}
  if(!data.activity?.length)activity.append(text("p","No tool or plan events are recorded for this chat.","empty-message"));
  for(const event of data.activity||[]){const row=document.createElement("div");row.className="activity-row";row.append(text("span",event.label),text("span",event.status,"event-status "+event.status),text("time",shortTime(event.timestamp)));activity.append(row);}
  if(data.activity?.length===100)activity.append(text("p","Showing the 100 most recent saved activity events.","panel-help"));
  const changes=el("changes-panel");
  const expanded=new Set([...changes.querySelectorAll("details[open]")].map(item=>item.dataset.path));
  changes.replaceChildren(text("h3","Recorded file changes"),text("p","Latest saved patch per file from recent activity. This shows what was recorded at the time; it does not compare the current working tree.","panel-help"));
  if(!data.changes?.length)changes.append(text("p","No file patches are present in this chat’s recent saved activity.","empty-message"));
  for(const change of data.changes||[]){const card=document.createElement("details");card.className="change-card";card.dataset.path=change.path;card.open=expanded.has(change.path);card.append(text("summary",change.path));card.append(text("p","Recorded status: "+change.status,"panel-help"));const pre=document.createElement("pre");pre.append(text("code",change.diff||"No patch text recorded."));card.append(pre);if(change.truncated)card.append(text("p","Patch preview shortened. Review the full change in the desktop app.","panel-help"));changes.append(card);}
  const details=el("details-panel");details.replaceChildren(text("h3","Conversation details"));
  const projectNames=projects.filter(project=>(data.project_keys||[data.project_key]).includes(project.id)).map(project=>project.name).join(", ");
  const fields=[["Chat",data.title],["ID",data.id],["Project",projectNames||data.workspace||"Without a project"],["Folder",data.details?.folder||"Not recorded"],["Model",data.control?.live?.model||data.details?.model||"Not recorded"],["Reasoning",data.control?.live?.effort||data.details?.reasoning_effort||"Not recorded"],["Branch",data.details?.branch||"Not recorded"],["Type",data.subagent?"Subagent chat":"Main chat"],["Archive",data.archived?"Archived":"Active"],["Source",data.details?.history_source||"Local desktop history"]];
  const list=document.createElement("dl");for(const [label,value]of fields)list.append(text("dt",label),text("dd",value));details.append(list);
  details.append(text("p","Older chats are grouped using saved project folders when no project ID was recorded. Projects sharing the same folder can contain the same chat.","panel-help"));
  details.append(text("h4","Available here"),text("p","Projects and folders, new local chats, active and archived chats, subagent chats, conversation history, recorded activity, saved patches, and photo messages. Connected chats support direct Send, Steer, and model selection.","panel-help"));
  details.append(text('h4','Questions for you'),text('p','Assistant questions include the same saved choices and a free-text answer. Replies use the desktop question format and go directly to a connected active chat. When live controls are disconnected, a reply is explicitly queued. A question clears after its reply is recorded. Live approvals have a separate Review panel. Secure macOS dialogs and sign-ins require your Mac.','panel-help'));
  details.append(text("h4","Your drafts and privacy"),text("p","Drafts stay with each chat while this page is open, including during a connection outage. Reloading or signing out clears them. Conversation text is read from your Mac; tool output and internal reasoning stay private. Detected credentials are hidden.","panel-help"));
  details.append(text('h4','Live controls'),text('p',data.control?.live?.model_selection?'The shared desktop connection can change this chat’s model for subsequent turns. Steer is available while an accepted active turn is running.':data.control?.live?.reason||'The shared desktop control connection is unavailable.','panel-help'));
  details.append(text('h4','Desktop apps while locked'),text('p',currentStatus?.computer_use?.state==='blocked'?'The last native Chrome test could not unlock the locked Mac, even though Locked Use was installed. Background work and direct message delivery are separate. Tap Mac battery above for the check details.':'Tap Mac battery above to see the last locked-screen desktop-app check.','panel-help'));
  details.append(text("h4","Continue in the desktop app"),text("p","Live stop, runtime approval requests, non-photo attachments, worktree management, and cloud or other-host chats still require the desktop app.","panel-help"));
  for(const [id,position]of positions)el(id).scrollTop=position;
}

async function selectChat(id,navigate=true) {
  if(el('approvals-dialog').open)el('approvals-dialog').close();el('approvals-banner').hidden=true;
  const reopening=document.body.classList.contains('home-open');
  if(el('questions-dialog').open)el('questions-dialog').close();el('questions-banner').hidden=true;questionSignature='';
  const url=new URL(location.href);url.searchParams.set("chat",id);
  if(navigate&&(!document.body.classList.contains("chat-open")||selected!==id)){
    if(!history.state?.companion){const listURL=new URL(location.href);listURL.searchParams.delete("chat");history.replaceState({companion:true,view:"list"},"",listURL.pathname+listURL.search);}
    history.pushState({companion:true,view:"chat",id},"",url.pathname+url.search);
  }
  document.body.classList.remove('home-open');el('home-screen').hidden=true;
  if(id===selected&&selectedData){document.activeElement?.blur();document.body.classList.add("chat-open");if(reopening){conversationNeedsScroll=true;conversationScroll.open();jumpLatest();}renderQuestions();updateComposer();return refreshChat();}
  document.activeElement?.blur();selectionVersion++;
  selected=id;conversationRevision=''; conversationScroll.open();selectedData=null;displayedRevision=null;deliveryMode='queue'; lastSignature="";panelSignature="";pendingSignature="";conversationNeedsScroll=true; messageMap.clear();nextBefore=null;loadedEarlier=false;loadingEarlier=false;el("command-result").textContent="";
  el("instruction").value=drafts.get(id)||"";
  el("instruction").disabled=true; el("send").disabled=true;
  el("messages").replaceChildren();el("pending").replaceChildren();el("title").textContent=chats.find(chat=>chat.id===id)?.title||"Loading conversation…";el("activity").textContent="Loading the recorded conversation…";
  for(const id of ["activity-panel","changes-panel","details-panel"])el(id).replaceChildren();
  document.body.classList.add("chat-open");
  setView("conversation");updateComposer();renderChats();
  return refreshChat();
}

function renderMessages(preservePosition=false){
  const messages=[...messageMap.values()].sort((a,b)=>(a.ordinal??a.timestamp??0)-(b.ordinal??b.timestamp??0));
  const receipts=deliveryReceipts.list(selected,messages),working=CompanionConversation.working(selectedData?.control?.live?.execution,connected);
  const signature=JSON.stringify([messages,nextBefore,loadingEarlier,receipts,working,selectedData?.control?.live?.execution?.approval_count]);
  if(signature===lastSignature)return;
  const panel=el("messages"),nearBottom=conversationScroll?.following()??panel.scrollHeight-panel.scrollTop-panel.clientHeight<100;
  const first=!lastSignature,oldTop=panel.scrollTop,top=panel.getBoundingClientRect().top;
  const existing=new Map([...panel.querySelectorAll("article[data-message]")].map(article=>[article.dataset.message,article]));
  const anchor=[...existing.values()].find(article=>article.getBoundingClientRect().bottom>top);
  const anchorOffset=anchor?anchor.getBoundingClientRect().top-top:0;
  let historyNote=panel.querySelector(".history-note");
  if(!historyNote){historyNote=document.createElement("div");historyNote.className="history-note";panel.prepend(historyNote);}
  const historySignature=JSON.stringify([nextBefore,loadingEarlier,Boolean(messages.length)]);
  if(historyNote.dataset.signature!==historySignature){historyNote.replaceChildren();historyNote.dataset.signature=historySignature;
  if(nextBefore!==null){
    const button=document.createElement("button");button.type="button";button.className="earlier-button";button.textContent=loadingEarlier?"Loading earlier messages…":"Load earlier messages";button.disabled=loadingEarlier;
    button.addEventListener("click",loadEarlier);historyNote.append(button);
  }else{const text=document.createElement("span");text.textContent=messages.length?"Beginning of the recorded conversation":"Conversation history";historyNote.append(text);}
  }
  let previous=historyNote;
  const retained=new Set();
  for(const message of messages){
    const key=String(message.id),signature=JSON.stringify(message);retained.add(key);
    let article=existing.get(key);
    if(!article||messageSignatures.get(article)!==signature){
    const replacement=document.createElement("article");replacement.className=`message ${message.role}`;replacement.dataset.message=key;messageSignatures.set(replacement,signature);
    const heading=document.createElement("div");heading.className="message-heading";
    const avatar=document.createElement("span");avatar.className="message-avatar";avatar.textContent=message.role==="user"?"Y":"DC";avatar.setAttribute("aria-hidden","true");
    const role=document.createElement("span");role.className="message-role";role.textContent=message.role==="user"?"You":"ChatGPT";
    const time=document.createElement("time");time.textContent=shortTime(message.timestamp);if(message.timestamp)time.dateTime=new Date(message.timestamp).toISOString();
    const copy=document.createElement("button");copy.type="button";copy.className="copy-message";copy.textContent="Copy";copy.setAttribute("aria-label","Copy message");
    copy.addEventListener("click",async()=>{try{await navigator.clipboard.writeText(message.text);copy.textContent="Copied";}catch(_){copy.textContent="Select to copy";}setTimeout(()=>{copy.textContent="Copy";},1800);});
    heading.append(avatar,role,time);
    if(message.phase==="commentary"){const phase=document.createElement("span");phase.className="message-phase";phase.textContent="Progress update";heading.append(phase);}
    heading.append(copy);
    const body=document.createElement("div");body.className="message-text";ChatMarkdown.render(body,message.text);
    replacement.append(heading,body);
    if(message.photos?.length){const photos=document.createElement('div');photos.className='message-photos';for(const photo of message.photos){if(!/^\/api\/photos\/[a-f0-9]{32}$/.test(photo.url))continue;const link=document.createElement('a');link.href=photo.url;link.target='_blank';link.rel='noopener noreferrer';const img=document.createElement('img');img.src=photo.url;img.alt=photo.name||'Attached photo';img.loading='lazy';link.append(img);photos.append(link);}replacement.append(photos);}
    if(article)article.replaceWith(replacement);article=replacement;
    }
    if(previous.nextElementSibling!==article)previous.after(article);
    previous=article;
  }
  for(const [key,article]of existing)if(!retained.has(key))article.remove();
  panel.querySelector(".empty-message")?.remove();
  if(!messages.length&&!receipts.length){const text=document.createElement("p");text.className="empty-message";text.textContent=selectedData?.archived?'No messages are saved in this chat.':'No messages yet. Write your first message below to get started.';panel.append(text);}
  renderConversationTail(panel,receipts,working);
  if((first||nearBottom)&&!preservePosition&&activeView==="conversation")jumpLatest();
  else if(anchor?.isConnected)panel.scrollTop=oldTop+anchor.getBoundingClientRect().top-top-anchorOffset;
  else panel.scrollTop=oldTop;
  lastSignature=signature;
  updateJump();
}

function renderConversationTail(panel,receipts,working){
  let tail=panel.querySelector('.conversation-live');
  if(!tail){tail=document.createElement('div');tail.className='conversation-live';panel.append(tail);}
  tail.replaceChildren();
  for(const receipt of receipts){
    const card=document.createElement('div');card.className='delivery-receipt';card.dataset.request=receipt.id;
    const text=document.createElement('p');text.textContent=receipt.text||'Photo attached.';
    const state=document.createElement('small');state.textContent=receipt.state==='accepted'?'Accepted by Mac · Syncing conversation…':'Sending to your Mac…';card.append(text,state);
    if(receipt.photos.length){const photos=document.createElement('div');photos.className='message-photos';for(const photo of receipt.photos){if(!/^\/api\/photos\/[a-f0-9]{32}$/.test(photo.url))continue;const img=document.createElement('img');img.src=photo.url;img.alt=photo.name||'Attached photo';photos.append(img);}card.append(photos);}
    tail.append(card);
  }
  if(working){const status=document.createElement('div');status.className='working-indicator';status.setAttribute('role','status');
    const orb=document.createElement('span');orb.className='working-orb';orb.setAttribute('aria-hidden','true');
    const text=document.createElement('span');text.textContent=selectedData?.control?.live?.execution?.approval_count?'Waiting for your response…':'Working on your Mac…';status.append(orb,text);tail.append(status);}
}

async function loadEarlier(){
  if(loadingEarlier||nextBefore===null||!selected)return;
  const id=selected,cursor=nextBefore,version=selectionVersion;conversationScroll.pause();loadingEarlier=true;renderMessages();
  try{
    const data=await api(`/api/chat?id=${encodeURIComponent(id)}&before=${cursor}`);
    if(id!==selected||version!==selectionVersion)return;
    for(const message of data.messages)messageMap.set(message.id,message);
    nextBefore=data.next_before??null;loadedEarlier=true;loadingEarlier=false;renderMessages(true);
  }catch(error){if(id===selected&&version===selectionVersion){loadingEarlier=false;renderMessages();el("command-result").textContent=`Earlier messages could not load: ${error.message}`;}}
}

async function refreshChat(fresh=false){
  if(!selected||document.body.classList.contains('home-open'))return true;
  const key=authVersion+':'+selectionVersion+':'+selected;
  if(chatRefreshPromise?.key===key){
    const previous=chatRefreshPromise.promise;
    if(!fresh)return previous;
    await previous;
    if(key!==authVersion+':'+selectionVersion+':'+selected)return;
    return refreshChat();
  }
  const task=readCurrentChat();chatRefreshPromise={key,promise:task};
  try{return await task;}finally{if(chatRefreshPromise?.promise===task)chatRefreshPromise=null;}
}
async function readCurrentChat() {
  if (!selected||document.body.classList.contains('home-open')) return true;
  const id=selected,version=selectionVersion,request=++chatRequestVersion;
  try {
    const data=await api(`/api/chat?id=${encodeURIComponent(id)}`,conversationRevision?{headers:{'If-None-Match':conversationRevision}}:{});
    if(id!==selected||version!==selectionVersion||request!==chatRequestVersion||document.body.classList.contains('home-open'))return;
    if(data.unchanged&&selectedData){
      if(selectedData.control?.live?.execution&&Number.isFinite(data.checked_at)&&data.checked_at>0)selectedData.control.live.execution.checked_at=data.checked_at;
      renderMessages();if(conversationNeedsScroll&&activeView==='conversation')jumpLatest();return true;
    }
    conversationRevision=data.view_revision||'';selectedData=data;
    if(connected)showNotice('');
    if(data.control.live?.execution&&currentStatus){currentStatus.execution=currentStatus.execution||{};currentStatus.execution[id]=data.control.live.execution;}
    renderQuestions();
    renderPanels(data);
    el("title").textContent=data.title; el("workspace").textContent=data.workspace||"LOCAL CHAT";
    const labels={inProgress:"In progress at last recorded update",completed:"Last turn completed",interrupted:"Last turn interrupted",failed:"Last turn failed"};
    const execution=data.control.live?.execution;
    const status=execution?.approval_count?'Waiting for your response':execution?.status==='working'?'Working now':execution?.status==='idle'?'Ready for your message':data.last_recorded_turn?.status==='inProgress'?'Live status unavailable':labels[data.last_recorded_turn?.status]||'Recorded conversation';
    const timestamps=data.messages.map(message=>message.timestamp||0);
    const latest=Math.max(data.updated_at||0,...timestamps);
    el("activity").textContent=`${status} · Updated ${shortTime(latest)}`;
    const available=data.control.available;
    el("control-status").textContent=available?(deliveryMode==='steer'?(data.control.live?.steer?'Send a correction to the active turn.':data.control.live?.reason||'Steering is unavailable. Choose Queue to send later.'):'Follow-ups wait in this chat’s desktop queue.'):data.control.reason;
    el("instruction").placeholder=available?"Message or paste a photo…":"Draft a message…";
    const signature=JSON.stringify([data.pending_instructions,data.control.live?.queue_steer,data.queue_recovery,[...queueSteering]]);
    if(signature!==pendingSignature){pendingSignature=signature;el("pending").replaceChildren();
      const queued=queueUI.render(data);if(queued)el('pending').append(queued);
      let savedVersions=null;
      for(const recovery of data.queue_recovery||[]){const card=document.createElement('details');card.className='queue-recovery';const title=document.createElement('summary');title.textContent=recovery.title||'Delivery needs review';const note=document.createElement('p');note.textContent=recovery.message;const text=document.createElement('p');text.className='pending-text';text.textContent=recovery.text;const actions=document.createElement('div');actions.className='recovery-actions';const button=document.createElement('button');button.type='button';button.className='quiet-button';button.textContent='Copy to draft';button.addEventListener('click',()=>{if(((drafts.get(data.id)||'').trim()||(photoDrafts.get(data.id)||[]).length)&&!window.confirm('Replace the current draft and photos with this saved queue version?'))return;drafts.set(data.id,recovery.text);photoDrafts.set(data.id,recovery.photos||[]);el('instruction').value=recovery.text;showCommandResult('Review the conversation before sending this text again.');updateComposer();});const dismiss=document.createElement('button');dismiss.type='button';dismiss.className='quiet-button';dismiss.textContent='Dismiss';dismiss.addEventListener('click',async()=>{dismiss.disabled=true;try{await api('/api/queue/recovery/dismiss',{method:'POST',headers:{'Content-Type':'application/json','X-Companion-CSRF':csrf},body:JSON.stringify({thread_id:data.id,queue_id:recovery.id})});pendingSignature='';await refreshChat();}catch(error){dismiss.disabled=false;showCommandResult(error.message);}});actions.append(button,dismiss);card.append(title,note,text,actions);if(recovery.confirmed){if(!savedVersions){savedVersions=document.createElement('details');savedVersions.className='queue-recovery queue-versions';const heading=document.createElement('summary');heading.textContent='Previous queue versions';savedVersions.append(heading);el('pending').append(savedVersions);}savedVersions.append(card);}else el('pending').append(card);}

    }
    for(const message of data.messages)messageMap.set(message.id,message);
    if(!loadedEarlier)nextBefore=data.next_before??null;
    renderMessages();
    displayedRevision=data.message_revision;
    updateComposer();
    if(conversationNeedsScroll&&activeView==='conversation')jumpLatest();
    scheduleMarkViewed();
    if(el('model-dialog').open)renderModelDialog();
    return true;
  }catch(error){if(id===selected&&version===selectionVersion&&request===chatRequestVersion){
    if(selectedData)selectedData={...selectedData,control:{...selectedData.control,available:false,reason:'Reconnecting. Your draft is kept; sending resumes after a fresh check.',live:{connected:false,execution:{status:'unknown'}}}};
    conversationRevision='';displayedRevision=null;
    if(currentStatus?.execution)currentStatus.execution[id]={status:'unknown'};
    el('activity').textContent='Reconnecting · showing the last saved update';
    if(selectedData)renderPanels(selectedData);
    renderQuestions();renderMessages();updateComposer();
    el("control-status").textContent='Reconnecting. Your draft is kept; sending resumes after a fresh check.';
    showNotice(`Showing the last conversation update. ${error.message}`);
  }return false;}
}

async function refreshAll(force=false,skipChat=false){
  if(force===true)catalogRevision='';
  if(refreshPromise)return refreshPromise;
  const task=refreshWorkspace(skipChat);refreshPromise=task;
  try{return await task;}finally{if(refreshPromise===task)refreshPromise=null;}
}
async function refreshWorkspace(skipChat=false){
  refreshing=true;
  const version=authVersion;
  try{
    const [status,list]=await Promise.all([api("/api/status"),api("/api/chats",catalogRevision?{headers:{'If-None-Match':'"'+catalogRevision+'"'}}:{})]);
    if(version!==authVersion)return;
    connected=true;
    currentStatus=status;csrf=status.csrf;if(updater?.check(status.build))return;if(!list.unchanged){chats=list.chats;projects=list.projects||[];catalogRevision=list.revision||'';}applyReadAcknowledgements();renderProjects();renderChats();renderUsage();el('new-chat').disabled=!status.control?.available;
    document.body.classList.remove("booting","signed-out","disconnected");document.body.classList.add("ready");
    el("phone-setup").hidden=!status.phone_setup_available;el('sign-out-area').hidden=status.mode!=="remote";el('account-description').textContent=status.mode==='remote'?'This browser is paired with your Mac.':'You’re viewing Companion on this Mac.';
    el("location-badge").textContent=status.mode==="remote"?"Paired browser":"On this Mac";
    renderPower(status.power||{});
    el("connection").textContent="History connected";el("connection-dot").className="dot connected";
    el("footer-connection").textContent="Connected to your Mac";
    el("sidebar-retry").hidden=true;
    showNotice("");
    if(status.phone_setup_available&&el("phone-dialog").open)await showDevices();
    if(el('host-dialog').open)renderHostStatus();
    if(initialSelectionPending){initialSelectionPending=false;const requested=new URLSearchParams(location.search).get("chat");const target=chats.find(chat=>chat.id===requested);if(target?.archived||target?.subagent){el("chat-filter").value=target.archived?"archived":"subagents";renderChats();}if(target)await selectChat(target.id,Boolean(mobile()&&!history.state?.companion));else{document.body.classList.add('home-open');el('home-screen').hidden=false;}}
    else if(!skipChat&&await refreshChat()===false)return false;
    updateComposer();
    return true;
  }catch(error){if(version!==authVersion)return;connected=false;renderChats();renderQuestions();renderPower(currentStatus?.power||{},true);if(currentStatus?.usage){currentStatus.usage.stale=true;renderUsage();}document.body.classList.add("disconnected");el("footer-connection").textContent="Connection interrupted";el("sidebar-retry").hidden=false;el("connection").textContent="Disconnected";el("connection-dot").className="dot offline";showNotice(`Updates paused. ${error.message}`);el("control-status").textContent="You can keep drafting. Reconnect to queue your follow-up.";updateComposer();if(document.body.classList.contains("booting")){el("startup-message").textContent=error.message;el("startup-retry").hidden=false;}return false;}
  finally{refreshing=false;}
}

el("search").addEventListener("input",renderChats);
el('home').addEventListener('click',goHome);
for(const view of ['recent','unread','working','reply'])el('quick-'+view).addEventListener('click',()=>chooseInbox(view));
el('weekly-usage').addEventListener('click',()=>{renderUsage();el('usage-dialog').showModal();});el('usage-close').addEventListener('click',()=>el('usage-dialog').close());
el('mark-all-read').addEventListener('click',async()=>{
  if(markingAll||!connected)return;markingAll=true;el('inbox-message').textContent='';renderHome();const version=authVersion,targets=visibleChats().filter(chat=>chat.unread);
  try{for(const chat of targets){await api('/api/read-state',{method:'POST',headers:{'Content-Type':'application/json','X-Companion-CSRF':csrf},body:JSON.stringify({thread_id:chat.id,revision:chat.message_revision,operation:'read'})});if(version!==authVersion)return;chat.unread=false;readAcknowledgements.set(chat.id,chat.message_revision);}renderChats();}
  catch(error){if(version===authVersion)el('inbox-message').textContent=error.message;}
  finally{markingAll=false;renderHome();}
});
for(const mode of ['queue','steer'])el('mode-'+mode).addEventListener('click',()=>{if(mode==='steer'&&!selectedData?.control?.live?.steer)return;deliveryMode=mode;el('control-status').textContent=mode==='steer'?'Send a correction to the active turn.':'Follow-ups wait in this chat’s desktop queue.';el('command-result').textContent='';updateComposer();});
el('model-trigger').addEventListener('click',()=>{el('model-choice').replaceChildren();el('effort-choice').replaceChildren();el('model-result').textContent='';renderModelDialog();el('model-dialog').showModal();});
el('model-close').addEventListener('click',()=>el('model-dialog').close());el('model-choice').addEventListener('change',()=>renderEfforts(true));
el('model-form').addEventListener('submit',async event=>{
  event.preventDefault();if(modelSaving||!connected||!selectedData?.control?.live?.model_selection)return;
  const target=selected,version=authVersion,model=el('model-choice').value,effort=el('effort-choice').value||null;
  const key=JSON.stringify([target,model,effort]);let id=modelAttempts.get(key);if(!id){id=crypto.randomUUID();modelAttempts.set(key,id);}
  modelSaving=true;el('model-apply').disabled=true;el('model-result').textContent='Saving this chat’s model…';
  try{const result=await api('/api/model',{method:'POST',headers:{'Content-Type':'application/json','X-Companion-CSRF':csrf},body:JSON.stringify({thread_id:target,model,effort,command_id:id})});if(version!==authVersion)return;modelAttempts.delete(key);if(target===selected){el('model-result').textContent=result.message;await refreshChat();}}
  catch(error){if(version===authVersion)el('model-result').textContent=error.message;}
  finally{modelSaving=false;if(version===authVersion)renderModelDialog();}
});
el("instruction").addEventListener("input",()=>{if(selected)drafts.set(selected,el("instruction").value);updateComposer();});
el("instruction").addEventListener("keydown",event=>{if(event.key==="Enter"&&(event.metaKey||event.ctrlKey)&&!event.isComposing){event.preventDefault();if(!el("send").disabled)el("composer").requestSubmit();}});
el("project-filter").addEventListener("change",applyFilter);el("chat-filter").addEventListener("change",applyFilter);
el("projects-toggle").addEventListener("click",()=>{el("projects").hidden=!el("projects").hidden;el("projects-toggle").setAttribute("aria-pressed",String(!el("projects").hidden));});
const views=["conversation","activity","changes","details"];
for(const name of views){const tab=el("tab-"+name);tab.addEventListener("click",()=>setView(name));tab.addEventListener("keydown",event=>{const index=views.indexOf(name);let next;if(event.key==="ArrowRight")next=(index+1)%views.length;else if(event.key==="ArrowLeft")next=(index+views.length-1)%views.length;else if(event.key==="Home")next=0;else if(event.key==="End")next=views.length-1;else return;event.preventDefault();setView(views[next]);el("tab-"+views[next]).focus();});}
el("refresh").addEventListener("click",refreshAll);
el("notice-retry").addEventListener("click",refreshAll);
el("sidebar-retry").addEventListener("click",refreshAll);
if(window.CompanionRefresh)CompanionRefresh.install({refresh:()=>refreshAll(true),reload:()=>updater?.reload(),isMobile:mobile});
else el('manual-refresh').addEventListener('click',refreshAll);
for(const dialog of document.querySelectorAll('dialog'))dialog.addEventListener('close',scheduleMarkViewed);
function showList(){document.activeElement?.blur();document.body.classList.remove("chat-open");document.body.classList.add('home-open');el('home-screen').hidden=false;renderHome();renderChats();}
function newChatProject(){return el('project-filter').value||(!document.body.classList.contains('home-open')?selectedData?.project_key:'')||'personal';}
function post(path,payload){return api(path,{method:'POST',headers:{'Content-Type':'application/json','X-Companion-CSRF':csrf},body:JSON.stringify(payload)});}
if(window.CompanionNewChat)newChatFlow=CompanionNewChat.install({api,post,preparePhoto,isConnected:()=>connected,getVersion:()=>authVersion,onCreated:async(result,draft,submitted)=>{
  if(result.delivery?.state==='sent'&&submitted){deliveryReceipts.begin(result.thread_id,'initial:'+result.thread_id,submitted.message,submitted.photos);deliveryReceipts.settle(result.thread_id,'initial:'+result.thread_id,result.delivery);}
  inboxView='recent';el('search').value='';el('project-filter').value='';el('chat-filter').value='active';
  await refreshAll(true);await selectChat(result.thread_id);
  if(draft){drafts.set(result.thread_id,draft.message);photoDrafts.set(result.thread_id,draft.photos);el('instruction').value=draft.message;updateComposer();}
  showCommandResult(result.delivery?.message||result.message,result.delivery?.state==='sent');
}});
el('new-chat').addEventListener('click',()=>newChatFlow?.open(newChatProject()));
el('chat-options').addEventListener('click',()=>{if(!selectedData)return;el('chat-actions-title').textContent=selectedData.title;el('chat-actions-result').textContent='';el('rename-form').hidden=true;el('chat-link-fallback').hidden=true;el('chat-rename').disabled=!selectedData.control?.available;el('chat-new').disabled=!currentStatus?.control?.available;el('chat-actions-dialog').showModal();});
el('chat-actions-close').addEventListener('click',()=>{if(!renamingChat)el('chat-actions-dialog').close();});
el('chat-actions-dialog').addEventListener('cancel',event=>{if(renamingChat)event.preventDefault();});
el('chat-rename').addEventListener('click',()=>{el('rename-title').value=selectedData?.title||'';el('rename-form').hidden=false;el('rename-title').focus();el('rename-title').select();});
el('rename-cancel').addEventListener('click',()=>el('rename-form').hidden=true);
el('chat-new').addEventListener('click',()=>{const project=selectedData?.project_key||'personal';el('chat-actions-dialog').close();newChatFlow?.open(project);});
el('chat-copy-link').addEventListener('click',async()=>{const url=new URL(currentStatus?.public_origin||location.origin);url.searchParams.set('chat',selected);try{await navigator.clipboard.writeText(url.href);el('chat-actions-result').textContent='Chat link copied. Opening it still requires a paired browser.';}catch(_){el('chat-link-fallback').value=url.href;el('chat-link-fallback').hidden=false;el('chat-link-fallback').select();el('chat-actions-result').textContent='Select and copy this chat link.';}});
el('rename-form').addEventListener('submit',async event=>{event.preventDefault();if(renamingChat||!selected)return;const target=selected,title=el('rename-title').value.trim();if(!title)return;renamingChat=true;for(const id of ['rename-save','rename-cancel','rename-title','chat-actions-close'])el(id).disabled=true;el('chat-actions-result').textContent='Saving name…';try{const result=await post('/api/chat/rename',{command_id:crypto.randomUUID(),thread_id:target,title});catalogRevision='';await refreshAll(true);el('rename-form').hidden=true;el('chat-actions-title').textContent=result.title;el('chat-actions-result').textContent=result.message;}catch(error){el('chat-actions-result').textContent=error.message;}finally{renamingChat=false;for(const id of ['rename-save','rename-cancel','rename-title','chat-actions-close'])el(id).disabled=false;}});
el("back").addEventListener("click",()=>{if(history.state?.view==="chat")history.back();else{showList();const url=new URL(location.href);url.searchParams.delete("chat");history.replaceState({companion:true,view:"list"},"",url.pathname+url.search);}});
window.addEventListener("popstate",()=>{const id=new URLSearchParams(location.search).get("chat");if(id&&chats.some(chat=>chat.id===id))selectChat(id,false);else showList();});
el("login-form").addEventListener("submit",async event=>{
  event.preventDefault();el("login-submit").disabled=true;el("login-error").textContent="";
  try{await api("/api/auth/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({code:el("pairing-code-input").value,name:el("device-name").value})});el("pairing-code-input").value="";if(mediaReturn()){location.assign(mediaReturn());return;}await refreshAll();}
  catch(error){el("login-error").textContent=error.message;}
  finally{el("login-submit").disabled=false;}
});
el('account-trigger').addEventListener('click',()=>{el('reload-result').textContent='';el('refresh-help').textContent=CompanionShell.standalone()?'Tap ↻ to update without losing your draft. Pull down from the app header to reload the full page. Pull inside a list/reader at its top for a soft refresh. Reloading asks before clearing drafts or photos.':'Tap ↻ or pull down inside a list/reader at its top to update without losing your draft. Pulling from the header keeps the browser’s own refresh gesture. Use Reload app for a full interface reload.';el('account-dialog').showModal();});
el('account-close').addEventListener('click',()=>el('account-dialog').close());
if(window.CompanionHealth)healthFlow=CompanionHealth.install({api,getVersion:()=>authVersion});
el('system-open').addEventListener('click',()=>{el('account-dialog').close();healthFlow?.open();});
el("logout").addEventListener("click",async()=>{try{await api("/api/auth/logout",{method:"POST",headers:{"X-Companion-CSRF":csrf}});lockScreen();}catch(error){showNotice(error.message);}});
async function showDevices(){
  const result=await api("/api/devices");el("devices").replaceChildren();
  if(!result.devices.length){const text=document.createElement("p");text.textContent="No browsers are currently paired.";el("devices").append(text);}
  for(const device of result.devices){
    const row=document.createElement("div");row.className="device-row";
    const label=document.createElement("span");label.textContent=`${device.name} · Expires ${shortTime(device.expires*1000)}`;
    const revoke=document.createElement("button");revoke.className="quiet-button";revoke.textContent="Revoke";
    revoke.addEventListener("click",async()=>{try{await api("/api/devices/revoke",{method:"POST",headers:{"Content-Type":"application/json","X-Companion-CSRF":csrf},body:JSON.stringify({device_id:device.id})});await showDevices();}catch(error){el("device-error").textContent=error.message;}});
    row.append(label,revoke);el("devices").append(row);
  }
}
el("phone-setup").addEventListener("click",async()=>{el('account-dialog').close();
  el("phone-address").textContent=currentStatus?.public_origin||"Remote address not yet active";
  el("phone-help").textContent=currentStatus?.public_origin?"Pairing grants access to your conversation text and instruction queue. Your Mac must remain awake and connected.":"The authenticated gateway is ready locally. A remote connection still needs to be started.";
  el("pair-create").disabled=!currentStatus?.public_origin;el("private-code-area").hidden=true;el("private-pairing-code").textContent="";el("device-error").textContent="";
  el("phone-dialog").showModal();try{await showDevices();}catch(error){el("device-error").textContent=error.message;}
});
el("pair-create").addEventListener("click",async()=>{
  el("pair-create").disabled=true;
  try{
    const result=await api("/api/pairing",{method:"POST",headers:{"X-Companion-CSRF":csrf}});
    el("private-pairing-code").textContent=result.code;el("private-code-area").hidden=false;
    clearTimeout(pairingTimer);pairingTimer=setTimeout(()=>{el("private-pairing-code").textContent="Code expired";},Math.max(0,result.expires_at-Date.now()));
  }catch(error){el("device-error").textContent=error.message;}
  finally{el("pair-create").disabled=false;}
});
el("phone-close").addEventListener("click",()=>el("phone-dialog").close());
el("phone-dialog").addEventListener("close",()=>{
  clearTimeout(pairingTimer);el("private-pairing-code").textContent="";el("private-code-area").hidden=true;
  api("/api/pairing/cancel",{method:"POST",headers:{"X-Companion-CSRF":csrf}}).catch(()=>{});
});
el("composer").addEventListener("submit",async event=>{
  event.preventDefault();
  const message=el("instruction").value.trim();
  const photos=(photoDrafts.get(selected)||[]).map(photo=>photo.id);
  if((!message&&!photos.length)||sending||photoUploads.get(selected)||!connected||!selectedData?.control.available)return;
  const turn=selectedData?.control.live?.execution||selectedData?.last_recorded_turn;
  const target=selected,version=authVersion,original=el("instruction").value;
  let attempt=commandAttempts.get(target);
  if(!attempt||attempt.message!==message||JSON.stringify(attempt.photos)!==JSON.stringify(photos)){attempt={message,mode:deliveryMode,photos,turn:turn?.turn_id,id:crypto.randomUUID()};commandAttempts.set(target,attempt);}
  const mode=attempt.mode;
  if(mode==='steer'&&!selectedData.control.live?.steer&&!attempt.retrying)return;
  deliveryReceipts.begin(target,attempt.id,message,(photoDrafts.get(target)||[]).filter(photo=>photos.includes(photo.id)));
  sending=true;renderMessages();jumpLatest();updateComposer();
  el("command-result").textContent=mode==='steer'?'Sending to the active desktop turn…':mode==='send'?'Starting the next desktop turn…':"Queueing for the selected desktop chat…";
  try{
    const payload={command_id:attempt.id,thread_id:target,message,mode,photos};if(mode==='steer')payload.expected_turn_id=attempt.turn;
    const result=await api("/api/command",{method:"POST",headers:{"Content-Type":"application/json","X-Companion-CSRF":csrf},body:JSON.stringify(payload)});
    if(version!==authVersion)return;
    deliveryReceipts.settle(target,attempt.id,result);
    commandAttempts.delete(target);
    if(result.state==='queued')queueUI.reveal();
    photoDrafts.set(target,(photoDrafts.get(target)||[]).filter(photo=>!photos.includes(photo.id)));
    if(drafts.get(target)===original)drafts.delete(target);
    if(selected===target){showCommandResult(result.message,true);if(el("instruction").value===original)el("instruction").value="";}
  }catch(error){attempt.retrying=true;const rejected=error.result?.state==='rejected'||error.status>=400&&error.status<500&&error.result?.state!=='unknown'&&error.status!==429;if(rejected)commandAttempts.delete(target);deliveryReceipts.remove(attempt.id);if(version===authVersion&&selected===target)el("command-result").textContent=rejected?`${error.message} Your draft is kept. Refresh and send again when ready.`:`${error.message} Your draft is kept. Check ${mode==='queue'?'the queued follow-ups':'the desktop conversation'} before retrying; an unchanged retry uses the same request ID.`;}
  finally{sending=false;if(version===authVersion){renderMessages();updateComposer();await refreshChat(true);}}
});
el('connect-chat').addEventListener('click',async()=>{if(connectingChat||!selected)return;const target=selected;connectingChat=true;updateComposer();try{const result=await api('/api/chat/connect',{method:'POST',headers:{'Content-Type':'application/json','X-Companion-CSRF':csrf},body:JSON.stringify({thread_id:target})});if(target===selected){showCommandResult(result.message,true);await refreshChat();}}catch(error){if(target===selected)showCommandResult(error.message);}finally{connectingChat=false;updateComposer();}});
if(window.CompanionUpdates)updater=CompanionUpdates.install({loadedBuild,hasDraft:hasUnsentDraft,isBusy:()=>photoStorageBusy||approvalUI?.busy()||sending||deliveryReceipts.hasPending()||photoUploads.size>0||questionBusy.size>0||queueUI?.isBusy()||modelSaving||renamingChat||newChatFlow?.isBusy()||connectingChat,isInteracting:()=>Boolean(document.querySelector('dialog[open]'))||document.activeElement?.matches('input,textarea,select'),onBusy:()=>{const message='Wait for the current operation to finish before reloading.';if(el('account-dialog').open)el('reload-result').textContent=message;else if(document.body.classList.contains('home-open')||!selected)showNotice(message);else showCommandResult(message);}});
el('app-reload').addEventListener('click',()=>updater?.reload());
window.addEventListener('pageshow',event=>{if(event.persisted&&!document.body.classList.contains('signed-out'))refreshAll(true);});
function wakeReads(){workspacePoll.wake();conversationPoll.wake();}
function pauseReads(){workspacePoll.pause();conversationPoll.pause();}
document.addEventListener("visibilitychange",()=>{if(document.hidden)pauseReads();else if(!document.body.classList.contains("signed-out")){scheduleMarkViewed();wakeReads();}});
window.addEventListener('online',wakeReads);
window.addEventListener('offline',pauseReads);
const pollingEnabled=()=>!document.hidden&&navigator.onLine!==false&&!document.body.classList.contains('signed-out');
const workspacePoll=CompanionConversation.poll({enabled:pollingEnabled,read:()=>document.body.classList.contains('booting')?initialize():refreshAll(false,true),delay:()=>inConversation()?15000:5000,retryAfter:()=>Math.max(0,readRetryAt-Date.now())});
const conversationPoll=CompanionConversation.poll({enabled:()=>pollingEnabled()&&Boolean(selected)&&!document.body.classList.contains('home-open'),read:refreshChat,delay:()=>sending||deliveryReceipts.list(selected).length||CompanionConversation.working(selectedData?.control?.live?.execution,connected)?750:3000,retryAfter:()=>Math.max(0,readRetryAt-Date.now())});
function mediaReturn(){const id=new URLSearchParams(location.search).get('media');return /^[a-z0-9][a-z0-9-]{0,79}$/.test(id||'')?'/media?item='+id:null;}
async function initialize(){if(starting)return;starting=true;el("startup-retry").hidden=true;try{const auth=await api("/api/auth/status");if(auth.authenticated){if(mediaReturn()){location.assign(mediaReturn());return;}return await refreshAll();}else lockScreen();}catch(error){el("startup-message").textContent=error.message;el("startup-retry").hidden=false;return false;}finally{starting=false;}}
el("startup-retry").addEventListener("click",initialize);
initialize();
