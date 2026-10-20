"use strict";
// One build witness for every chat and a reload guard for mixed/stale responses.
window.CompanionUpdates={install({loadedBuild,hasDraft,isBusy,isInteracting,onBusy,reload=()=>location.reload(),confirmReload=()=>window.confirm('Reload app? Unsent drafts and photos in this tab will be cleared.')}){
 const banner=document.getElementById('update-banner');let lastAutomatic='';
 try{lastAutomatic=sessionStorage.getItem('companion-last-update')||'';}catch(_){}
 function requestReload(){if(isBusy()){onBusy();return false;}if(hasDraft()&&!confirmReload())return false;reload();return true;}
 document.getElementById('update-reload').addEventListener('click',requestReload);
 return {reload:requestReload,check(build){
  const available=typeof build==='string'&&/^[a-f0-9]{16}$/.test(build)&&build!==loadedBuild;
  banner.hidden=!available;
  if(available&&!isBusy()&&!hasDraft()&&!isInteracting()&&lastAutomatic!==build){
   lastAutomatic=build;try{sessionStorage.setItem('companion-last-update',build);}catch(_){}
   reload();return true;
  }
  return false;
 }};
}};
