'use strict';
// A small command channel shared with the CLI. Never evaluate supplied JavaScript.
const uiClient=crypto.randomUUID();let uiTimer,uiSyncing=false,uiResults=[];
function uiState(){
  const selection=state.selected?{...selectedContext(),position:getPosition(),inlineStyle:state.selected.style.cssText.slice(0,1000)}:null;
  return {deck:state.deck?.id||null,revision:state.revision,page:paged()?currentIndex()+1:null,pages:paged()?slides().length:null,mode:paged()?'slides':'native',dirty:state.dirty,busy:state.busy||!!state.preparing||!!state.loading||!!state.startingPresentation,editing:state.edit,presenting:!!state.presenting,fullscreen:!!fullscreen?.active,desktop:!!fullscreen?.native,window:fullscreen?.key||null,selection,timer:presentation?.timer()||null};
}
async function uiCommand(command){
  const a=command.args||{},action=command.action;
  if(state.busy||state.preparing||state.loading)throw Error('当前正在准备或保存文件，请稍后重试');
  if(action==='settings'){openSettings();return;}
  if(action==='open'){
    commitText();if(state.dirty)throw Error('当前有未保存编辑，请先 ui save，再打开作品');
    const meta=await api('/api/decks/'+encodeURIComponent(a.deck));
    await new Promise((resolve,reject)=>{
      const done=()=>{clearTimeout(timeout);resolve();};
      const timeout=setTimeout(()=>{frame.removeEventListener('jemu-ready',done);reject(Error('作品载入超时，请检查窗口状态'));},25000);
      frame.addEventListener('jemu-ready',done,{once:true});
      openDeck(meta,true).catch(error=>{clearTimeout(timeout);frame.removeEventListener('jemu-ready',done);reject(error);});
    });return;
  }
  if(!state.deck)throw Error('请先打开作品');
  if(a.deck&&a.deck!==state.deck.id)throw Error('窗口中作品已改变，请检查 ui status');
  if(a.revision&&a.revision!==state.revision)throw Error('窗口中版本已改变，请检查 ui status');
  if(['next','previous','goto'].includes(action)){
    if(!paged())throw Error('自由网页没有页码，请使用作品自身的交互');
    if(action==='goto'){if(!Number.isInteger(a.page)||a.page<1||a.page>slides().length)throw Error('页码超出范围');goTo(a.page-1);}
    else navigate(action==='next'?1:-1);return;
  }
  if(action==='reset'){await resetPresentation();return;}
  if(action==='edit'){if(a.enabled&&!state.canEdit)throw Error('当前内容不可直接编辑');setEdit(!!a.enabled);return;}
  if(action==='save'){await save();return;}
  if(action==='undo'){if(!state.undo.length&&!state.pendingText)throw Error('没有可撤销的编辑');undo();return;}
  if(action==='redo'){if(!state.redo.length)throw Error('没有可重做的编辑');redo();return;}
  if(action==='exit'){await leavePresentation();return;}
  if(action==='present'){await startPresentation();if(!state.presenting)throw Error('未开始演示；请检查窗口或弹窗权限');return;}
  if(action==='presenter'){presentation.open();return;}
  if(action==='timer'){
    if(!['start','pause','reset'].includes(a.operation))throw Error('计时操作无效');
    if(a.operation==='start')presentation.start();else if(a.operation==='pause')presentation.pause();else presentation.resetTimer();return;
  }
  if(!state.canEdit)throw Error('当前内容不可直接编辑');
  if(!state.edit)setEdit(true);
  if(action==='select'){
    if(typeof a.selector!=='string'||a.selector.length>2000)throw Error('请选择有效的 CSS 选择器');
    const root=designCanvas()||doc(),matches=root.querySelectorAll(a.selector);
    if(matches.length!==1)throw Error('选择器必须匹配一个元素，实际匹配 '+matches.length+' 个');
    const el=matches[0],page=slides().find(s=>s.contains(el));
    if(page)goTo(slides().indexOf(page));
    select(el);if(state.selected!==el)throw Error('此元素不能直接选择');return;
  }
  if(action==='paste'){if(!state.clipboard)throw Error('请先 ui copy');insertElement(state.clipboard);return;}
  const el=state.selected;if(!el)throw Error('请先 ui select 选择元素');
  commitText();
  if(action==='copy'){const payload=clipboardSnapshot();if(!payload)throw Error('无法复制此元素');state.clipboard=payload;return;}
  if(action==='delete'){deleteSelection();return;}
  const before=elementSnapshot(el);
  if(action==='text'){
    if(!textLeaf(el))throw Error('只能给纯文本元素改字，容器请通过文件编辑');
    if(typeof a.text!=='string'||a.text.length>50000)throw Error('文字过大或无效');
    el.textContent=a.text;record(el,before,'text');$('textValue').value=a.text;
  }else if(action==='move'){
    if(!Number.isFinite(a.x)||!Number.isFinite(a.y)||Math.abs(a.x)>100000||Math.abs(a.y)>100000)throw Error('位移无效');
    setPosition(a.x,a.y);record(el,before,'move');
  }else if(action==='format'){
    const properties={fontFamily:'string',fontSize:'number',bold:'boolean',italic:'boolean'};
    for(const [key,value] of Object.entries(a)){
      if(['deck','revision'].includes(key))continue;
      if(!properties[key]||typeof value!==properties[key])throw Error('字体参数无效');
      if(key==='fontSize'&&(!Number.isFinite(value)||value<1||value>500))throw Error('字号应在 1～500 pt 之间');
      if(key==='fontFamily'&&value.length>200)throw Error('字体名称过长');
    }
    if(a.fontFamily!==undefined)el.style.fontFamily=a.fontFamily;
    if(a.fontSize!==undefined)el.style.fontSize=a.fontSize+'pt';
    if(a.bold!==undefined)el.style.fontWeight=a.bold?'700':'400';
    if(a.italic!==undefined)el.style.fontStyle=a.italic?'italic':'normal';
    record(el,before,'format');updateFontControls();
  }else throw Error('不支持的界面操作');
  selectionRect();updateDirty();
}
async function syncUI(){
  if(uiSyncing||state.disposed)return;
  uiSyncing=true;
  try{
    const response=await api('/api/ui/sync',{client:uiClient,state:uiState(),results:uiResults});uiResults=[];
    for(const command of response.commands){
      try{await uiCommand(command);uiResults.push({id:command.id,ok:true});}
      catch(error){uiResults.push({id:command.id,ok:false,message:error.message});}
    }
  }catch{/* Service may restart; acknowledge results on the next successful sync. */}
  finally{uiSyncing=false;if(!state.disposed)uiTimer=setTimeout(syncUI,uiResults.length?50:document.hidden?5000:1000);}
}
window.addEventListener('pagehide',()=>clearTimeout(uiTimer));
document.addEventListener('visibilitychange',()=>{clearTimeout(uiTimer);if(!uiSyncing)syncUI();});
syncUI();
