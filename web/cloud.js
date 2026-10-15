'use strict';
// Explicit, on-demand connections. Passwords are never returned by the service.
let cloudConfig=null,cloudOffset=0,cloudWorking=false;
const cloudDAV=()=>$('cloudProvider').value==='nutstore';
const cloudReady=()=>!!cloudConfig?.configured;
function cloudFields(){
  const dav=cloudDAV();
  const quark=$('cloudProvider').value==='quark';
  $('cloudFolderLabel').textContent=quark?'本机备份文件夹':'同步文件夹';
  $('cloudFolder').placeholder=quark?'填写已在夸克客户端设为备份的本机目录':'粘贴云盘中已有文件夹的完整路径';
  $('cloudFolderHelp').textContent=quark?'先在夸克客户端为这个本机文件夹开启备份，再填写路径。简幕写入存档，上传由夸克负责。换电脑恢复时，先下载完整的 Jemu Archives 文件夹，再关联下载后的目录。':'先在云盘客户端设置同步，再填入这台电脑能访问的目录。上传由云盘客户端完成。';
  $('cloudFolderFields').hidden=dav;$('cloudDAVFields').hidden=!dav;
  $('cloudOpenFolder').hidden=dav;$('cloudTest').hidden=!dav;$('cloudMonthField').hidden=!dav;
  $('cloudSaveConfig').textContent=dav?'保存设置':'保存位置';cloudControls();
}
function cloudControls(){
  $('cloudBackup').disabled=cloudWorking||!cloudReady()||!state.deck;
  for(const id of ['cloudSaveConfig','cloudOpenFolder','cloudRefresh','cloudDisconnect','cloudTest','cloudNewer','cloudOlder','cloudProvider','cloudUsername','cloudPassword','cloudFolder','cloudMonth','backupDeck','reloadSettings'])$(id).disabled=cloudWorking;
}
function requireCloud(){
  if(!cloudReady())throw Error('请先保存存档设置');
  if($('cloudProvider').value!==cloudConfig.provider || (cloudDAV()?$('cloudUsername').value.trim()!==cloudConfig.username||!!$('cloudPassword').value:$('cloudFolder').value.trim()!==cloudConfig.folder))throw Error('设置有修改，请先保存');
}
function showCloudConfig(){
  $('cloudProvider').value=cloudConfig.provider;$('cloudFolder').value=cloudConfig.folder||'';
  $('cloudUsername').value=cloudConfig.username||'';$('cloudPassword').value='';
  $('cloudPassword').placeholder=cloudConfig.passwordStored?'已保存，留空保留原应用密码':'第三方应用密码，非登录密码';
  if(!$('cloudMonth').value)$('cloudMonth').value=new Date().toISOString().slice(0,7);
  cloudFields();
}
async function loadCloud(){
  if(cloudWorking)return;
  cloudConfig=await api('/api/settings/backup');showCloudConfig();cloudOffset=0;
  $('cloudNewer').hidden=true;$('cloudOlder').hidden=true;
  if(cloudConfig.mode==='sync-folder'&&cloudReady()){
    $('cloudStatus').textContent='已关联本机存档目录。云端上传状态请在云盘客户端查看。';await refreshCloud();
  }else{
    $('cloudStatus').textContent=cloudReady()?'本机凭据已保存。点击验证连接、刷新或备份时才访问云端。':'填写账号和应用密码后保存设置，或选择同步文件夹。';
    $('cloudArchives').textContent='保存设置后，点击刷新查看存档。';
  }
}
async function refreshCloud(){
  requireCloud();
  const result=await api('/api/cloud/archives?offset='+cloudOffset+(cloudDAV()?'&month='+encodeURIComponent($('cloudMonth').value):''));
  $('cloudArchives').replaceChildren();
  if(!result.items.length)$('cloudArchives').textContent=cloudDAV()?'这个月份还没有存档。':'这个位置还没有存档。';
  for(const item of result.items){
    const row=document.createElement('div');row.className='cloud-archive';
    const title=document.createElement('b');title.textContent=item.name;
    const detail=document.createElement('small');detail.textContent=item.invalid?'存档未下载完整或格式无效':new Date(item.created).toLocaleString('zh-CN',{hour12:false})+' · '+item.versionCount+' 个版本 · '+(item.bytes/1048576).toFixed(1)+' MB';
    const restore=document.createElement('button');restore.textContent='恢复为新稿件';restore.disabled=!!item.invalid;
    restore.onclick=run(()=>cloudTask(async()=>{
      requireCloud();
      const meta=await api('/api/cloud/restore',{archive:item.id,expectedSettingsRevision:cloudConfig.settingsRevision});
      await refreshDecks(meta.id);
      $('cloudStatus').textContent='已校验并恢复为新稿件，原稿和原有版本均保留。';toast('存档已恢复为副本');
    }));
    row.append(title,detail,restore);$('cloudArchives').append(row);
  }
  $('cloudNewer').hidden=cloudOffset===0;$('cloudOlder').hidden=!result.hasMore;
}
async function cloudTask(operation){
  if(cloudWorking||state.busy)return toast('当前操作尚未完成，请稍后重试');
  cloudWorking=true;cloudControls();
  $('cloudStatus').textContent='正在处理存档，请稍候…';
  try{await operation();}
  catch(error){$('cloudStatus').textContent=error.message;throw error;}
  finally{cloudWorking=false;cloudControls();}
}
$('cloudProvider').onchange=()=>{cloudFields();$('cloudStatus').textContent='选择后请保存设置；尚未连接新位置。';$('cloudArchives').textContent='保存设置后，点击刷新查看。';$('cloudNewer').hidden=true;$('cloudOlder').hidden=true;};
$('cloudSaveConfig').onclick=run(()=>cloudTask(async()=>{
  const data=cloudDAV()?{provider:'nutstore',username:$('cloudUsername').value,password:$('cloudPassword').value}:{provider:$('cloudProvider').value,folder:$('cloudFolder').value};
  data.expectedSettingsRevision=cloudConfig?.settingsRevision;
  try{cloudConfig=await api('/api/settings/backup',data);}finally{$('cloudPassword').value='';}
  showCloudConfig();cloudOffset=0;$('cloudArchives').textContent='点击刷新查看存档。';
  $('cloudNewer').hidden=true;$('cloudOlder').hidden=true;
  $('cloudStatus').textContent=cloudDAV()?'凭据已加密保存在本机，尚未验证连接。点击备份才上传完整存档。':$('cloudProvider').value==='quark'?'位置已保存。请确认夸克客户端已为这个目录开启备份。':'位置已保存。点击备份才写入完整存档。';
}));
$('cloudTest').onclick=run(()=>cloudTask(async()=>{requireCloud();const result=await api('/api/cloud/test',{expectedSettingsRevision:cloudConfig.settingsRevision});$('cloudStatus').textContent=result.message;}));
$('cloudDisconnect').onclick=run(()=>cloudTask(async()=>{
  cloudConfig=await api('/api/cloud/disconnect',{expectedSettingsRevision:cloudConfig?.settingsRevision});showCloudConfig();
  $('cloudArchives').textContent='已有存档仍保留在原位置。';$('cloudStatus').textContent='已断开关联并清除本机应用密码。';$('cloudNewer').hidden=true;$('cloudOlder').hidden=true;
}));
$('cloudBackup').onclick=run(()=>cloudTask(async()=>{
  requireCloud();if(!state.deck)return;
  await prepareCloudBackup();
  const id=state.deck.id;
  if(state.deck.id!==id)throw Error('稿件已切换，请重新点击备份');
  const result=await api('/api/decks/'+id+'/cloud-backup',{baseRevision:state.revision,expectedSettingsRevision:cloudConfig.settingsRevision});
  cloudOffset=0;
  if(result.month)$('cloudMonth').value=result.month;
  try{await refreshCloud();}catch(error){$('cloudArchives').textContent='备份已完成，列表刷新失败：'+error.message;}
  $('cloudStatus').textContent=result.status==='uploaded'?'已上传，云端服务器已接收 '+result.versionCount+' 个版本。恢复时会校验文件完整性。':($('cloudProvider').value==='quark'?'已写入夸克备份目录':'已写入同步目录')+'，包含 '+result.versionCount+' 个版本。\n云端上传状态请在云盘客户端查看。';
  toast(result.status==='uploaded'?'存档已上传':'存档已写入本机目录');
}));
$('cloudOpenFolder').onclick=run(()=>{requireCloud();return api('/api/cloud/open-folder',{});});
$('cloudRefresh').onclick=run(()=>cloudTask(async()=>{cloudOffset=0;await refreshCloud();$('cloudStatus').textContent=cloudDAV()?'已读取云端存档索引。':'已刷新本机可见的存档。云端状态请在云盘客户端查看。';}));
$('cloudMonth').onchange=()=>{cloudOffset=0;$('cloudArchives').textContent='点击刷新查看这个月份。';$('cloudNewer').hidden=true;$('cloudOlder').hidden=true;};
$('cloudOlder').onclick=run(()=>cloudTask(async()=>{const old=cloudOffset;cloudOffset+=50;try{await refreshCloud();}catch(e){cloudOffset=old;throw e;}$('cloudStatus').textContent='已读取较早的存档。';}));
$('cloudNewer').onclick=run(()=>cloudTask(async()=>{const old=cloudOffset;cloudOffset=Math.max(0,cloudOffset-50);try{await refreshCloud();}catch(e){cloudOffset=old;throw e;}$('cloudStatus').textContent='已读取较新的存档。';}));
