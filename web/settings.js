'use strict';
const $=id=>document.getElementById(id);
const state={deck:null,decks:[],busy:false,revision:null};let toastTimer,settingsTimer,settingsDisposed=false,settingsChecking=false;
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,4500);}
function run(fn){return (...args)=>Promise.resolve().then(()=>fn(...args)).catch(error=>toast(error.message));}
async function api(path,body){
  const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Deckroom-Token':window.DECKROOM_TOKEN},body:JSON.stringify(body)});
  const value=await response.json();if(!response.ok)throw Error(value.error||'操作未完成');return value;
}
async function openDeck(meta){state.deck=meta;state.revision=meta.revision;$('backupDeck').value=meta.id;cloudControls();}
async function refreshDecks(preferred){
  const selected=preferred||state.deck?.id||new URLSearchParams(location.search).get('deck');
  state.decks=await api('/api/decks');$('backupDeck').replaceChildren();
  if(!state.decks.length){const option=new Option('还没有作品','');$('backupDeck').append(option);state.deck=null;cloudControls();return;}
  for(const meta of state.decks)$('backupDeck').append(new Option(meta.name,meta.id));
  await openDeck(state.decks.find(meta=>meta.id===selected)||state.decks[0]);
}
async function prepareCloudBackup(){
  if(!state.deck)throw Error('请先选择作品');
  const windows=await api('/api/ui/state');
  if(windows.clients.some(client=>client.deck===state.deck.id&&client.dirty))throw Error('所选作品在编辑窗口有未保存修改，请先返回作品保存');
  await openDeck(await api('/api/decks/'+state.deck.id));
}
function settingsChanged(){
  return !!cloudConfig&&($('cloudProvider').value!==cloudConfig.provider||(cloudDAV()?$('cloudUsername').value.trim()!==cloudConfig.username||!!$('cloudPassword').value:$('cloudFolder').value.trim()!==(cloudConfig.folder||'')));
}
async function checkSettings(){
  clearTimeout(settingsTimer);
  if(settingsDisposed||document.hidden||settingsChecking)return;
  settingsChecking=true;
  try{
    if(cloudConfig&&!cloudWorking){
      const current=await api('/api/settings/revision');
      if(current.revision!==cloudConfig.settingsRevision){
        if(!settingsChanged()){await loadCloud();$('reloadSettings').hidden=true;}
        else{$('cloudStatus').textContent='已保存的设置发生变化。当前输入已保留，请重新载入设置后再操作。';$('reloadSettings').hidden=false;}
      }
    }
  }catch{/* A service restart must not erase form inputs. */}
  finally{settingsChecking=false;if(!settingsDisposed&&!document.hidden)settingsTimer=setTimeout(checkSettings,5000);}
}
$('backupDeck').onchange=run(async()=>{if(cloudWorking)return;await openDeck(await api('/api/decks/'+$('backupDeck').value));});
$('reloadSettings').onclick=run(async()=>{await loadCloud();$('reloadSettings').hidden=true;});
$('backToWork').onclick=event=>{if(window.opener){event.preventDefault();window.opener.focus();window.close();}};
window.addEventListener('beforeunload',event=>{if(settingsChanged()){event.preventDefault();event.returnValue='';}});
window.addEventListener('pagehide',()=>{settingsDisposed=true;clearTimeout(settingsTimer);clearTimeout(toastTimer);});
document.addEventListener('visibilitychange',()=>{clearTimeout(settingsTimer);if(!document.hidden)checkSettings();});
window.addEventListener('focus',()=>checkSettings());
window.addEventListener('DOMContentLoaded',run(async()=>{await refreshDecks();await loadCloud();checkSettings();}));
