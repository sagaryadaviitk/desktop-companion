"use strict";
// Run before styles render. Only this browser's appearance preference is stored.
window.CompanionAppearance=(()=>{
 const key='companion-appearance',allowed=new Set(['system','light','dark']);
 const media=window.matchMedia('(prefers-color-scheme: dark)');let preference='system';
 try{const saved=localStorage.getItem(key);if(allowed.has(saved))preference=saved;}catch(_){}
 function sync(){
  const resolved=preference==='system'?(media.matches?'dark':'light'):preference;
  document.documentElement.dataset.theme=resolved;
  document.documentElement.dataset.appearance=preference;
  const meta=document.querySelector('meta[name="theme-color"]');if(meta)meta.content=resolved==='dark'?'#181818':'#f5f5f7';
  for(const button of document.querySelectorAll('[data-theme-choice]'))button.setAttribute('aria-pressed',String(button.dataset.themeChoice===preference));
  const note=document.getElementById('appearance-note');if(note)note.textContent=preference==='system'?'Follows this device’s light or dark appearance.':'Saved for this browser.';
 }
 function set(value){if(!allowed.has(value))return false;preference=value;try{localStorage.setItem(key,value);}catch(_){}sync();return true;}
 sync();media.addEventListener('change',()=>{if(preference==='system')sync();});
 window.addEventListener('storage',event=>{if(event.key===key){preference=allowed.has(event.newValue)?event.newValue:'system';sync();}});
 document.addEventListener('DOMContentLoaded',()=>{for(const button of document.querySelectorAll('[data-theme-choice]'))button.addEventListener('click',()=>set(button.dataset.themeChoice));sync();},{once:true});
 return {set,get:()=>preference};
})();
