"use strict";
// Transient delivery receipts, independent reads, and deliberate scroll following.
// This module never sends or retries a command and never persists conversation text.
window.CompanionConversation={
 receipts(){
  const entries=new Map();
  return {
   begin(thread,id,text,photos=[]){entries.set(id,{thread,id,text,photos:[...photos],state:'sending',timestamp:Date.now()});},
   settle(thread,id,result){const item=entries.get(id);if(!item||item.thread!==thread)return;
    if(['sent','steered'].includes(result.state)&&typeof result.client_id==='string'&&result.client_id){Object.assign(item,{state:'accepted',client_id:result.client_id});}
    else entries.delete(id);
   },
   remove(id){entries.delete(id);},
   list(thread,messages=[]){const recorded=new Set(messages.filter(m=>m.role==='user'&&m.client_id).map(m=>m.client_id));
    for(const [id,item]of entries)if(item.thread===thread&&recorded.has(item.client_id))entries.delete(id);
    return [...entries.values()].filter(item=>item.thread===thread);
   },
   hasPending(){return entries.size>0;},
   reset(){entries.clear();}
  };
 },
 working(execution,connected=true,now=Date.now()){
  const age=now-execution?.checked_at;
  return connected&&execution?.status==='working'&&typeof execution.turn_id==='string'&&Boolean(execution.turn_id)&&Number.isFinite(age)&&age>=-1000&&age<=15000;
 },
 poll({read,enabled,delay,setTimer=setTimeout,clearTimer=clearTimeout}){
  let stopped=false,timer=null,running=false;
  async function tick(){timer=null;if(stopped||running)return;running=true;
   try{if(enabled())await read();}catch(_){/* The read path reports its own connection state. */}
   finally{running=false;if(!stopped)timer=setTimer(tick,delay());}
  }
  timer=setTimer(tick,delay());
  return {stop(){stopped=true;clearTimer(timer);}};
 },
 scroller(panel,{changed=()=>{},frame=requestAnimationFrame,Resize=window.ResizeObserver,Mutation=window.MutationObserver}={}){
  let following=true,scheduled=false,adjusting=false;
  function visible(){return panel.clientHeight>0;}
  function bottom(){following=true;if(!visible())return;adjusting=true;panel.scrollTop=panel.scrollHeight;frame(()=>{adjusting=false;changed();});}
  function schedule(){if(scheduled||!following)return;scheduled=true;frame(()=>{scheduled=false;if(following)bottom();});}
  panel.addEventListener('scroll',()=>{if(!visible()||adjusting)return;following=panel.scrollHeight-panel.scrollTop-panel.clientHeight<100;changed();},{passive:true});
  // Touch/wheel intent cancels a scheduled follow before a late image can pull it back.
  panel.addEventListener('wheel',event=>{if(event.deltaY<0)following=false;},{passive:true});
  let touchY=null;panel.addEventListener('touchstart',event=>{touchY=event.touches[0]?.clientY??null;},{passive:true});
  panel.addEventListener('touchmove',event=>{if(touchY!==null&&event.touches[0]?.clientY>touchY+4)following=false;},{passive:true});
  panel.addEventListener('load',schedule,true);
  const resize=Resize?new Resize(schedule):null;
  function observe(){resize?.disconnect();resize?.observe(panel);for(const child of panel.children)resize?.observe(child);schedule();}
  const mutations=Mutation?new Mutation(observe):null;mutations?.observe(panel,{childList:true});observe();
  return {bottom,following:()=>following,open(){following=true;schedule();},pause(){following=false;},changed:schedule};
 }
};
