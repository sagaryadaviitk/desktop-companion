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
   hasPending(){return [...entries.values()].some(item=>item.state==='sending');},
   reset(){entries.clear();}
  };
 },
 working(execution,connected=true,now=Date.now()){
  const age=now-execution?.checked_at;
  return connected&&execution?.status==='working'&&typeof execution.turn_id==='string'&&Boolean(execution.turn_id)&&Number.isFinite(age)&&age>=-1000&&age<=15000;
 },
 poll({read,enabled,delay,retryAfter=()=>0,random=Math.random,setTimer=setTimeout,clearTimer=clearTimeout}){
  let stopped=false,paused=false,timer=null,running=false,failures=0,wakePending=false;
  function schedule(ms){clearTimer(timer);if(!stopped&&!paused)timer=setTimer(tick,ms);}
  async function tick(){timer=null;if(stopped||paused||running)return;running=true;
   try{if(enabled()){const result=await read();failures=result===false?Math.min(failures+1,16):0;}}
   catch(_){failures=Math.min(failures+1,16);/* Reads report connection state; mutations never enter this scheduler. */}
   finally{
    running=false;
    const cap=Math.min(30000,1000*2**Math.max(0,failures-1));
    const wait=failures?cap*(.5+random()*.5):delay();
    schedule(Math.max(wakePending?0:wait,retryAfter()));wakePending=false;
   }
  }
  schedule(delay());
  return {wake(){if(stopped)return;paused=false;failures=0;if(running)wakePending=true;else schedule(Math.max(0,retryAfter()));},
          pause(){paused=true;wakePending=false;clearTimer(timer);},
          stop(){stopped=true;clearTimer(timer);}};
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
