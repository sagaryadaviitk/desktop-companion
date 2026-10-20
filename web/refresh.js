"use strict";
// Refresh data in place. Home Screen apps also own header pulls because no browser refresh UI is present.
window.CompanionRefresh={install({refresh,isMobile,isStandalone=()=>window.CompanionShell?.standalone()===true,haptic=kind=>window.CompanionShell?.haptic(kind)}){
const el=id=>document.getElementById(id);
let manualRefreshing=false,pullGesture=null,pullTimer=null,pullAnchor=null;
function cancelPull(){pullGesture=null;if(!manualRefreshing){el('pull-refresh').hidden=true;pullAnchor=null;}}
function showPullRefresh(label,loading=false){
  clearTimeout(pullTimer);const indicator=el('pull-refresh');indicator.hidden=false;
  document.documentElement.style.setProperty('--pull-top',(pullAnchor?.top??document.querySelector('.topbar').getBoundingClientRect().bottom)+'px');
  el('pull-refresh-label').textContent=label;indicator.classList.toggle('loading',loading);indicator.querySelector('.pull-refresh-icon').textContent=loading?'↻':label==='Release to refresh'?'↻':label==='Up to date'?'✓':'↓';
}
async function manualRefresh(fromPull=false){
  if(manualRefreshing||document.body.classList.contains('signed-out'))return;
  if(!fromPull)haptic('start');
  manualRefreshing=true;el('manual-refresh').disabled=true;el('manual-refresh').setAttribute('aria-busy','true');showPullRefresh('Refreshing…',true);
  try{
    const updated=await refresh();if(document.body.classList.contains('signed-out'))return;
    if(updated)haptic('complete');
    showPullRefresh(updated?'Up to date':'Couldn’t refresh. Try again.');
    el('manual-refresh').title=updated?'Updated '+new Date().toLocaleTimeString():'Refresh failed. Tap to try again.';
    pullTimer=setTimeout(()=>{el('pull-refresh').hidden=true;},updated?1800:4000);
  }catch(_){showPullRefresh('Couldn’t refresh. Try again.');pullTimer=setTimeout(()=>{el('pull-refresh').hidden=true;},4000);}
  finally{manualRefreshing=false;el('manual-refresh').disabled=false;el('manual-refresh').setAttribute('aria-busy','false');}
}
el('manual-refresh').addEventListener('click',()=>{pullAnchor=null;return manualRefresh();});
// The app scrolls within panels to fit above a phone keyboard. Its pull gesture
// refreshes data without reloading the document or discarding drafts/attachments.
document.addEventListener('touchstart',event=>{
  cancelPull();
  if(!isMobile()||manualRefreshing||event.touches.length!==1||document.querySelector('dialog[open]')||document.body.classList.contains('keyboard-open')||!document.body.classList.contains('ready'))return;
  const target=event.target;if(!(target instanceof Element)||target.closest('input,textarea,select,a,[contenteditable],.code-block,.table-scroll'))return;
  const panel=target.closest('#chats,#messages,#home-screen,#activity-panel,#changes-panel,#details-panel');
  const header=isStandalone()&&target.closest('.topbar,.conversation-heading,.conversation-tabs,.inbox-tabs,.browse-toolbar');
  if((!panel&&!header)||(panel&&panel.scrollTop>1))return;
  const touch=event.touches[0];pullGesture={panel,x:touch.clientX,y:touch.clientY,distance:0,active:false,armed:false};
  pullAnchor=header?{top:document.querySelector('.topbar').getBoundingClientRect().bottom}:panel.getBoundingClientRect();
},{passive:true});
document.addEventListener('touchmove',event=>{
  if(!pullGesture)return;
  if(event.touches.length!==1){cancelPull();return;}
  const touch=event.touches[0],dy=touch.clientY-pullGesture.y,dx=Math.abs(touch.clientX-pullGesture.x);
  if(!pullGesture.active&&(dy< -8||dx>12&&dx>dy||(pullGesture.panel?.scrollTop||0)>1)){cancelPull();return;}
  if(dy<12&&!pullGesture.active){if(dy>0&&dx<=dy&&event.cancelable)event.preventDefault();return;}
  if(!event.cancelable){cancelPull();return;}
  event.preventDefault();pullGesture.active=true;pullGesture.distance=Math.max(0,dy);
  if(dy>=80&&!pullGesture.armed){pullGesture.armed=true;haptic('armed');}
  showPullRefresh(dy>=80?'Release to refresh':'Pull to refresh');
},{passive:false});
document.addEventListener('touchend',event=>{
  const gesture=pullGesture;pullGesture=null;if(!gesture?.active)return;
  if(event.cancelable)event.preventDefault(); // The gesture must not also open a chat card.
  if(gesture.distance>=80)return manualRefresh(true);else el('pull-refresh').hidden=true;
},{passive:false});
document.addEventListener('touchcancel',cancelPull,{passive:true});

return {refresh:manualRefresh};
}};
