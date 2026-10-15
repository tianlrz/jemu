const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const resources=require('../web/resources.js');

const state={undo:[],undoBase:'',changes:[]};
for(let i=1;i<=10000;i++){
  state.undo.push({id:String(i),bytes:2048});resources.trimUndo(state);
  resources.appendTrace(state,{type:'move',text:'x'.repeat(500),i});
}
assert.equal(state.undo.length,100);
assert.equal(state.undoBase,'9900');
assert.equal(state.undo.at(-1).id,'10000');
while(state.undo.length)state.undo.pop();
assert.equal(state.undo.at(-1)?.id||state.undoBase,'9900','Undo floor must not falsely mark a modified document as the original');
assert.ok(state.changes.length<=500);
assert.ok(state.traceBytes<=resources.limits.traceBytes);
assert.equal(state.compacted+state.changes.length,10000);
state.undo.push({id:'oversized',bytes:resources.limits.undoBytes+1});
resources.trimUndo(state);
assert.equal(state.undo.length,0);
assert.equal(state.undoBase,'oversized','Large operation remains applied and dirty even when not retained for undo');

let reads=0, ticks=0, callback;
const context={crypto:{randomUUID:()=> 'test'},location:{origin:'http://localhost'},document:{hidden:false,addEventListener(){},removeEventListener(){}},window:{addEventListener(){}},setTimeout(fn){callback=fn;return 1;},clearTimeout(){}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(require.resolve('../web/presenting.js'),'utf8')+'\nthis.Presentation=DeckroomPresentation;',context);
const presentation=new context.Presentation({read(){reads++;return {notes:'large notes'};},onTick(){ticks++;}});
presentation.sync();assert.equal(reads,0,'Closed presenter must not parse notes');
presentation.start();callback();assert.equal(reads,0);
let sent=[];presentation.popup={closed:false,postMessage(data){sent.push(data);}};presentation.connected=true;
presentation.sync();assert.equal(reads,1);
callback();assert.equal(reads,1,'Timer tick sends no full note payload');
assert.equal(sent.at(-1).kind,'deckroom-presenter-tick');
presentation.popup.closed=true;callback();assert.equal(presentation.popup,null);
presentation.dispose();
console.log('PASS: 10,000 edits remain bounded, undo checkpoint preserved, oversized action released, presenter notes only read on demand.');
