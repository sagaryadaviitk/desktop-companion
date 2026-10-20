"use strict";
// Browser shell behavior shared by installed Home Screen apps and ordinary tabs.
window.CompanionShell=(()=>{
 const display=window.matchMedia('(display-mode: standalone)'),sizes=new WeakMap();
 const standalone=()=>display.matches||navigator.standalone===true;
 function sync(){document.documentElement.dataset.standalone=String(standalone());}
 sync();display.addEventListener('change',sync);
 function haptic(kind='start'){
  if(typeof navigator.vibrate!=='function')return false;
  try{return navigator.vibrate(kind==='complete'?6:10)===true;}catch(_){return false;}
 }
 function sizeInput(input,maxHeight=Math.min(160,window.innerHeight*.3)){
  const width=input.clientWidth;if(!width)return;
  const signature=JSON.stringify([input.value,width,maxHeight]);if(sizes.get(input)===signature)return;
  sizes.set(input,signature);
  // Do not collapse a focused, empty textarea on every photo/status update.
  if(!input.value){if(input.style.height!=='52px')input.style.height='52px';return;}
  input.style.height='auto';input.style.height=Math.min(Math.max(52,input.scrollHeight),maxHeight)+'px';
 }
 function repaintCaret(input){
  requestAnimationFrame(()=>{
   if(document.activeElement!==input||input.disabled||!input.isConnected)return;
   // Re-anchor WebKit's caret after the photo tray changes layout, without a
   // blur/refocus cycle or changing the user's current text selection.
   const {selectionStart:start,selectionEnd:end,selectionDirection:direction}=input;
   if(typeof start==='number'&&typeof end==='number')input.setSelectionRange(start,end,direction||'none');
  });
 }
 return {standalone,haptic,sizeInput,repaintCaret};
})();
