'use strict';
// Native fullscreen controls only a launcher-registered Jemu window.
class JemuFullscreen {
  constructor({document,api,key,onExit}){
    this.document=document;this.api=api;this.key=key;this.onExit=onExit;this.native=false;this.active=false;
    this.changed=()=>{if(!this.native&&this.active&&!document.fullscreenElement){this.active=false;onExit?.();}};
    document.addEventListener('fullscreenchange',this.changed);
  }
  async attach(){
    if(!this.key)return false;
    for(let attempt=0;attempt<20;attempt++){
      const result=await this.api('/api/window/attach',{window:this.key});
      if(result.attached){this.native=true;if(result.fullscreen)await this.api('/api/window/fullscreen',{window:this.key,enabled:false});this.active=false;this.document.title='Jemu · 简幕';return true;}
      await new Promise(resolve=>setTimeout(resolve,150));
    }
    throw Error('桌面窗口尚未就绪，请重新打开桌面窗口');
  }
  enter(){
    if(this.active)return Promise.resolve();
    // Invoke the browser API in the click stack, before saving or opening a popup.
    if(this.native)return this.api('/api/window/fullscreen',{window:this.key,enabled:true}).then(()=>{this.active=true;});
    if(!this.document.fullscreenEnabled||!this.document.documentElement.requestFullscreen)return Promise.reject(Error('当前内嵌窗口不支持系统全屏，请用桌面窗口打开'));
    return this.document.documentElement.requestFullscreen({navigationUI:'hide'}).then(()=>{this.active=true;},()=>{throw Error('浏览器未允许系统全屏，请使用左下角「桌面窗口」');});
  }
  async exit(){
    const wasNative=this.native;this.active=false;
    if(wasNative)await this.api('/api/window/fullscreen',{window:this.key,enabled:false});
    else if(this.document.fullscreenElement)await this.document.exitFullscreen();
  }
  dispose(){this.document.removeEventListener('fullscreenchange',this.changed);}
}
if(typeof module!=='undefined')module.exports=JemuFullscreen;
