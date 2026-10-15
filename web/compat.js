'use strict';
const JemuCompat=(()=>{
  function pageList(document,markedOnly=false){
    const selectors=markedOnly?['[data-jemu-deck] > .slide,[data-jemu-deck] > [data-slide-id]','[data-jemu-deck] > section']:['[data-jemu-deck] > .slide,[data-jemu-deck] > [data-slide-id]','[data-jemu-deck] > section','.slide,[data-slide-id]','.slides > section','main > section','body > section'];
    for(const selector of selectors){const pages=[...document.querySelectorAll(selector)];if(pages.length&&pages.every(page=>page.parentElement===pages[0].parentElement))return pages;}
    return [];
  }
  function detect(document,mode='auto'){
    if(!document||mode==='native')return {kind:'native',pages:[]};
    const preference=document.querySelector('meta[name="jemu:mode"]')?.content;
    if(mode==='auto'&&(preference==='native'||document.documentElement.dataset.jemuMode==='native'))return {kind:'native',pages:[]};
    const stage=document.querySelector('deck-stage');
    if(typeof stage?.goTo==='function')return {kind:'legacy',stage,pages:[...stage.children].filter(el=>el.tagName==='SECTION')};
    const host=document.querySelector('[data-jemu-deck]');
    if(mode==='slides'||host){const pages=pageList(document,mode!=='slides');if(pages.length)return {kind:'pure',pages};}
    return {kind:'native',pages:[]};
  }
  function documentEntry(url,deckId,origin){
    try{const parsed=new URL(url,origin),prefix='/documents/'+deckId+'/';if(parsed.origin!==origin||!parsed.pathname.startsWith(prefix))return null;const entry=decodeURIComponent(parsed.pathname.slice(prefix.length));return /\.html?$/i.test(entry)?entry:null;}catch{return null;}
  }
  return {detect,pageList,documentEntry};
})();
if(typeof module!=='undefined')module.exports=JemuCompat;
