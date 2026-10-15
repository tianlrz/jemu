'use strict';
// Bounds apply to retained editor data, not the browser engine or source document.
const JemuResources = (()=>{
  const limits=Object.freeze({undoSteps:100,undoBytes:16*1024*1024,traceSteps:500,traceBytes:2*1024*1024});
  const bytes=value=>JSON.stringify(value??null).length*2;
  function trimUndo(state){
    let total=state.undo.reduce((n,action)=>n+action.bytes,0),removed=0;
    while(state.undo.length>limits.undoSteps||total>limits.undoBytes){const action=state.undo.shift();total-=action.bytes;state.undoBase=action.id;removed++;}
    return removed;
  }
  function appendTrace(state,change){
    state.changes.push(change);state.traceBytes=(state.traceBytes||0)+bytes(change);
    while(state.changes.length>limits.traceSteps||state.traceBytes>limits.traceBytes){state.traceBytes-=bytes(state.changes.shift());state.compacted=(state.compacted||0)+1;}
  }
  function text(value,max=4000){return typeof value==='string'?value.slice(0,max):value;}
  return {limits,bytes,trimUndo,appendTrace,text};
})();
if(typeof module!=='undefined')module.exports=JemuResources;
