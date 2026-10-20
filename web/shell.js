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
 function installKeyboard({isMobile}){
  const input=document.getElementById('instruction');let gesture=null;
  const selecting=()=>window.getSelection?.()?.isCollapsed===false||input.selectionStart!==input.selectionEnd;
  const zoomed=()=>Math.abs((window.visualViewport?.scale||1)-1)>.05;
  const cancel=()=>{gesture=null;};
  document.addEventListener('touchstart',event=>{
   cancel();
   if(!isMobile()||event.touches.length!==1||zoomed()||document.activeElement!==input||!document.body.classList.contains('keyboard-open')||document.querySelector('dialog[open]')||selecting())return;
   const target=event.target;
   if(!(target instanceof Element)||!target.closest('#messages')||target.closest('a,button,input,textarea,select,[contenteditable],.code-block,.table-scroll'))return;
   const touch=event.touches[0];gesture={x:touch.clientX,y:touch.clientY,finger:touch.identifier};
  },{passive:true});
  document.addEventListener('touchmove',event=>{
   if(!gesture)return;
   if(event.touches.length!==1||document.activeElement!==input||selecting()||zoomed()){cancel();return;}
   const touch=event.touches[0];if(touch.identifier!==gesture.finger){cancel();return;}
   const dy=touch.clientY-gesture.y,dx=Math.abs(touch.clientX-gesture.x);
   if(dy< -12||(dx>18&&dx>Math.abs(dy))){cancel();return;}
   // Only dismiss. Leave native scrolling alone and never reuse this swipe as
   // a refresh gesture; refresh ignores gestures that begin with a keyboard.
   if(dy>=40&&dy>dx*1.5){cancel();input.blur();}
  },{passive:true});
  document.addEventListener('touchend',cancel,{passive:true});
  document.addEventListener('touchcancel',cancel,{passive:true});
 }
 return {standalone,haptic,sizeInput,repaintCaret,installKeyboard};
})();
