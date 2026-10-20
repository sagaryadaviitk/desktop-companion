"use strict";
// One build witness for every chat and a reload guard for mixed/stale responses.
window.CompanionUpdates={install({loadedBuild,hasDraft,isBusy,isInteracting,onBusy,reload=()=>location.reload(),confirmReload=()=>window.confirm('Reload to update? Unsent drafts and photos in this tab will be cleared.')}){
 const banner=document.getElementById('update-banner');let lastAutomatic='';
 try{lastAutomatic=sessionStorage.getItem('companion-last-update')||'';}catch(_){}
 document.getElementById('update-reload').addEventListener('click',()=>{
  if(isBusy()){onBusy();return;}if(hasDraft()&&!confirmReload())return;reload();
 });
 return {check(build){
  const available=typeof build==='string'&&/^[a-f0-9]{16}$/.test(build)&&build!==loadedBuild;
  banner.hidden=!available;
  if(available&&!isBusy()&&!hasDraft()&&!isInteracting()&&lastAutomatic!==build){
   lastAutomatic=build;try{sessionStorage.setItem('companion-last-update',build);}catch(_){}
   reload();return true;
  }
  return false;
 }};
}};
