"use strict";
// Clipboard images use exactly the same private upload path as selected files.
window.CompanionAttachments={
  files(data){
    const items=Array.from(data?.items||[]).filter(item=>item.kind==='file'&&item.type.startsWith('image/'));
    const files=items.map(item=>item.getAsFile()).filter(Boolean);
    return files.length?files:Array.from(data?.files||[]).filter(file=>file.type.startsWith('image/'));
  },
  install({addFiles,canAdd,onError,getContext,ids={}}){
    const el=id=>document.getElementById(ids[id]||id),menu=el('photo-menu'),trigger=el('add-photo');
    function close(){menu.hidden=true;trigger.setAttribute('aria-expanded','false');}
    async function upload(files){const input=el('instruction'),context=getContext();try{await addFiles(files);if(context===getContext())window.CompanionShell?.repaintCaret(input);}catch(_){onError('This photo could not be added. Try again from the message box.');}}
    trigger.addEventListener('click',()=>{menu.hidden=!menu.hidden;trigger.setAttribute('aria-expanded',String(!menu.hidden));});
    el('choose-photo').addEventListener('click',()=>{close();el('photo-input').click();});
    el('photo-input').addEventListener('change',()=>{const files=Array.from(el('photo-input').files);el('photo-input').value='';if(canAdd())return upload(files);});
    el('instruction').addEventListener('paste',event=>{
      const files=window.CompanionAttachments.files(event.clipboardData);if(!files.length)return;
      // Mixed text + image paste keeps the browser's text insertion and undo.
      if(!event.clipboardData.getData('text/plain'))event.preventDefault();
      if(canAdd()){close();return upload(files);}else onError('Reconnect to this chat before adding a photo.');
    });
    el('paste-photo').addEventListener('click',async()=>{
      close();if(!canAdd())return;const context=getContext();
      if(!navigator.clipboard?.read){onError('Use Paste in the message box, or choose a photo from your library. This browser does not offer clipboard reading.');return;}
      try{
        const entries=await navigator.clipboard.read(),files=[];
        for(const item of entries){const type=item.types.find(type=>type.startsWith('image/'));if(type)files.push(new File([await item.getType(type)],'Pasted photo',{type}));}
        if(context!==getContext()){onError('The selected chat changed. Paste again in the intended chat.');return;}
        if(files.length)await upload(files);else onError('No photo was found in the clipboard. Copy the photo itself, then paste again.');
      }catch(_){onError('Photo paste was not allowed. Use Paste in the message box or choose a photo from your library.');}
    });
    document.addEventListener('pointerdown',event=>{if(!menu.contains(event.target)&&!trigger.contains(event.target))close();});
    document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!menu.hidden){close();trigger.focus();}});
  }
};
