"use strict";
// Pure presentation rules: unknown and stale telemetry never imply healthy status.
window.CompanionStatus={
 power(power={},stale=false){
  const percent=Number.isFinite(power.battery_percent)&&power.battery_percent>=0&&power.battery_percent<=100?power.battery_percent:null;
  const plugged=power.source==='adapter',charging=plugged&&power.charging===true;
  const tone=stale?'neutral':percent!==null&&percent<20?'danger':charging?'positive':plugged?'warning':'neutral';
  const source=plugged?(charging?'Charging':power.charging===false?'Plugged in, not charging':'Plugged in; charging status unavailable'):power.source==='battery'?'On battery':'Power source unavailable';
  const lid=power.lid_closed===true?'Lid closed':power.lid_closed===false?'Lid open':'';
  return {battery:'Mac '+(percent===null?'—':percent+'%'),percent,lid,tone,charging:charging&&!stale,title:(stale?'Last known: ':'')+source+(percent===null?'':' · '+percent+'%')+(lid?' · '+lid:'')};
 },
 allowance(percent,stale=false){
  const valid=Number.isFinite(percent)&&percent>=0&&percent<=100;
  return {value:valid?percent:null,tone:!valid||stale?'neutral':percent<=10?'danger':percent<=25?'warning':'positive'};
 }
};

// The connection path matches the native overview; support nodes show dependencies.
window.CompanionSystemMap={install(){
 const el=id=>document.getElementById(id),root=el('system-map'),ns='http://www.w3.org/2000/svg';
 const states={ready:'Ready',warning:'Attention',stopped:'Stopped',disabled:'Off',unknown:'Unavailable'};
 const definitions=new Map([
  ['phone',['Phone access','blue','M8 2h8a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1Z M11 18h2']],
  ['cloudflare',['Cloudflare','orange','M6 18h12a4 4 0 0 0 0-8 6 6 0 0 0-11-2 5 5 0 0 0-1 10Z']],
  ['tunnel',['Tunnel','teal','M8 7h-2a5 5 0 0 0 0 10h2 M16 7h2a5 5 0 0 1 0 10h-2 M8 12h8']],
  ['desktop',['ChatGPT','purple','M4 4h16v12H9l-5 4V4Z M8 8h8 M8 12h5']],
  ['server',['Companion','purple','M3 4h18v6H3z M3 14h18v6H3z M7 7h.01 M7 17h.01 M11 7h6 M11 17h6']],
  ['gateway',['Gateway','teal','M12 2 3 6v5c0 5 4 9 9 11 5-2 9-6 9-11V6l-9-4Z M8 12l3 3 5-6']],
  ['network',['Network','blue','M2 8a16 16 0 0 1 20 0 M5 12a11 11 0 0 1 14 0 M8 16a6 6 0 0 1 8 0 M12 20h.01']],
  ['background',['Recovery','purple','M20 7v5h-5 M4 17v-5h5 M6 6a8 8 0 0 1 13 4 M18 18a8 8 0 0 1-13-4']],
  ['awake',['Awake','orange','M3 6h16v12H3z M22 10v4 M11 8l-3 5h4l-1 3 4-6h-4l1-2']],
  ['lid',['Lid mode','orange','M5 4h14v12H5z M2 20h20 M3 16h18l1 4H2l1-4Z']],
  ['history',['History','blue','M3 11a9 9 0 1 1 2 7 M3 5v6h6 M12 7v6l4 2']],
  ['access',['Access','teal','M14 7a5 5 0 1 1-10 0 5 5 0 0 1 10 0Z M12 11l9 9 M17 16l3-3 M7 6h.01']],
  ['queue',['Delivery','pink','M3 5h12 M3 10h9 M3 15h6 M14 11l6 5-6 5 M10 16h10']]
 ]);
 const path=['phone','cloudflare','tunnel','desktop','server','gateway'],support=['network','background','awake','lid','history','access','queue'];
 const links=[['phone','cloudflare','M45 39H150','M95 36l4 3-4 3'],['cloudflare','tunnel','M150 39H255','M200 36l4 3-4 3'],['tunnel','gateway','M255 39V145','M252 89l3 4 3-4'],['gateway','server','M255 145H150','M205 142l-4 3 4 3'],['server','desktop','M150 145H45','M100 142l-4 3 4 3']];
 let signature='',selected=null,items=new Map(),buttons=new Map();
 function icon(definition){const svg=document.createElementNS(ns,'svg');for(const [key,value]of Object.entries({viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':'1.7','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'}))svg.setAttribute(key,value);const shape=document.createElementNS(ns,'path');shape.setAttribute('d',definition[2]);svg.append(shape);return svg;}
 function detail(scroll=false){
  const item=items.get(selected);if(!item)return;const panel=el('system-map-detail');panel.hidden=false;panel.dataset.state=item.state;
  el('system-map-detail-title').textContent=item.title;el('system-map-detail-state').textContent=states[item.state];el('system-map-detail-text').textContent=item.detail;
  for(const [id,button]of buttons)button.setAttribute('aria-pressed',String(id===selected));
  if(scroll&&panel.scrollIntoView)panel.scrollIntoView({block:'nearest',behavior:window.matchMedia?.('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
 }
 function node(id){
  const item=items.get(id),definition=definitions.get(id)||[item.title,'purple',definitions.get('server')[2]],button=document.createElement('button');button.type='button';button.className='map-node';button.dataset.state=item.state;button.dataset.kind=definition[1];button.dataset.mapComponent=id;button.setAttribute('aria-label',item.title+': '+states[item.state]);button.setAttribute('aria-controls','system-map-detail');button.setAttribute('aria-pressed',String(id===selected));
  const mark=document.createElement('span');mark.className='map-node-icon';mark.append(icon(definition));const title=document.createElement('span');title.className='map-node-title';title.textContent=definition[0];const state=document.createElement('span');state.className='map-node-state';state.textContent=(item.state==='ready'?'✓ ':item.state==='warning'?'! ':item.state==='stopped'?'× ':'')+states[item.state];button.append(mark,title,state);
  button.addEventListener('click',()=>{selected=id;detail(true);});buttons.set(id,button);return button;
 }
 function render(components){
  const incoming=new Map((Array.isArray(components)?components:[]).filter(item=>item&&typeof item.id==='string').slice(0,24).map(item=>[item.id,item]));
  const order=[...definitions.keys(),...[...incoming.keys()].filter(id=>!definitions.has(id))];
  items=new Map(order.map(id=>{const raw=incoming.get(id),definition=definitions.get(id);return[id,{id,title:String(raw?.title||definition?.[0]||'Component').slice(0,120),detail:String(raw?.detail||'No current check reported.').slice(0,1200),state:raw&&Object.hasOwn(states,raw.state)?raw.state:'unknown'}];}));
  const values=[...items.values()],ready=values.filter(item=>item.state==='ready').length,disabled=values.filter(item=>item.state==='disabled').length,counts={ready,disabled,total:items.size,attention:items.size-ready-disabled};
  root.hidden=false;root.dataset.stale='false';root.dataset.mapState=counts.attention?'warning':'ready';
  el('system-map-count').textContent=ready+' / '+items.size+' ready';el('system-map-progress').max=items.size;el('system-map-progress').value=ready;el('system-map-progress').setAttribute('aria-label',ready+' of '+items.size+' components ready');
  const next=JSON.stringify([...items.values()]);if(next===signature)return counts;signature=next;const focused=document.activeElement?.dataset?.mapComponent;buttons=new Map();
  el('system-map-path').replaceChildren(...path.map(node));el('system-map-support').replaceChildren(...[...support,...order.filter(id=>!definitions.has(id))].map(node));
  const drawing=el('system-map-links');drawing.replaceChildren();
  for(const [from,to,line,arrow]of links){const a=items.get(from).state,b=items.get(to).state,state=a==='ready'&&b==='ready'?'ready':[a,b].includes('stopped')?'stopped':[a,b].includes('warning')?'warning':'unknown';const group=document.createElementNS(ns,'g');group.dataset.state=state;for(const d of [line,arrow]){const shape=document.createElementNS(ns,'path');shape.setAttribute('d',d);group.append(shape);}drawing.append(group);}
  if(selected)detail();if(focused)buttons.get(focused)?.focus({preventScroll:true});return counts;
 }
 return {render,stale(){root.dataset.stale='true';el('system-map-count').textContent='Last known · '+el('system-map-count').textContent.replace(/^Last known · /,'');},reset(){signature='';selected=null;items=new Map();buttons=new Map();root.hidden=true;el('system-map-path').replaceChildren();el('system-map-support').replaceChildren();el('system-map-links').replaceChildren();el('system-map-detail').hidden=true;}};
}};

// Fetch the more expensive component checks only while this dialog is in use.
window.CompanionHealth={install({api,getVersion}){
 const el=id=>document.getElementById(id),dialog=el('system-dialog'),map=window.CompanionSystemMap.install();let busy=false,epoch=0,last=null;
 const states={ready:'Ready',warning:'Needs attention',stopped:'Stopped',disabled:'Not configured'};
 function render(data){
  const counts=map.render(data.components),summary=el('system-summary');summary.dataset.state=counts.attention?'warning':'ready';
  el('system-summary-title').textContent=counts.attention?counts.attention+' component'+(counts.attention===1?' needs':'s need')+' attention':counts.disabled?'Configured components are ready':'All components are ready';el('system-summary-detail').textContent=counts.attention?'Local services and phone access are checked separately. Recovery controls are in the Mac app.':data.summary?.detail||'Check the desktop control app for recovery.';
  const checked=Number.isFinite(data.checked_at)?new Date(data.checked_at*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit'}):null;
  el('system-checked').textContent=checked?'Checked '+checked:'Check time unavailable';
  const list=el('system-components');list.dataset.stale='false';list.replaceChildren();
  for(const item of (Array.isArray(data.components)?data.components:[]).slice(0,24)){
   const row=document.createElement('div');row.className='component-item';row.dataset.state=Object.hasOwn(states,item.state)?item.state:'unknown';
   const heading=document.createElement('div');heading.className='component-heading';const title=document.createElement('strong');title.textContent=String(item.title||'Component').slice(0,120);const state=document.createElement('span');state.className='component-state';state.textContent=Object.hasOwn(states,item.state)?states[item.state]:'Unavailable';heading.append(title,state);
   const detail=document.createElement('p');detail.textContent=String(item.detail||'No current status reported.').slice(0,1200);row.append(heading,detail);list.append(row);
  }
 }
 async function refresh(){
  if(busy||!dialog.open||document.hidden)return;busy=true;el('system-refresh').disabled=true;el('system-refresh').textContent='Checking…';el('system-error').textContent='';
  const current=epoch,auth=getVersion();
  try{const data=await api('/api/components');if(current!==epoch||auth!==getVersion())return;last=data;render(data);}
  catch(error){if(current===epoch&&auth===getVersion()){el('system-error').textContent=(error.status===404?'The desktop service is finishing its update. Try again shortly.':error.message)+(last?' Last known results are shown below.':' Open Raycast → Desktop Companion on your Mac for recovery.');el('system-summary').dataset.state='unknown';el('system-summary-title').textContent=last?'Last known status':'Status unavailable';el('system-summary-detail').textContent=last?'Reconnect to refresh these component checks.':'The Mac could not complete a current status check.';if(last){map.stale();el('system-components').dataset.stale='true';el('system-checked').textContent='Last known status · Reconnect to check again';}}}
  finally{if(current===epoch){busy=false;el('system-refresh').disabled=false;el('system-refresh').textContent='Refresh status';}}
 }
 function reset(){epoch++;busy=false;last=null;map.reset();el('system-components').replaceChildren();el('system-error').textContent='';el('system-summary').dataset.state='unknown';el('system-summary-title').textContent='Checking your Mac';el('system-summary-detail').textContent='Local services and phone access are checked separately.';el('system-checked').textContent='';el('system-refresh').disabled=false;el('system-refresh').textContent='Refresh status';}
 el('system-close').addEventListener('click',()=>dialog.close());el('system-refresh').addEventListener('click',refresh);
 setInterval(()=>{if(dialog.open&&!document.hidden)refresh();},15000);
 reset();return {open(){dialog.showModal();return refresh();},reset};
}};
