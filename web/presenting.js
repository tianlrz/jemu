'use strict';
// Keep each workspace's presenter isolated from template runtimes and other decks.
class DeckroomPresentation {
  constructor(options) {
    this.options=options;this.session=crypto.randomUUID();this.popup=null;this.connected=false;
    this.elapsed=0;this.started=0;this.running=false;
    window.addEventListener('message',event=>{
      const data=event.data;
      if(event.origin!==location.origin||event.source!==this.popup||data?.session!==this.session||data?.kind!=='deckroom-presenter-command')return;
      this.command(data).catch(error=>this.send({kind:'deckroom-presenter-error',message:error.message}));
    });
    this.tick=()=>{
      if(this.popup?.closed){this.popup=null;this.connected=false;options.onConnection?.(false);}
      if(!document.hidden||this.popup){options.onTick?.(this.timer());if(this.connected)this.send({kind:'deckroom-presenter-tick',timer:this.timer()});}
      this.schedule();
    };
    this.visibility=()=>this.schedule();document.addEventListener('visibilitychange',this.visibility);
    window.addEventListener('pagehide',()=>this.dispose());
  }
  timer(){return {elapsedMs:this.elapsed+(this.running?Date.now()-this.started:0),running:this.running};}
  schedule(){clearTimeout(this.interval);if((this.running&&!document.hidden)||this.popup)this.interval=setTimeout(this.tick,1000);}
  dispose(){clearTimeout(this.interval);document.removeEventListener('visibilitychange',this.visibility);this.popup?.close();this.popup=null;this.connected=false;}
  start(reset=false){if(reset)this.elapsed=0;if(reset||!this.running)this.started=Date.now();this.running=true;this.sync();}
  pause(){if(this.running){this.elapsed=this.timer().elapsedMs;this.running=false;}this.sync();}
  toggle(){if(this.running)this.pause();else this.start();this.sync();}
  resetTimer(){this.elapsed=0;this.started=Date.now();this.sync();}
  open({focus=true}={}){
    if(this.popup&&!this.popup.closed){if(focus)this.popup.focus();this.sync();return this.popup;}
    this.popup=window.open('/presenter.html?session='+encodeURIComponent(this.session),'deckroom-presenter-'+this.session,'popup,width=1040,height=850');
    this.connected=false;this.options.onConnection?.(false);
    if(!this.popup)throw Error('演讲者窗口未打开。请允许此本地页面弹出窗口，再点击「演讲者窗口」。');
    this.schedule();return this.popup;
  }
  send(message){if(this.popup&&!this.popup.closed)this.popup.postMessage({...message,session:this.session},location.origin);}
  sync(){this.options.onTick?.(this.timer());this.schedule();if(!this.popup||this.popup.closed)return;const data=this.options.read();if(data)this.send({kind:'deckroom-presenter-state',...data,timer:this.timer()});}
  async command(data){
    const total=this.options.read()?.total||1;
    switch(data.action){
      case 'ready':this.connected=true;this.options.onConnection?.(true);break;
      case 'previous':this.options.navigate(-1);break;
      case 'next':this.options.navigate(1);break;
      case 'goto':if(Number.isInteger(data.index))this.options.goTo(Math.min(total-1,Math.max(0,data.index)));break;
      case 'restart':if(this.options.restart)await this.options.restart();else{this.options.goTo(0);this.start(true);}break;
      case 'timer-toggle':this.toggle();break;
      case 'timer-reset':this.resetTimer();break;
      case 'exit':await this.options.exit?.();break;
      case 'save-notes':
        if(!Number.isInteger(data.index)||data.index<0||data.index>=total||typeof data.text!=='string'||data.text.length>50000)throw Error('讲稿内容或页码无效');
        await this.options.saveNotes(data.index,data.text);
        this.send({kind:'deckroom-notes-saved',index:data.index});break;
    }
    this.sync();
  }
}
