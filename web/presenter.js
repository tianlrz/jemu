'use strict';
const $=id=>document.getElementById(id),session=new URLSearchParams(location.search).get('session');
let current=null,lastReceived=0,noteDraft=null,fontSize=24,messageTimer;
function command(action,extra={}){window.opener?.postMessage({kind:'deckroom-presenter-command',session,action,...extra},location.origin);}
function message(text){$('message').textContent=text;$('message').hidden=false;clearTimeout(messageTimer);messageTimer=setTimeout(()=>$('message').hidden=true,5000);}
function format(ms){const seconds=Math.floor(ms/1000);return Math.floor(seconds/60).toString().padStart(2,'0')+':'+(seconds%60).toString().padStart(2,'0');}
function render(data){
  lastReceived=Date.now();const changed=!current||current.index!==data.index||current.notes!==data.notes;current=data;
  $('connection').textContent='● 已连接展示窗口';$('connection').classList.remove('offline');
  const pages=data.paged!==false;
  $('deckTitle').textContent=data.deckName;$('currentPage').textContent=pages?data.index+1:'网页';$('totalPages').textContent=pages?data.total:'—';
  $('totalPages').parentElement.hidden=!pages;$('nextTitle').parentElement.hidden=!pages;
  $('notesHeading').textContent=pages?'本页讲稿':'作品讲稿';$('restart').textContent=pages?'从头演示 ↺':'重新载入作品 ↺';
  document.querySelector('footer>span').textContent=pages?'← → 翻页 · Esc 结束 · Space 暂停／继续计时':'在作品中操作 · Esc 结束 · Space 暂停／继续计时';
  $('previous').hidden=$('next').hidden=!pages;$('editNotes').disabled=data.editable===false;
  $('slideTitle').textContent=data.title||'第 '+(data.index+1)+' 页';$('nextTitle').textContent=data.nextTitle||'已是最后一页';
  if(changed){$('notes').textContent=data.notes||'这一页还没有讲稿。点击「编辑讲稿」即可填写。';$('notes').classList.toggle('empty',!data.notes);}
  $('previous').disabled=data.index<=0;$('next').disabled=data.index>=data.total-1;
  renderTimer(data.timer);
}
function renderTimer(timer){
  const elapsed=timer.elapsedMs,target=(Number($('targetMinutes').value)||8)*60000;
  $('timer').textContent=format(elapsed);$('timer').classList.toggle('overtime',elapsed>target);
  $('timerToggle').textContent=timer.running?'暂停计时':'继续计时';$('progress').style.width=Math.min(100,elapsed/target*100)+'%';
  $('timerHint').textContent=elapsed>target?'已超过计划时长':'切换窗口后继续计时。';
}
window.addEventListener('message',event=>{
  const data=event.data;if(event.origin!==location.origin||event.source!==window.opener||data?.session!==session)return;
  if(data.kind==='deckroom-presenter-state')render(data);
  if(data.kind==='deckroom-presenter-tick'){lastReceived=Date.now();renderTimer(data.timer);}
  if(data.kind==='deckroom-presenter-error'){$('saveNotes').disabled=false;message(data.message);}
  if(data.kind==='deckroom-notes-saved'){closeEditor();message('本页讲稿已保存到稿件，并留下版本记录');}
});
$('previous').onclick=()=>command('previous');$('next').onclick=()=>command('next');$('restart').onclick=()=>command('restart');
$('timerToggle').onclick=()=>command('timer-toggle');$('timerReset').onclick=()=>command('timer-reset');
$('endPresentation').onclick=()=>command('exit');
$('fontSmaller').onclick=()=>{$('notes').style.fontSize=(fontSize=Math.max(16,fontSize-2))+'px';};
$('fontLarger').onclick=()=>{$('notes').style.fontSize=(fontSize=Math.min(48,fontSize+2))+'px';};
function closeEditor(){noteDraft=null;$('notesEditing').hidden=true;$('notes').hidden=false;$('editNotes').hidden=false;$('saveNotes').disabled=false;}
$('editNotes').onclick=()=>{if(!current)return;noteDraft={index:current.index};$('notesEditor').value=current.notes||'';$('draftPage').textContent='正在编辑第 '+(current.index+1)+' 页讲稿';$('notes').hidden=true;$('notesEditing').hidden=false;$('editNotes').hidden=true;$('notesEditor').focus();};
$('cancelNotes').onclick=closeEditor;
$('saveNotes').onclick=()=>{if(!noteDraft)return;$('saveNotes').disabled=true;command('save-notes',{index:noteDraft.index,text:$('notesEditor').value});};
window.addEventListener('beforeunload',event=>{if(noteDraft&&$('notesEditor').value!==(current?.index===noteDraft.index?current.notes:'')){event.preventDefault();event.returnValue='';}});
document.addEventListener('keydown',event=>{
  if(['INPUT','TEXTAREA'].includes(event.target.tagName))return;
  if(event.key==='Escape'){event.preventDefault();command('exit');return;}
  if(event.key==='ArrowRight'||event.key==='PageDown'){event.preventDefault();command('next');}
  else if(event.key==='ArrowLeft'||event.key==='PageUp'){event.preventDefault();command('previous');}
  else if(event.key==='Home'){event.preventDefault();command('goto',{index:0});}
  else if(event.key==='End'){event.preventDefault();command('goto',{index:(current?.total||1)-1});}
  else if(event.key.toLowerCase()==='r'){event.preventDefault();command('restart');}
  else if(event.key===' '){event.preventDefault();command('timer-toggle');}
});
const connectionInterval=setInterval(()=>{if(Date.now()-lastReceived>5000){$('connection').textContent='展示窗口未连接，请从工作间重新打开';$('connection').classList.add('offline');}},2000);
window.addEventListener('pagehide',()=>{clearInterval(connectionInterval);clearTimeout(messageTimer);current=null;noteDraft=null;});
command('ready');
