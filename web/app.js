'use strict';
const $ = id => document.getElementById(id);
const frame = $('frame');
const state = {deck:null, decks:[], edit:false, selected:null, undo:[], redo:[], undoBase:'', savedHistory:'', changes:[], traceBytes:0, compacted:0, dirty:false, revision:null, pendingText:null, importing:[], busy:false, panel:null};
let toastTimer,presentation=null,audienceControlsTimer,fullscreen=null;
function toast(message) { $('toast').textContent=message; $('toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').hidden=true,4500); }
async function api(path, body) {
  const response=await fetch(path, body===undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-Deckroom-Token':window.DECKROOM_TOKEN},body:JSON.stringify(body)});
  const value=await response.json();
  if (!response.ok) { const error=new Error(value.error||'操作没有完成'); error.status=response.status; throw error; }
  return value;
}
function run(fn) { return (...args)=>Promise.resolve().then(()=>fn(...args)).catch(error=>toast(error.message)); }
function openSettings(){
  const url='/settings'+(state.deck?'?deck='+encodeURIComponent(state.deck.id):'');
  const popup=window.open(url,'jemu-settings-'+location.port);
  if(!popup)throw Error('设置页未打开，请允许本地页面打开窗口');
  popup.focus();
}
function doc() { try {return frame.contentDocument;} catch {return null;} }
function concealFrame(){frame.classList.remove('jemu-ready');$('surface').setAttribute('aria-busy','true');}
function revealFrame(){frame.classList.add('jemu-ready');$('surface').removeAttribute('aria-busy');}
function loadFrame(url){state.prepareController?.abort();concealFrame();state.runtimeSource=null;state.normalizationPending=false;state.loading=true;$('pdfButton').disabled=true;frame.removeAttribute('srcdoc');frame.src=url;}
function paged(){return state.adapter?.kind==='pure'||state.adapter?.kind==='legacy';}
function stage() {return state.adapter?.kind==='legacy'?state.adapter.stage:null;}
function slides() {return paged()?state.adapter.pages:[];}
function currentIndex(){return stage()?.index??state.page??0;}
function designSize(){return {width:stage()?.designWidth||state.pure?.width||frame.clientWidth||1280,height:stage()?.designHeight||state.pure?.height||frame.clientHeight||720};}
function designCanvas(){return stage()?.shadowRoot?.querySelector('.canvas')||state.pure?.canvas;}
function fitPure(){
  const pure=state.pure;if(!pure||pure.document!==doc())return;
  const scale=Math.min(frame.clientWidth/pure.width,frame.clientHeight/pure.height);
  if(frame.contentWindow.CSS.supports('zoom','1'))pure.canvas.style.zoom=scale;
  else pure.canvas.style.transform='scale('+scale+')';
}
function attachPureDocument(){
  state.pure=null;if(state.adapter?.kind!=='pure')return;
  const d=doc(),pages=slides();if(!pages.length)return;
  let host=pages[0].parentElement;
  if(!pages.every(page=>page.parentElement===host))return;
  if(host===d.body){const source=d.createElement('main');source.dataset.deckroomUi='source-body';pages[0].before(source);pages.forEach(page=>source.append(page));host=source;}
  const width=Math.min(10000,Math.max(100,Number(host.dataset.width)||1920)),height=Math.min(10000,Math.max(100,Number(host.dataset.height)||1080));
  const wrapper=d.createElement('div'),canvas=d.createElement('div'),style=d.createElement('style');
  wrapper.dataset.deckroomUi='content-stage';canvas.dataset.jemuCanvas='';
  Object.assign(canvas.style,{width:width+'px',height:height+'px',position:'relative',flexShrink:'0'});
  host.before(wrapper);wrapper.append(canvas);canvas.append(host);
  pages.forEach(page=>page.setAttribute('data-jemu-page',''));
  style.dataset.deckroomUi='true';
  style.textContent=`html,body{height:100%!important;overflow:hidden!important;margin:0!important;padding:0!important;background:#111!important}[data-deckroom-ui="content-stage"]{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;overflow:hidden}[data-jemu-canvas]>:first-child{position:absolute!important;inset:0!important;width:100%!important;height:100%!important;margin:0!important}[data-jemu-page]{position:absolute!important;inset:0!important;width:100%!important;height:100%!important;margin:0!important;box-sizing:border-box!important;visibility:hidden;pointer-events:none}[data-jemu-page][data-deck-active]{visibility:visible;pointer-events:auto}@page{size:${width}px ${height}px;margin:0}@media print{html,body{height:auto!important;overflow:visible!important;background:none!important}[data-deckroom-ui="content-stage"]{position:static;display:block;overflow:visible}[data-jemu-canvas]{zoom:1!important;transform:none!important;width:auto!important;height:auto!important}[data-jemu-canvas]>:first-child{position:static!important;height:auto!important}[data-jemu-page]{position:relative!important;inset:auto!important;width:${width}px!important;height:${height}px!important;visibility:visible!important;pointer-events:auto;break-after:page;break-inside:avoid}[data-jemu-page]:last-child{break-after:auto}*{-webkit-print-color-adjust:exact;print-color-adjust:exact}}`;
  d.head.append(style);state.pure={document:d,canvas,width,height};state.page=Math.min(state.page||0,pages.length-1);
  const chrome=d.createElement('style');chrome.dataset.deckroomUi='true';chrome.textContent='[data-jemu-suppressed]{display:none!important}';d.head.append(chrome);
  d.querySelectorAll('.deck-toolbar,[data-jemu-controls]').forEach(el=>{if(!pages.some(page=>page.contains(el)))el.setAttribute('data-jemu-suppressed','');});
  style.textContent+='@media screen{[data-jemu-page]:not([data-deck-active]){display:none!important}}';
  pages.forEach((page,index)=>page.toggleAttribute('data-deck-active',index===state.page));
  frame.contentWindow.addEventListener('resize',fitPure);fitPure();
}
function selectedContext() {
  const el=state.selected, list=slides(), slide=list.find(page=>page.contains(el));
  const path=[];
  for(let node=el;node&&node!==doc()?.body;node=node.parentElement){
    if(node.dataset.slideId){path.unshift('[data-slide-id="'+CSS.escape(node.dataset.slideId)+'"]');break;}
    const siblings=node.parentElement?[...node.parentElement.children].filter(sibling=>sibling.tagName===node.tagName):[node];
    path.unshift(node.tagName.toLowerCase()+':nth-of-type('+(siblings.indexOf(node)+1)+')');
  }
  return {entry:state.activeEntry||null,slide:slide?.dataset.slideId||null,page:paged()?(slide?list.indexOf(slide)+1:currentIndex()+1):null,node:el?.dataset.nodeId||null,selector:el?.dataset.nodeId?'[data-node-id="'+CSS.escape(el.dataset.nodeId)+'"]':null,path:path.join(' > '),slideTitle:slide?.querySelector('h1,h2,h3')?.textContent||null,slot:el?.dataset.slot||null,role:el?.dataset.role||null,tag:el?.tagName.toLowerCase()||null,text:el?.textContent?.trim().slice(0,500)||null};
}
function elementSnapshot(el) {
  const r=el.getBoundingClientRect(),canvas=designCanvas()?.getBoundingClientRect();
  const scale=canvas?canvas.width/designSize().width:1,round=value=>Math.round(value*100)/100;
  const view=el.ownerDocument.defaultView;
  const box={x:round((r.x-(canvas?.x||0))/scale+(canvas?0:view.scrollX)),y:round((r.y-(canvas?.y||0))/scale+(canvas?0:view.scrollY)),width:round(r.width/scale),height:round(r.height/scale),unit:canvas?'slide-px':'css-px'};
  const computed=el.ownerDocument.defaultView.getComputedStyle(el);
  return {text:el.textContent,translate:el.style.translate||'',html:el.innerHTML,cssText:el.style.cssText,box,style:{fontFamily:computed.fontFamily,fontSize:computed.fontSize,fontWeight:computed.fontWeight,fontStyle:computed.fontStyle,color:computed.color,cssText:el.style.cssText}};
}
function updateDirty() {
  state.dirty=historySignature()!==state.savedHistory || !!state.pendingText;
  $('saveState').textContent=state.dirty?'有未保存修改':state.normalizationPending?'完整稿件待保存':'已保存在本机 · '+(state.deck?.versions.length||0)+' 个版本';
  $('saveButton').disabled=(!state.dirty&&!state.normalizationPending)||state.busy;
  $('undoButton').disabled=state.undo.length===0&&!state.pendingText;
  $('redoButton').disabled=state.redo.length===0;
  $('toolbarUndo').disabled=$('undoButton').disabled;$('toolbarRedo').disabled=$('redoButton').disabled;
}
function changeSnapshot(snapshot){return {text:JemuResources.text(snapshot.text),textTruncated:(snapshot.text?.length||0)>4000,translate:snapshot.translate,box:snapshot.box,style:snapshot.style};}
function historySignature(){return state.undo.at(-1)?.id||state.undoBase;}
function pushAction(action,change){
  action.id=crypto.randomUUID();action.change=change;
  action.bytes=JemuResources.bytes({before:action.before,after:action.after,change})+(['insert','delete'].includes(action.kind)?(action.el?.outerHTML.length||0)*2:0);
  state.undo.push(action);state.redo=[];JemuResources.appendTrace(state,change);
  if(JemuResources.trimUndo(state)&&!state.historyNotice){state.historyNotice=true;toast('为控制内存，仅保留最近 100 步、约 16 MB 的撤销记录；当前内容和已保存版本不受影响。');}
  updateDirty();
}
function record(el,before,type,context=selectedContext()) {
  const after=elementSnapshot(el);
  if(before.html===after.html && before.cssText===after.cssText) return;
  if(['move','resize','format'].includes(type)){before.html=null;after.html=null;before.text=null;after.text=null;}
  pushAction({kind:'modify',el,before,after,type},{...context,type,before:changeSnapshot(before),after:changeSnapshot(after)});
}
function commitText() {
  finishInline();
  if (!state.pendingText) return;
  const pending=state.pendingText; state.pendingText=null;
  record(pending.el,pending.before,'text'); updateDirty();
}
function watchSelection(){clearInterval(state.selectionTimer);state.selectionTimer=null;if(state.selected&&!document.hidden)state.selectionTimer=setInterval(selectionRect,250);}
function clearSelection() { commitText(); state.selected=null;watchSelection(); $('selectionBox').hidden=true; $('properties').hidden=true; $('noSelection').hidden=false; }
function textLeaf(el){return el&&el.namespaceURI==='http://www.w3.org/1999/xhtml'&&!el.shadowRoot&&!el.tagName.includes('-')&&el.children.length===0&&!['IMG','SVG','INPUT','VIDEO','AUDIO','CANVAS','IFRAME','SCRIPT','STYLE'].includes(el.tagName);}
function finishInline(){
  const active=state.inline;if(!active)return;state.inline=null;
  if(active.changed)active.el.textContent=active.el.innerText;
  for(const [key,value] of Object.entries(active.attrs)){if(value===null)active.el.removeAttribute(key);else active.el.setAttribute(key,value);}
  active.el.removeAttribute('data-deckroom-inline');
}
function beginInline(el,selectAll=false){
  if(!textLeaf(el))return;
  if(state.inline?.el===el)return;
  commitText();
  state.inline={el,before:elementSnapshot(el),attrs:{contenteditable:el.getAttribute('contenteditable'),spellcheck:el.getAttribute('spellcheck')},changed:false};
  el.setAttribute('contenteditable','plaintext-only');el.setAttribute('spellcheck','false');el.dataset.deckroomInline='true';el.focus();
  const selection=frame.contentWindow.getSelection();
  if(selectAll||!el.contains(selection.anchorNode)){const range=doc().createRange();range.selectNodeContents(el);if(!selectAll)range.collapse(false);selection.removeAllRanges();selection.addRange(range);}
}
function select(el) {
  commitText();
  if(!el || el===doc()?.body || el===stage() || slides().includes(el)) return clearSelection();
  if(!el.dataset.nodeId) el.dataset.nodeId='dr-'+crypto.randomUUID().slice(0,12);
  state.selected=el;
  watchSelection();
  $('properties').hidden=false; $('noSelection').hidden=true;
  $('elementName').textContent=el.tagName==='IMG'?'图片':textLeaf(el)?'文本框':'内容容器';
  // Only text leaves can be edited as plain text; containers preserve their children and markup.
  const leaf=textLeaf(el);
  $('textValue').disabled=!leaf;
  $('textValue').value=leaf?el.textContent:'';
  $('textHelp').textContent=leaf?'文字实时预览，点击保存后写入文件。':'这是一个容器或图形。选择里面的文字进行编辑。';
  updatePosition();updateFontControls(); selectionRect();
}
function updateFontControls(){
  const el=state.selected;if(!el)return;const computed=el.ownerDocument.defaultView.getComputedStyle(el),enabled=!['IMG','SVG','VIDEO','CANVAS'].includes(el.tagName);
  for(const id of ['fontFamily','fontSize','fontBold','fontItalic'])$(id).disabled=!enabled;
  $('fontFamily').value=el.style.fontFamily.includes(',')?el.style.fontFamily:el.style.fontFamily.replace(/^["']|["']$/g,'');$('fontFamily').placeholder=computed.fontFamily.split(',')[0].replace(/["']/g,'');
  $('fontSize').value=Math.round(parseFloat(computed.fontSize)*0.75*100)/100;
  $('fontBold').setAttribute('aria-pressed',String(Number(computed.fontWeight)>=600||computed.fontWeight==='bold'));
  $('fontItalic').setAttribute('aria-pressed',String(computed.fontStyle==='italic'));
}
function applyFont(property,value){if(!state.selected)return;commitText();const before=elementSnapshot(state.selected);state.selected.style[property]=value;record(state.selected,before,'format');updateFontControls();selectionRect();}
const CLIPBOARD_PREFIX='DECKROOM_ELEMENT_V1\n';
function typingTarget(target){return ['INPUT','TEXTAREA','SELECT'].includes(target?.tagName)||target?.isContentEditable;}
function clipboardSnapshot(){
  commitText();const el=state.selected;if(!el)return null;
  const copy=el.cloneNode(true),liveNodes=[el,...el.querySelectorAll('*')],copyNodes=[copy,...copy.querySelectorAll('*')];
  const properties=['color','background-color','font-family','font-size','font-weight','font-style','line-height','letter-spacing','text-align','text-decoration','white-space','border-top','border-right','border-bottom','border-left','border-radius','padding','box-sizing','display','gap','grid-template-columns','align-items','justify-content','object-fit'];
  liveNodes.forEach((live,index)=>{
    const clone=copyNodes[index],computed=live.ownerDocument.defaultView.getComputedStyle(live);
    for(const property of properties)clone.style.setProperty(property,computed.getPropertyValue(property));
    if(live.tagName==='IMG'){
      clone.src=live.src;
      if(!live.src.startsWith('data:')&&live.complete&&live.naturalWidth&&live.naturalWidth*live.naturalHeight<=4000000){
        try{const canvas=doc().createElement('canvas');canvas.width=live.naturalWidth;canvas.height=live.naturalHeight;canvas.getContext('2d').drawImage(live,0,0);clone.src=canvas.toDataURL('image/png');}catch{}
      }
    }
  });
  const result={schema:'deckroom.element',version:1,html:copy.outerHTML,box:elementSnapshot(el).box,source:selectedContext(),sourceDeck:state.deck.id};
  if(JSON.stringify(result).length>8*1024*1024)throw Error('这个对象的素材过大，请缩小图片后再复制');
  return result;
}
function safeClipboardElement(html){
  const template=doc().createElement('template');template.innerHTML=html;
  const root=template.content.firstElementChild;if(!root||['SCRIPT','STYLE','IFRAME','OBJECT','EMBED','LINK','META','BASE','SECTION','DECK-STAGE'].includes(root.tagName))throw Error('无法粘贴这个对象');
  root.querySelectorAll('script,style,iframe,object,embed,link,meta,base').forEach(el=>el.remove());
  const nodes=[root,...root.querySelectorAll('*')],ids=new Map();
  for(const node of nodes){
    if(node.id){const id='dr-id-'+crypto.randomUUID().slice(0,12);ids.set(node.id,id);node.id=id;}
    for(const attr of [...node.attributes])if(/^on/i.test(attr.name)||attr.name==='contenteditable'||attr.name==='spellcheck'||attr.name.startsWith('data-deckroom-')||(/^javascript:/i.test(attr.value.trim())&&['src','href','xlink:href'].includes(attr.name)))node.removeAttribute(attr.name);
    if(node===root||node.dataset.nodeId)node.dataset.nodeId='dr-'+crypto.randomUUID().slice(0,12);
  }
  for(const node of nodes)for(const attr of [...node.attributes]){
    let value=attr.value.replace(/url\(#([^)]*)\)/g,(match,id)=>ids.has(id)?'url(#'+ids.get(id)+')':match);
    if(['href','xlink:href'].includes(attr.name)&&value.startsWith('#')&&ids.has(value.slice(1)))value='#'+ids.get(value.slice(1));
    if(['aria-labelledby','aria-describedby','for'].includes(attr.name))value=value.split(/\s+/).map(id=>ids.get(id)||id).join(' ');
    if(value!==attr.value)node.setAttribute(attr.name,value);
  }
  return root;
}
function insertElement(payload){
  if(state.presenting||!state.deck||!state.canEdit)return;commitText();
  const el=safeClipboardElement(payload.html),parent=slides()[currentIndex()]||doc().body;
  const bounds=paged()?designSize():{width:Math.max(doc().documentElement.scrollWidth,frame.clientWidth),height:Math.max(doc().documentElement.scrollHeight,frame.clientHeight)};
  const box=payload.box||{x:120,y:150,width:700,height:90};
  const x=Math.max(0,Math.min(bounds.width-Math.min(box.width,bounds.width),Number(box.x||0)+24));
  const y=Math.max(0,Math.min(bounds.height-Math.min(box.height,bounds.height),Number(box.y||0)+24));
  Object.assign(el.style,{position:'absolute',inset:'auto',left:x+'px',top:y+'px',width:Math.max(16,Number(box.width)||700)+'px',height:Math.max(16,Number(box.height)||90)+'px',margin:'0',translate:'none',minWidth:'0',minHeight:'0',maxWidth:'none',maxHeight:'none',boxSizing:'border-box',zIndex:'2'});
  parent.append(el);if(!state.edit)setEdit(true);select(el);
  const after=elementSnapshot(el),change={...selectedContext(),type:'insert',source:payload.source||null,before:null,after:changeSnapshot(after)};
  pushAction({kind:'insert',el,parent,next:null},change);frame.contentWindow.focus();
}
function deleteSelection(){
  if(!state.selected||state.presenting)return;commitText();const el=state.selected,parent=el.parentNode,next=el.nextSibling;
  const change={...selectedContext(),type:'delete',before:changeSnapshot(elementSnapshot(el)),after:null};
  el.remove();clearSelection();pushAction({kind:'delete',el,parent,next},change);frame.contentWindow.focus();
}
function pasteText(value){
  if(!value)return;
  if(value.startsWith(CLIPBOARD_PREFIX)){
    if(value.length>8*1024*1024)throw Error('剪贴板对象过大');
    const payload=JSON.parse(value.slice(CLIPBOARD_PREFIX.length));
    if(payload.schema!=='deckroom.element'||typeof payload.html!=='string')throw Error('剪贴板对象格式无效');
    insertElement(payload);return;
  }
  // Inherit the page's own font and colour so pasted text stays readable on dark or branded slides.
  const el=doc().createElement('p');el.textContent=value;Object.assign(el.style,{fontSize:paged()?'36px':'',lineHeight:'1.5',whiteSpace:'pre-wrap'});
  insertElement({html:el.outerHTML,box:{x:120,y:150,width:700,height:Math.max(70,Math.min(600,value.split('\n').length*55))}});
}
function bindClipboard(targetDocument){
  for(const type of ['copy','cut'])targetDocument.addEventListener(type,event=>{
    if(!state.edit||typingTarget(event.target)||!state.selected||state.presenting)return;
    try{const payload=clipboardSnapshot();event.clipboardData.setData('text/plain',CLIPBOARD_PREFIX+JSON.stringify(payload));event.clipboardData.setData('text/html',payload.html);event.preventDefault();state.clipboard=payload;if(type==='cut')deleteSelection();}catch(error){toast(error.message);}
  },true);
  targetDocument.addEventListener('paste',event=>{
    if(!state.edit||typingTarget(event.target)||state.presenting||!state.deck)return;
    const text=event.clipboardData.getData('text/plain'),image=[...event.clipboardData.files].find(file=>/^image\/(png|jpeg|webp|gif)$/.test(file.type));
    if(!text&&!image)return;event.preventDefault();event.stopImmediatePropagation();
    try{
      if(text){pasteText(text);return;}
      if(image.size>5*1024*1024)throw Error('粘贴图片不能超过 5 MB');
      const deckId=state.deck.id,reader=new FileReader();reader.onload=()=>{if(state.deck?.id!==deckId)return;const img=doc().createElement('img');img.src=reader.result;img.alt='粘贴的图片';img.onload=()=>{const width=Math.min(700,img.naturalWidth);insertElement({html:img.outerHTML,box:{x:120,y:150,width,height:width*img.naturalHeight/img.naturalWidth}});};};reader.readAsDataURL(image);
    }catch(error){toast(error.message);}
  },true);
}
function getPosition(el=state.selected) {
  const values=(el?.style.translate||'0 0').split(/\s+/).map(parseFloat);
  return {x:Number.isFinite(values[0])?values[0]:0,y:Number.isFinite(values[1])?values[1]:0};
}
function updatePosition() { const p=getPosition(); $('positionX').value=Math.round(p.x); $('positionY').value=Math.round(p.y); }
// translate, width and height do not apply to inline boxes such as span/b/a: make them inline-block first.
function ensureBox(el){if(el.ownerDocument.defaultView.getComputedStyle(el).display==='inline')el.style.display='inline-block';}
function setPosition(x,y) { if(!state.selected)return; ensureBox(state.selected); state.selected.style.translate=`${x}px ${y}px`; updatePosition(); selectionRect(); }
function selectionRect() {
  const el=state.selected, box=$('selectionBox');
  if(!state.edit || !el?.isConnected) {box.hidden=true;return;}
  const slide=slides().find(page=>page.contains(el)), s=stage();
  if(slide&&slides().includes(slide)&&slide!==slides()[currentIndex()]) { box.hidden=true;return; }
  const r=el.getBoundingClientRect();
  box.hidden=false; box.style.left=r.x+'px';box.style.top=r.y+'px';box.style.width=r.width+'px';box.style.height=r.height+'px';
  $('selectionLabel').textContent=el.tagName==='IMG'?'图片':textLeaf(el)?'文本框':'容器';
  box.classList.toggle('editing-text',!!state.inline);
}
function fitSurface() {
  document.body.classList.toggle('native-content',!paged());
  if(!paged()){Object.assign($('surface').style,{width:'100%',height:'100%',aspectRatio:'auto'});selectionRect();return;}
  $('surface').style.height='';
  if(state.presenting){$('surface').style.width='100%';$('surface').style.aspectRatio='auto';fitPure();return;}
  const surround=document.querySelector('.stage-surround'), width=surround.clientWidth-68, height=surround.clientHeight-58;
  const size=designSize(),ratio=size.width/size.height;
  $('surface').style.width=Math.max(10,Math.min(width,height*ratio))+'px';
  $('surface').style.aspectRatio=String(ratio);
  fitPure();selectionRect();
}
function updatePage(){
  const pages=paged(),index=currentIndex(),total=slides().length;
  for(const id of ['prevButton','nextButton','pageNumber','audiencePrev','audienceNext'])$(id).hidden=!pages;
  for(const id of ['nativeBack','nativeHome','audienceBack','audienceHome'])$(id).hidden=pages;
  $('nativeBack').disabled=$('audienceBack').disabled=(state.nativeHistory?.length||0)<2;
  $('pageNumber').textContent=pages?`${index+1} / ${total}`:'';
  $('resetButton').textContent=pages?'从头 ↺':'重新载入 ↺';$('audienceReset').textContent=pages?'从头 ↺':'重新载入 ↺';
  $('resetButton').title=pages?'回到第一页并重置计时 · R':'重新载入当前页面并重置计时';
  $('exitPresenting').textContent=pages?'退出演示 · Esc':'退出演示';
  $('editButton').disabled=!state.canEdit;
  $('pdfButton').disabled=!state.deck||!!state.preparing||!!state.loading;
  $('pptButton').disabled=!pages||!!state.preparing||!!state.loading||!!state.rendering;
  $('pdfButton').disabled||=!!state.rendering;
  $('pdfButton').textContent=pages?'保存整稿 PDF':'保存 PDF';
  $('pdfButton').title=pages?'打印全部 '+total+' 页，不限于当前页':'打印当前网页；动态稿件需先适配为完整幻灯片';
  document.body.classList.toggle('read-only-content',!state.canEdit);
  for(const id of ['prevButton','audiencePrev'])$(id).disabled=index<=0;
  for(const id of ['nextButton','audienceNext'])$(id).disabled=index>=total-1;
  $('modeHint').textContent=state.edit?'单击选中 · 拖动对象移动 · 双击改字':pages?'← → 翻页 · 点击「轻编辑」修改':'自由网页 · 保留滚动和原生交互';
  selectionRect();presentation?.sync();
}
function navigate(delta){goTo(currentIndex()+delta);}
function goTo(index){if(!paged())return;commitText();clearSelection();const old=slides()[currentIndex()],s=stage(),list=slides();state.page=Math.min(Math.max(index,0),Math.max(0,list.length-1));if(old&&old!==list[state.page])old.querySelectorAll('video,audio').forEach(media=>media.pause());if(s)s.goTo(state.page);else if(state.pure)list.forEach((page,i)=>page.toggleAttribute('data-deck-active',i===state.page));updatePage();}
async function navigateNative(home=false){
  if(state.busy)return;commitText();if(state.dirty)await save();
  if(home){state.nativeHistory=[];loadFrame(state.deck.url);}
  else{if((state.nativeHistory?.length||0)<2)return;state.nativeHistory.pop();const previous=state.nativeHistory.at(-1);loadFrame(previous?.url||state.deck.url);}
}
async function resetPresentation(){if(paged())goTo(0);else{if(state.busy||state.preparing)return;commitText();if(state.dirty)await save();loadFrame(state.currentUrl||state.deck.url);}presentation.resetTimer();toast(paged()?'已回到第一页，计时归零':'已重新载入页面，计时归零');}
function readNotes(){
  const tag=doc()?.querySelector('#speaker-notes');
  try{if(tag){const notes=JSON.parse(tag.textContent);if(Array.isArray(notes))return notes.map(note=>typeof note==='string'?note:'');}}catch{}
  return paged()?slides().map(slide=>slide.dataset.notes||slide.querySelector('aside.notes')?.textContent||''):[doc()?.body?.dataset.notes||''];
}
function presentationState(){
  if(!state.deck)return null;
  if(!paged())return {deckName:state.deck.name,index:0,total:0,title:doc()?.title||state.deck.name,nextTitle:'自由网页：在作品中继续交互',notes:readNotes()[0]||'',paged:false,editable:!!state.canEdit};
  const list=slides(),index=currentIndex();
  const title=slide=>slide?.querySelector('h1,h2,h3')?.textContent?.trim()||slide?.getAttribute('aria-label')||'';
  return {deckName:state.deck.name,index,total:stage()?.length||list.length||1,title:title(list[index]),nextTitle:title(list[index+1]),notes:readNotes()[index]||'',paged:true,editable:!!state.canEdit};
}
async function saveSpeakerNotes(index,text){
  if(state.busy||!state.canEdit)throw Error('当前页面暂不可编辑，请返回本地 HTML 后重试');
  commitText();const notes=readNotes();while(notes.length<slides().length)notes.push('');
  let tag=doc().querySelector('#speaker-notes');if(!tag){tag=doc().createElement('script');tag.type='application/json';tag.id='speaker-notes';tag.textContent='[]';doc().body.append(tag);}
  const before=elementSnapshot(tag);notes[index]=text;tag.textContent=JSON.stringify(notes,null,2).replace(/</g,'\\u003c');
  record(tag,before,'notes',{entry:state.activeEntry,page:paged()?index+1:null,slide:slides()[index]?.dataset.slideId||null,node:'speaker-notes',role:'speaker-notes',selector:'#speaker-notes'});
  stage()?._loadNotes?.();await save();presentation.sync();
}
function showAudienceControls(){if(!state.presenting)return;document.body.classList.add('show-audience-controls');clearTimeout(audienceControlsTimer);audienceControlsTimer=setTimeout(()=>document.body.classList.remove('show-audience-controls'),2200);}
async function leavePresentation(){state.presenting=false;document.body.classList.remove('presenting','show-audience-controls');$('audienceControls').hidden=true;clearTimeout(audienceControlsTimer);presentation.pause();try{await fullscreen.exit();}finally{fitSurface();}}
async function startPresentation(){
  if(!state.deck||state.busy||state.preparing||state.loading||state.startingPresentation)return;
  state.startingPresentation=true;
  try{
    let requested;
    if(fullscreen.key){
      if(paged())try{presentation.open({focus:false});}catch(error){toast(error.message);}
      requested=state.nativeReady.then(()=>fullscreen.enter()).then(()=>null,error=>error);
    }else{
      requested=fullscreen.enter().then(()=>null,error=>error);
      if(paged())try{presentation.open({focus:false});}catch{/* Browser activation may allow only fullscreen. The presenter button remains available. */}
    }
    commitText();if(state.dirty)await save();setEdit(false);setPanel(null);
    state.presenting=true;document.body.classList.add('presenting');$('audienceControls').hidden=false;
    const error=await requested;if(error)throw error;
    presentation.start();fitSurface();showAudienceControls();frame.contentWindow.focus();
  }catch(error){await leavePresentation().catch(()=>{});toast(error.message);}
  finally{state.startingPresentation=false;}
}
function setPanel(name) {
  commitText();state.panel=name;
  $('inspector').hidden=name!=='edit';$('historyPanel').hidden=name!=='history';$('exportPanel').hidden=name!=='export';
  $('historyButton').setAttribute('aria-pressed',String(name==='history'));
  $('exportMenuButton').setAttribute('aria-pressed',String(name==='export'));
  if(name==='history')renderHistory();
  requestAnimationFrame(fitSurface);
}
function setEdit(enabled) {
  if(enabled&&!state.canEdit)return toast('此页面可展示；直接编辑仅支持稿件目录内的 HTML 页面');
  commitText();state.edit=enabled;
  $('editButton').setAttribute('aria-pressed',String(enabled));
  $('modeHint').textContent=enabled?'单击选中 · 拖动对象移动 · 双击改字':'单击选中 · 双击改字 · ← → 翻页';
  const d=doc();if(d)d.documentElement.dataset.deckroomEdit=enabled?'true':'false';
  if(enabled)setPanel('edit');else{clearSelection();if(state.panel==='edit')setPanel(null);}
  updatePage();
}
function renderDecks() {
  $('decks').replaceChildren();
  for(const item of state.decks) {
    const button=document.createElement('button');button.className='deck'+(state.deck?.id===item.id?' active':'');
    const title=document.createElement('div');title.textContent=item.name;
    const detail=document.createElement('span');detail.textContent=item.error?'无法读取：'+item.error:`${item.versions.length} 个版本 · HTML`;
    if(item.error){button.title=item.error;button.classList.add('broken');}
    button.append(title,detail);button.onclick=run(()=>openDeck(item));$('decks').append(button);
  }
}
async function refreshDecks() {state.decks=await api('/api/decks');renderDecks();}
window.addEventListener('focus',()=>{if(!state.disposed&&!state.busy)refreshDecks().catch(()=>{});});
function resetDocumentState(){state.undo=[];state.redo=[];state.undoBase='';state.savedHistory='';state.changes=[];state.traceBytes=0;state.compacted=0;state.historyNotice=false;state.dirty=false;state.pendingText=null;state.inline=null;state.selected=null;state.pure=null;state.adapter=null;watchSelection();updateDirty();}
function documentUrl(entry){return '/documents/'+state.deck.id+'/'+entry.split('/').map(encodeURIComponent).join('/');}
async function openDeck(deck,force=false) {
  if(state.preparing||state.busy)return;
  commitText();
  if(state.dirty&&!force&&!confirm('当前还有未保存的修改。切换稿件会丢弃这些修改，是否继续？'))return;
  state.exportDrafts??={};
  if(state.deck)state.exportDrafts[state.deck.id]={intent:$('intent').value,scope:$('scope').value};
  while(Object.keys(state.exportDrafts).length>20)delete state.exportDrafts[Object.keys(state.exportDrafts)[0]];
  const changed=state.deck?.id!==deck.id;
  if(changed){if(state.deck){presentation.pause();presentation.resetTimer();state.page=0;}else state.page=undefined;state.nativeHistory=[];state.currentUrl=null;state.activeEntry=null;}
  state.deck=await api('/api/decks/'+deck.id);state.revision=state.deck.revision;
  resetDocumentState();state.canEdit=false;$('displayMode').value=state.deck.viewMode||'auto';
  $('deckName').textContent=state.deck.name;$('filePath').textContent=state.deck.entryPath;$('filePath').title='点击复制：'+state.deck.entryPath;
  $('externalBanner').hidden=true;$('emptyState').hidden=true;$('surface').hidden=false;
  const context=state.exportDrafts[deck.id]||state.deck.exportContext||{};
  $('intent').value=context.intent||'';$('scope').value=context.scope||'current';
  loadFrame(documentUrl(state.activeEntry||state.deck.entry)+'?v='+state.revision.slice(0,12));renderDecks();updateDirty();renderHistory();
}
frame.addEventListener('load',async()=>{
  if(!state.deck)return;
  concealFrame();
  const generation=state.frameGeneration=(state.frameGeneration||0)+1;
  state.prepareController?.abort();state.prepareController=new AbortController();state.preparing=false;
  resetDocumentState();
  const d=doc();state.canEdit=false;state.activeEntry=null;
  let url=null;try{url=frame.contentWindow.location.href;}catch{}
  const runtime=url==='about:srcdoc'?state.runtimeSource:null;
  if(runtime){state.currentUrl=runtime.url;state.activeEntry=runtime.entry;}
  else if(url){state.runtimeSource=null;state.currentUrl=url;state.activeEntry=JemuCompat.documentEntry(url,state.deck.id,location.origin);}
  else state.currentUrl=null;
  state.nativeHistory??=[];
  const historyURL=runtime?runtime.url:url;
  if(state.nativeHistory.at(-1)?.url!==historyURL)state.nativeHistory.push({url:historyURL});
  if(state.nativeHistory.length>32)state.nativeHistory.shift();
  state.canEdit=!!state.activeEntry&&!!d?.head&&!!d?.body&&d.documentElement.tagName==='HTML';
  const mode=state.deck.viewMode||'auto',nativeRequested=mode==='native'||(mode==='auto'&&(d?.querySelector('meta[name="jemu:mode"]')?.content==='native'||d?.documentElement.dataset.jemuMode==='native'));
  if(state.canEdit&&!runtime&&!nativeRequested&&JemuDynamicDeck.hinted(d)){
    const entry=state.activeEntry,sourceURL=state.currentUrl;
    state.preparing=true;state.canEdit=false;fitSurface();updatePage();$('modeHint').textContent='正在准备完整稿件…';
    try{
      const historyURL='/api/decks/'+state.deck.id+'/rendered-pages?entry='+encodeURIComponent(entry);
      const normalized=await JemuDynamicDeck.prepare(d,{url:sourceURL,signal:state.prepareController.signal,historyURL});
      if(generation!==state.frameGeneration||state.prepareController.signal.aborted)return;
      if(normalized){state.runtimeSource={url:sourceURL,entry};state.normalizationPending=true;state.page=Math.min(Math.max(Number(state.page)||0,0),normalized.total-1);frame.srcdoc=normalized.html;return;}
    }catch(error){if(error.name==='AbortError')return;toast(error.message);state.adaptationError=error.message;}
    finally{if(generation===state.frameGeneration)state.preparing=false;}
    state.canEdit=true;
  }
  state.adapter=JemuCompat.detect(state.canEdit?d:null,state.deck.viewMode||'auto');
  if(!state.canEdit){state.loading=false;revealFrame();$('filePath').textContent='外部或非 HTML 内容 · 仅展示';setEdit(false);fitSurface();updatePage();frame.dispatchEvent(new Event('jemu-ready'));return;}
  $('filePath').textContent=state.deck.path+'/'+state.activeEntry;$('filePath').title='当前页面：'+state.activeEntry;
  clearSelection();
  attachPureDocument();
  if(stage()&&Number.isInteger(state.page))stage().goTo(state.page);
  const style=d.createElement('style');style.dataset.deckroomUi='true';style.textContent='[data-deckroom-inline]{outline:2px solid #3c947c;outline-offset:4px;cursor:text!important;white-space:pre-wrap!important;text-transform:none!important}html[data-deckroom-edit="true"] body *{-webkit-user-select:none;user-select:none}html[data-deckroom-edit="true"] [data-deckroom-inline],html[data-deckroom-edit="true"] [data-deckroom-inline] *{-webkit-user-select:text;user-select:text}';d.head.append(style);
  // The workspace supplies navigation; keep the slide's own controls for standalone presentation only.
  if(stage()?.shadowRoot){const controls=d.createElement('style');controls.textContent='.overlay,.tapzones{display:none!important}';stage().shadowRoot.append(controls);}
  d.addEventListener('click',event=>{
    if(state.suppressClick){state.suppressClick=false;event.preventDefault();event.stopImmediatePropagation();return;}
    const target=event.target instanceof frame.contentWindow.Element?event.target:null;
    if(!target)return;
    if(target.hasAttribute('data-deckroom-ui')||target.hasAttribute('data-jemu-canvas')){if(!state.presenting)clearSelection();return;}
    if(target===stage()){if(!state.presenting)clearSelection();return;}
    if(!state.edit)return;
    if(state.inline?.el.contains(target))return;
    event.preventDefault();event.stopImmediatePropagation();
    const el=target.closest('[data-node-id],[data-slot],[data-role],h1,h2,h3,p,span,b,strong,em,a,li,article,figure,img,td,th,div');select(el);frame.contentWindow.focus();
  },true);
  // Drag the selected object itself, like presentation software; a click without movement still selects inside it.
  d.addEventListener('pointerdown',event=>{
    state.suppressClick=false;
    const el=state.selected;
    if(!state.edit||state.presenting||event.button!==0||state.inline||!el||!el.contains(event.target))return;
    const before=elementSnapshot(el),p=getPosition(el),x=event.clientX,y=event.clientY,canvas=designCanvas()?.getBoundingClientRect(),scale=canvas?canvas.width/designSize().width:1;
    let moved=false;event.target.setPointerCapture?.(event.pointerId);
    const move=e=>{if(!moved&&Math.hypot(e.clientX-x,e.clientY-y)<4)return;moved=true;e.preventDefault();setPosition(Math.round(p.x+(e.clientX-x)/scale),Math.round(p.y+(e.clientY-y)/scale));};
    const end=()=>{d.removeEventListener('pointermove',move,true);d.removeEventListener('pointerup',end,true);d.removeEventListener('pointercancel',end,true);if(moved){state.suppressClick=true;record(el,before,'move');}};
    d.addEventListener('pointermove',move,true);d.addEventListener('pointerup',end,true);d.addEventListener('pointercancel',end,true);
  },true);
  d.addEventListener('dragstart',event=>{if(state.edit&&!state.presenting)event.preventDefault();},true);
  d.addEventListener('dblclick',event=>{if(state.presenting||!state.edit)return;const el=event.target.closest?.('h1,h2,h3,p,span,b,strong,em,a,li,td,th,[data-node-id]');if(!textLeaf(el))return;event.preventDefault();select(el);beginInline(el,true);},true);
  d.addEventListener('input',event=>{if(state.inline?.el!==event.target)return;const active=state.inline;if(!state.pendingText)state.pendingText={el:active.el,before:active.before};active.changed=true;$('textValue').value=active.el.innerText;updateDirty();selectionRect();},true);
  d.addEventListener('blur',event=>{if(state.inline?.el===event.target)commitText();},true);
  d.addEventListener('mousemove',showAudienceControls);
  bindClipboard(d);
  d.addEventListener('keydown',keyboard,true);
  frame.contentWindow.addEventListener('beforeunload',event=>{commitText();if(state.dirty){event.preventDefault();event.returnValue='';}else{concealFrame();state.loading=true;}});
  frame.contentWindow.addEventListener('hashchange',()=>{const url=frame.contentWindow.location.href;if(state.nativeHistory.at(-1)?.url!==url){state.nativeHistory.push({url});if(state.nativeHistory.length>32)state.nativeHistory.shift();}state.currentUrl=url;updatePage();});
  d.addEventListener('slidechange',()=>{clearSelection();updatePage();});
  if(state.edit)d.documentElement.dataset.deckroomEdit='true';
  state.preparing=true;state.busy=true;
  try {
    if(state.normalizationPending||!state.deck.jemuVersion){
      $('modeHint').textContent='正在保存 Jemu 工作版本…';
      const meta=await api('/api/decks/'+state.deck.id+'/prepare',{entry:state.activeEntry,baseRevision:state.revision,...(state.normalizationPending?{html:serialize()}:{})});
      if(generation!==state.frameGeneration)return;
      state.deck=meta;state.revision=meta.revision;state.normalizationPending=false;
      state.decks=state.decks.map(item=>item.id===meta.id?meta:item);renderDecks();renderHistory();updateDirty();
    }
    // Lay out and settle fonts while the single preview is hidden; never show
    // the source toolbar or the unscaled full-page layout between loads.
    fitSurface();
    await Promise.race([d.fonts?.ready||Promise.resolve(),new Promise(resolve=>setTimeout(resolve,1500))]);
    await new Promise(resolve=>{let first,second;const done=()=>{clearTimeout(timer);cancelAnimationFrame(first);cancelAnimationFrame(second);resolve();};const timer=setTimeout(done,150);first=requestAnimationFrame(()=>{second=requestAnimationFrame(done);});});
  } catch(error) {toast('Jemu 工作版本未保存：'+error.message+'。原稿和已有备份仍保留。');}
  finally {if(generation===state.frameGeneration){state.preparing=false;state.busy=false;updateDirty();}}
  if(generation!==state.frameGeneration)return;
  state.loading=false;fitSurface();updatePage();revealFrame();setTimeout(updatePage,300);frame.dispatchEvent(new Event('jemu-ready'));
});
function serialize() {
  commitText();
  if(!state.canEdit)throw Error('当前页面不能直接保存为本地 HTML');
  const copy=doc().documentElement.cloneNode(true);
  copy.querySelectorAll('[data-deckroom-ui="content-stage"]').forEach(wrapper=>{const canvas=wrapper.querySelector('[data-jemu-canvas]');if(canvas)wrapper.replaceWith(...canvas.childNodes);});
  copy.querySelectorAll('[data-deckroom-ui="source-body"]').forEach(wrapper=>wrapper.replaceWith(...wrapper.childNodes));
  copy.removeAttribute('data-deckroom-edit');
  copy.querySelectorAll('[data-jemu-page]').forEach(el=>el.removeAttribute('data-jemu-page'));
  copy.querySelectorAll('[data-jemu-suppressed]').forEach(el=>el.removeAttribute('data-jemu-suppressed'));
  copy.querySelectorAll('[data-deckroom-ui]').forEach(el=>el.remove());
  if(paged())copy.querySelectorAll('[data-deck-active],[data-deck-slide],[data-screen-label],[data-om-validate]').forEach(el=>{
    for(const key of ['data-deck-active','data-deck-slide','data-screen-label','data-om-validate'])el.removeAttribute(key);
  });
  // The saved bytes are UTF-8: old charset declarations must agree with them.
  copy.querySelectorAll('meta[charset]').forEach(meta=>meta.setAttribute('charset','utf-8'));
  copy.querySelectorAll('meta[http-equiv]').forEach(meta=>{if(meta.httpEquiv.toLowerCase()==='content-type')meta.content='text/html; charset=utf-8';});
  if(!copy.querySelector('meta[charset]')){const meta=doc().createElement('meta');meta.setAttribute('charset','utf-8');copy.querySelector('head').prepend(meta);}
  const type=doc().doctype?new XMLSerializer().serializeToString(doc().doctype)+'\n':'';
  return type+copy.outerHTML+'\n';
}
async function save() {
  commitText();if(!state.deck||(!state.dirty&&!state.normalizationPending)||state.busy)return;
  state.busy=true;updateDirty();
  try {
    const savedHistory=historySignature(),changes=[...state.changes],compacted=state.compacted;
    // Detach this batch so new edits made during saving remain in the next batch.
    state.changes=[];state.traceBytes=0;state.compacted=0;
    let meta;
    try{meta=await api('/api/decks/'+state.deck.id+'/save',{html:serialize(),entry:state.activeEntry,baseRevision:state.revision,label:'轻编辑保存',changes:compacted?[{type:'history-compacted',count:compacted,note:'较早的会话操作已合并省略；以当前 HTML 和最终文件差异为准。'},...changes]:changes});}
    catch(error){const newer=state.changes;state.changes=[];state.traceBytes=0;state.compacted+=compacted;for(const change of [...changes,...newer])JemuResources.appendTrace(state,change);throw error;}
    state.deck=meta;state.revision=meta.revision;state.savedHistory=savedHistory;state.normalizationPending=false;
    state.decks=state.decks.map(d=>d.id===meta.id?meta:d);renderDecks();renderHistory();toast('已保存一个新版本');
  } catch(error) {if(error.status===409){$('externalBanner').hidden=false;$('externalBanner').querySelector('span').textContent='文件已在外部更新。你的草稿仍保留，请先下载草稿，再载入外部版本。';}throw error;}
  finally {state.busy=false;updateDirty();}
}
function undo() {
  commitText();const action=state.undo.pop();if(!action)return;
  if(action.kind==='insert'){action.el.remove();clearSelection();}
  else if(action.kind==='delete'){action.parent.insertBefore(action.el,action.next?.parentNode===action.parent?action.next:null);select(action.el);}
  else{applySnapshot(action.el,action.before);if(action.type==='notes'){clearSelection();stage()?._loadNotes?.();presentation.sync();}else select(action.el);}
  JemuResources.appendTrace(state,{...action.change,type:'undo',actionType:action.change.type,before:action.change.after,after:action.change.before});state.redo.push(action);updateDirty();
}
function applySnapshot(el,snapshot){if(typeof snapshot.html==='string'&&el.innerHTML!==snapshot.html)el.innerHTML=snapshot.html;el.style.cssText=snapshot.cssText||'';}
function redo(){
  commitText();const action=state.redo.pop();if(!action)return;
  if(action.kind==='insert'){action.parent.insertBefore(action.el,action.next?.parentNode===action.parent?action.next:null);select(action.el);}
  else if(action.kind==='delete'){action.el.remove();clearSelection();}
  else{applySnapshot(action.el,action.after);if(action.type==='notes'){stage()?._loadNotes?.();presentation.sync();}else select(action.el);}
  state.undo.push(action);JemuResources.appendTrace(state,{...action.change,type:'redo',actionType:action.change.type});updateDirty();
}
function renderHistory() {
  $('versions').replaceChildren();if(!state.deck)return;
  for(const [i,item] of [...state.deck.versions].reverse().entries()) {
    const row=document.createElement('div');row.className='version';const label=document.createElement('b');label.textContent=item.label;
    const date=document.createElement('small');date.textContent=new Date(item.created).toLocaleString('zh-CN',{hour12:false});row.append(label,date);
    if(i===0){const tag=document.createElement('span');tag.className='current-tag';tag.textContent='当前版本';row.append(tag);}
    else{const button=document.createElement('button');button.textContent='恢复这个版本';button.onclick=run(async()=>{
      commitText();if(state.dirty&&!confirm('恢复将放弃未保存修改，是否继续？'))return;
      if(!confirm('恢复这个版本？已保存的后续版本仍会保留。'))return;
      const meta=await api('/api/decks/'+state.deck.id+'/restore',{version:item.id,baseRevision:state.revision});
      await openDeck(meta,true);await refreshDecks();toast('已恢复，并保留了原来的版本记录');
    });row.append(button);}
    $('versions').append(row);
  }
}
async function poll() {
  if(document.hidden||state.presenting||state.busy||state.preparing||state.loading||state.polling||!state.deck||$('importDialog').open)return;
  state.polling=true;
  try {
  const id=state.deck.id, meta=await api('/api/decks/'+id+'/status');
  if(state.deck?.id!==id)return;
  if(meta.revision!==state.revision) {
    if(state.dirty){$('externalBanner').hidden=false;return;}
    await openDeck(state.deck,true);
    toast('已载入外部修改，并保存版本记录');
  }
  } finally {state.polling=false;}
}
function keyboard(event) {
  // Native HTML owns all keys in its viewport until the user enters editing.
  if(event.view===frame.contentWindow&&!paged()&&!state.edit)return;
  if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='s'){event.preventDefault();event.stopImmediatePropagation();run(save)();return;}
  const tag=event.target?.tagName;
  if(event.target?.isContentEditable){
    if(event.key==='Escape'){event.preventDefault();commitText();event.target.blur();}
    event.stopImmediatePropagation();return;
  }
  if(['INPUT','TEXTAREA','SELECT'].includes(tag))return;
  if(!state.edit&&event.target?.closest?.('video,audio,button,a,[role="button"]')&&event.view===frame.contentWindow)return;
  if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='z'){event.preventDefault();event.stopImmediatePropagation();event.shiftKey?redo():undo();return;}
  if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='y'){event.preventDefault();event.stopImmediatePropagation();redo();return;}
  if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='d'&&state.selected&&!state.presenting){event.preventDefault();event.stopImmediatePropagation();try{insertElement(clipboardSnapshot());}catch(error){toast(error.message);}return;}
  if(state.selected&&state.edit&&!state.presenting){
    if(['Delete','Backspace'].includes(event.key)){event.preventDefault();event.stopImmediatePropagation();deleteSelection();return;}
    if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)){event.preventDefault();event.stopImmediatePropagation();const before=elementSnapshot(state.selected),p=getPosition(),step=event.shiftKey?10:1;setPosition(p.x+(event.key==='ArrowRight'?step:event.key==='ArrowLeft'?-step:0),p.y+(event.key==='ArrowDown'?step:event.key==='ArrowUp'?-step:0));record(state.selected,before,'move');return;}
    if(event.key==='Enter'||event.key==='F2'){event.preventDefault();event.stopImmediatePropagation();beginInline(state.selected);return;}
  }
  if(event.key==='Escape'){if(state.presenting){event.preventDefault();event.stopImmediatePropagation();run(leavePresentation)();return;}if(state.selected){clearSelection();event.stopImmediatePropagation();return;}if(state.edit)setEdit(false);return;}
  if(!paged())return;
  if(!event.ctrlKey&&!event.metaKey&&event.key.toLowerCase()==='r'){event.preventDefault();event.stopImmediatePropagation();resetPresentation();return;}
  if(['ArrowRight','ArrowLeft','ArrowUp','ArrowDown','PageUp','PageDown','Home','End'].includes(event.key)){event.preventDefault();event.stopImmediatePropagation();if(event.key==='Home')goTo(0);else if(event.key==='End')goTo(slides().length-1);else navigate(['ArrowRight','ArrowDown','PageDown'].includes(event.key)?1:-1);return;}
  if(event.key.toLowerCase()==='s'&&!event.ctrlKey&&!event.metaKey){event.preventDefault();event.stopImmediatePropagation();try{presentation.open();}catch(error){toast(error.message);}}
}
document.addEventListener('keydown',keyboard);
bindClipboard(document);
document.querySelector('.stage-surround').addEventListener('pointerdown',event=>{if(event.target===event.currentTarget&&!state.presenting)clearSelection();});
window.addEventListener('beforeunload',event=>{commitText();if(state.dirty){event.preventDefault();event.returnValue='';}});
const surfaceObserver=new ResizeObserver(fitSurface);surfaceObserver.observe(document.querySelector('.stage-surround'));
let pollInterval;
function schedulePoll(){clearTimeout(pollInterval);if(!state.disposed&&!document.hidden)pollInterval=setTimeout(async()=>{await poll().catch(()=>{});schedulePoll();},10000);}
schedulePoll();
document.addEventListener('visibilitychange',()=>{watchSelection();schedulePoll();if(!document.hidden){fitSurface();poll().catch(()=>{});}});
window.addEventListener('pagehide',()=>{state.disposed=true;clearInterval(state.selectionTimer);clearTimeout(pollInterval);clearTimeout(toastTimer);clearTimeout(audienceControlsTimer);surfaceObserver.disconnect();state.undo=[];state.redo=[];state.changes=[];state.clipboard=null;state.selected=null;state.pure=null;});
$('editButton').onclick=()=>{if(state.deck)setEdit(!state.edit);};
$('historyButton').onclick=()=>setPanel(state.panel==='history'?null:'history');
$('cloudButton').onclick=run(()=>openSettings());
$('desktopButton').onclick=run(async()=>{commitText();if(state.dirty)await save();await api('/api/open-desktop',{deck:state.deck?.id});toast('已打开桌面窗口；点击「开始演示」进入无边框全屏');});
$('exportMenuButton').onclick=()=>setPanel(state.panel==='export'?null:'export');
document.querySelectorAll('.close-panel').forEach(button=>button.onclick=()=>{if(state.panel==='edit')setEdit(false);setPanel(null);});
$('prevButton').onclick=()=>navigate(-1);$('nextButton').onclick=()=>navigate(1);
$('notesButton').onclick=()=>{if(!state.deck)return;try{presentation.open();}catch(error){toast(error.message);}};
$('presentButton').onclick=startPresentation;
$('resetButton').onclick=run(resetPresentation);
$('nativeBack').onclick=$('audienceBack').onclick=run(()=>navigateNative());
$('nativeHome').onclick=$('audienceHome').onclick=run(()=>navigateNative(true));
$('displayMode').onchange=run(async()=>{if(state.busy)return;commitText();if(state.dirty)await save();const meta=await api('/api/decks/'+state.deck.id+'/view-mode',{mode:$('displayMode').value});await openDeck(meta,true);});
$('toolbarUndo').onclick=undo;$('toolbarRedo').onclick=redo;
$('audiencePrev').onclick=()=>navigate(-1);$('audienceNext').onclick=()=>navigate(1);$('audienceReset').onclick=run(resetPresentation);
$('timerButton').onclick=()=>presentation.toggle();
$('exitPresenting').onclick=run(leavePresentation);
$('audiencePresenter').onclick=$('notesButton').onclick;
$('fullscreenButton').textContent='全屏演示';$('fullscreenButton').onclick=run(()=>fullscreen.enter());
async function printNative(){
  if(!state.deck)return;
  if(state.busy||state.preparing||state.loading)throw Error('正在准备稿件，请稍后再导出');
  if(!paged()&&JemuDynamicDeck.hinted(doc()))throw Error('动态稿件尚未完整展开，请切换到自动识别／幻灯片后再导出整稿');
  commitText();if(state.dirty||state.normalizationPending)await save();
  const d=doc();if(!d)throw Error('无法打印当前外部页面，请在原页面打印');
  let timer;
  const ready=Promise.all([d.fonts?.ready||Promise.resolve(),...[...d.images].map(img=>img.decode?.().catch(()=>{})||Promise.resolve())]);
  try{await Promise.race([ready,new Promise(resolve=>{timer=setTimeout(resolve,5000);})]);}finally{clearTimeout(timer);}
  if(paged())toast('已准备整稿 '+slides().length+' 页，请在打印窗口选择保存为 PDF');
  frame.contentWindow.focus();frame.contentWindow.print();
}
async function renderExport(kind){
  if(!state.deck||state.rendering)return;
  if(state.busy||state.preparing||state.loading)throw Error('正在准备稿件，请稍后再导出');
  if(!paged()){
    if(kind==='pdf')return printNative();
    throw Error('PPT 导出需要明确的幻灯片页面；自由网页请先选择可识别的幻灯片');
  }
  commitText();if(state.dirty||state.normalizationPending)await save();
  const size=designSize(),html=JemuExportLayout.build(doc(),{pages:slides(),...size});
  state.rendering=true;updatePage();
  try{
    const deck=state.deck.id;
    let job=await api('/api/decks/'+deck+'/render-start',{kind,html,pages:slides().length,...size,entry:state.activeEntry,baseRevision:state.revision});
    toast('正在独立导出整稿，当前页面仍可翻阅…');
    while(['queued','rendering'].includes(job.status)){
      await new Promise(resolve=>setTimeout(resolve,1000));
      job=await api('/api/decks/'+deck+'/render-jobs/'+job.id);
    }
    if(job.status!=='completed')throw Error(job.message||'导出没有完成');
    const a=document.createElement('a');a.href=job.downloadUrl;a.download='jemu-deck.'+kind;a.click();
    toast((kind==='pptx'?'PPT':'PDF')+' 已生成'+(job.pages?' · '+job.pages+' 页':'')+'，开始下载');
  }finally{state.rendering=false;updatePage();}
}
$('pdfButton').onclick=run(()=>renderExport('pdf'));
$('pptButton').onclick=run(()=>renderExport('pptx'));
$('folderButton').onclick=run(()=>state.deck&&api('/api/decks/'+state.deck.id+'/open-folder',{}));
$('filePath').onclick=run(async()=>{if(state.deck){await navigator.clipboard.writeText(state.activeEntry?state.deck.path+'/'+state.activeEntry:state.deck.entryPath);toast('已复制 HTML 文件路径');}});
$('exportButton').onclick=()=>setPanel('export');
$('saveButton').onclick=run(save);$('undoButton').onclick=undo;$('redoButton').onclick=redo;
$('copyObject').onclick=run(async()=>{const payload=clipboardSnapshot();if(!payload)return;state.clipboard=payload;try{await navigator.clipboard.writeText(CLIPBOARD_PREFIX+JSON.stringify(payload));toast('对象已复制');}catch{toast('已复制到工作间；也可用 Ctrl+C 复制到系统剪贴板');}});
$('pasteObject').onclick=run(async()=>{try{const text=await navigator.clipboard.readText();if(text){pasteText(text);return;}}catch{}if(state.clipboard)insertElement(state.clipboard);else toast('请使用 Ctrl+V 粘贴剪贴板内容');});
$('deleteObject').onclick=deleteSelection;
$('fontFamily').addEventListener('change',()=>applyFont('fontFamily',$('fontFamily').value.trim()));
$('fontSize').addEventListener('change',()=>{const size=Number($('fontSize').value);if(size>0)applyFont('fontSize',Math.min(500,Math.max(1,size))+'pt');});
$('fontBold').onclick=()=>applyFont('fontWeight',$('fontBold').getAttribute('aria-pressed')==='true'?'400':'700');
$('fontItalic').onclick=()=>applyFont('fontStyle',$('fontItalic').getAttribute('aria-pressed')==='true'?'normal':'italic');
$('parentButton').onclick=()=>{if(state.selected)select(state.selected.parentElement);};
$('textValue').addEventListener('input',()=>{if(!state.selected)return;if(!state.pendingText)state.pendingText={el:state.selected,before:elementSnapshot(state.selected)};state.selected.textContent=$('textValue').value;updateDirty();selectionRect();});
$('textValue').addEventListener('blur',commitText);
for(const id of ['positionX','positionY'])$(id).addEventListener('change',()=>{if(!state.selected)return;const before=elementSnapshot(state.selected);setPosition(Number($('positionX').value)||0,Number($('positionY').value)||0);record(state.selected,before,'move');});
$('resetPosition').onclick=()=>{if(!state.selected)return;const before=elementSnapshot(state.selected);state.selected.style.translate='';record(state.selected,before,'move');updatePosition();selectionRect();};
$('selectionBox').addEventListener('pointerdown',event=>{
  const direction=event.target.dataset.drag;if(!direction)return;
  if(!state.selected)return;commitText();event.preventDefault();const el=state.selected, before=elementSnapshot(el), p=getPosition(), x=event.clientX,y=event.clientY;
  const canvas=designCanvas()?.getBoundingClientRect(),scale=canvas?canvas.width/designSize().width:1;
  const handle=event.currentTarget;handle.setPointerCapture(event.pointerId);
  const move=e=>{
    const dx=(e.clientX-x)/scale,dy=(e.clientY-y)/scale;
    if(direction==='move'){setPosition(Math.round(p.x+dx),Math.round(p.y+dy));return;}
    const west=direction.includes('w'),north=direction.includes('n');
    let width=Math.max(16,before.box.width+(west?-dx:direction.includes('e')?dx:0)),height=Math.max(16,before.box.height+(north?-dy:direction.includes('s')?dy:0));
    if(direction.length===2&&(e.shiftKey||['IMG','SVG'].includes(el.tagName))){const ratio=before.box.width/before.box.height;if(Math.abs(dx)>Math.abs(dy)*ratio)height=width/ratio;else width=height*ratio;}
    ensureBox(el);Object.assign(el.style,{width:Math.round(width)+'px',height:Math.round(height)+'px',boxSizing:'border-box',minWidth:'0',minHeight:'0',maxWidth:'none',maxHeight:'none'});
    setPosition(p.x+(west?before.box.width-width:0),p.y+(north?before.box.height-height:0));
  };
  const end=e=>{handle.releasePointerCapture(e.pointerId);handle.removeEventListener('pointermove',move);handle.removeEventListener('pointerup',end);handle.removeEventListener('pointercancel',end);record(el,before,direction==='move'?'move':'resize');};
  handle.addEventListener('pointermove',move);handle.addEventListener('pointerup',end);handle.addEventListener('pointercancel',end);
});
$('reloadButton').onclick=run(async()=>{if(state.dirty&&!confirm('载入外部版本会放弃未保存的草稿。可以先下载草稿。是否继续？'))return;await openDeck(state.deck,true);});
function downloadText(text,name){const url=URL.createObjectURL(new Blob([text],{type:'text/html;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),3000);}
$('draftButton').onclick=()=>downloadText(serialize(),'jemu-draft.html');
async function exportEdits(mode) {
  if(!state.deck||state.exporting)return;state.exporting=true;
  $('exportTrace').disabled=true;$('exportFull').disabled=true;
  const status=$('exportStatus');status.textContent='正在保存并生成编辑物…';
  try {
    commitText();const selection=selectedContext();
    if(state.dirty||state.normalizationPending)await save();
    state.busy=true;
    const response=await fetch('/api/decks/'+state.deck.id+'/export-edit',{method:'POST',headers:{'Content-Type':'application/json','X-Deckroom-Token':window.DECKROOM_TOKEN},body:JSON.stringify({mode,prepare:true,baseRevision:state.revision,selection,scope:$('scope').value,intent:$('intent').value})});
    if(!response.ok){const error=await response.json();if(response.status===409)$('externalBanner').hidden=false;throw new Error(error.error||'导出没有完成');}
    const result=await response.json(),a=document.createElement('a');
    a.href=result.downloadUrl;a.download=mode==='trace'?'jemu-edits.md':'jemu-full.zip';a.click();
    status.textContent=mode==='trace'?'已导出编辑痕迹。把 MD 文件交给已有原稿的 AI 即可。':'已导出完整编辑包。解压可直接展示，也可以把 ZIP 整包交给 AI。';
    toast(mode==='trace'?'编辑痕迹已生成并开始下载':'完整编辑包已生成并开始下载');
  } catch(error){status.textContent=error.message;throw error;}
  finally {state.busy=false;state.exporting=false;$('exportTrace').disabled=false;$('exportFull').disabled=false;updateDirty();}
}
$('exportTrace').onclick=run(()=>exportEdits('trace'));
$('exportFull').onclick=run(()=>exportEdits('full'));
function showImport() {$('importDialog').showModal();}
$('importButton').onclick=showImport;$('emptyImport').onclick=showImport;
function chooseFiles(files,folder) {
  state.importing=[...files].filter(f=>!/(^|\/)(\.git|node_modules|\.DS_Store|Thumbs\.db)(\/|$)/.test(f.webkitRelativePath||f.name));
  const first=state.importing[0];const prefix=folder?((first?.webkitRelativePath||'').split('/')[0]+'/'):'';
  state.importing=state.importing.map(file=>({file,path:folder?file.webkitRelativePath.slice(prefix.length):file.name}));
  const html=state.importing.filter(item=>/\.html?$/i.test(item.path));$('entrySelect').replaceChildren();
  for(const item of html){const option=document.createElement('option');option.value=item.path;option.textContent=item.path;$('entrySelect').append(option);}
  const preferred=html.find(item=>/^(index|reference)\.html?$/i.test(item.path));if(preferred)$('entrySelect').value=preferred.path;
  $('importName').value=folder?prefix.slice(0,-1):(first?.name||'').replace(/\.html?$/i,'');
  const size=state.importing.reduce((n,item)=>n+item.file.size,0);
  $('importSummary').textContent=`${state.importing.length} 个文件 · ${(size/1024/1024).toFixed(1)} MB`+(html.length?'':' · 未找到 HTML')+(size>100*1024*1024?' · 超过 100 MB 上限，请移走视频等大文件后再导入':'');
  $('confirmImport').disabled=!html.length||size>100*1024*1024;
}
$('folderInput').onchange=event=>chooseFiles(event.target.files,true);$('htmlInput').onchange=event=>chooseFiles(event.target.files,false);
function releaseImport(){state.importing=[];$('folderInput').value='';$('htmlInput').value='';$('entrySelect').replaceChildren();$('importSummary').textContent='尚未选择文件';$('confirmImport').disabled=true;}
$('importDialog').addEventListener('close',releaseImport);
$('confirmImport').onclick=run(async()=>{
  if(!$('importName').value.trim())return toast('请填写稿件名称');
  $('confirmImport').disabled=true;
  let upload;
  try{
    ({upload}=await api('/api/import-start',{name:$('importName').value,entry:$('entrySelect').value}));
    for(const [index,item] of state.importing.entries()){
      $('importSummary').textContent=`正在导入 ${index+1} / ${state.importing.length}`;
      const response=await fetch('/api/import-file?upload='+encodeURIComponent(upload)+'&path='+encodeURIComponent(item.path),{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Deckroom-Token':window.DECKROOM_TOKEN},body:item.file});
      if(!response.ok)throw Error((await response.json()).error||'导入未完成');
    }
    const deck=await api('/api/import-finish',{upload});upload=null;
    $('importDialog').close();await refreshDecks();await openDeck(deck);toast('已导入到本地工作间');
  }finally{if(upload)await api('/api/import-cancel',{upload}).catch(()=>{});$('confirmImport').disabled=!state.importing.length;}
});
$('stopButton').onclick=run(async()=>{commitText();if(state.dirty&&!confirm('还有未保存修改。退出服务会放弃这些修改，是否继续？'))return;await api('/api/shutdown',{});state.dirty=false;toast('本地服务已退出，可以关闭窗口。');});
presentation=new DeckroomPresentation({read:presentationState,navigate,goTo,exit:leavePresentation,restart:async()=>{await resetPresentation();presentation.start(true);},saveNotes:saveSpeakerNotes,onConnection:connected=>{document.body.dataset.presenterConnected=String(connected);$('notesButton').textContent=connected?'演讲者已连接 ↗':'演讲者窗口 ↗';$('audiencePresenter').textContent=connected?'演讲者已连接 ↗':'演讲者窗口 ↗';},onTick:timer=>{const seconds=Math.floor(timer.elapsedMs/1000);$('timerButton').textContent=Math.floor(seconds/60).toString().padStart(2,'0')+':'+(seconds%60).toString().padStart(2,'0');$('timerButton').setAttribute('aria-pressed',String(timer.running));}});
fullscreen=new JemuFullscreen({document,api,key:new URLSearchParams(location.search).get('window'),onExit:()=>{if(state.presenting)run(leavePresentation)();}});
state.nativeReady=fullscreen.attach();state.nativeReady.catch(error=>toast(error.message));
run(async()=>{await refreshDecks();const requested=new URLSearchParams(location.search).get('deck');const readable=state.decks.filter(deck=>!deck.error);if(readable.length)await openDeck(readable.find(deck=>deck.id===requested)||readable[0]);else{$('surface').hidden=true;$('emptyState').hidden=false;}})();
