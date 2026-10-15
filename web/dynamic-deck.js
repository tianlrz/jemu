'use strict';
// A narrow adapter for single-page React decks with an explicit "show all" view.
// It materializes existing pages; never evaluates source strings or guesses app internals.
const JemuDynamicDeck=(()=>{
  const MAX_PAGES=200,MAX_HTML=10*1024*1024;
  function toolbar(document){return document.querySelector('.deck-toolbar');}
  function expandButton(document){return [...(toolbar(document)?.querySelectorAll('button')||[])].find(button=>/展开全部\s*\d*\s*页|show all\s*\d*\s*(slides|pages)/i.test(button.textContent));}
  function rows(document){return [...document.querySelectorAll('.deck-list-viewport > .slide-wrapper')].filter(row=>row.querySelector('.slide-canvas'));}
  function expected(document){const text=toolbar(document)?.textContent||'';return Number(text.match(/展开全部\s*(\d+)\s*页|show all\s*(\d+)\s*(?:slides|pages)/i)?.slice(1).find(Boolean)||text.match(/\d+\s*\/\s*(\d+)/)?.[1])||null;}
  function pageIndex(document){return Math.max(0,Number((toolbar(document)?.textContent||'').match(/(\d+)\s*\/\s*\d+/)?.[1]||1)-1);}
  function matches(document){
    if(!document||document.querySelector('[data-jemu-normalized]'))return false;
    return !!(toolbar(document)&&document.querySelector('.slide-canvas')&&(expandButton(document)||rows(document).length>1));
  }
  function hinted(document){
    if(!document||document.querySelector('[data-jemu-deck]'))return false;
    if(matches(document))return true;
    return [...document.querySelectorAll('script[type="text/babel"],script:not([src])')].some(script=>script.textContent.length<MAX_HTML&&script.textContent.includes('deck-list-viewport')&&script.textContent.includes('slide-canvas')&&script.textContent.includes('SLIDES'));
  }
  function waitFor(document,predicate,signal,timeout=12000){
    return new Promise((resolve,reject)=>{
      let observer,timer;
      const cleanup=()=>{observer?.disconnect();clearTimeout(timer);signal?.removeEventListener('abort',abort);};
      const abort=()=>{cleanup();reject(new DOMException('已取消适配','AbortError'));};
      const check=()=>{try{const value=predicate();if(value){cleanup();resolve(value);return true;}}catch(error){cleanup();reject(error);return true;}return false;};
      if(signal?.aborted)return abort();if(check())return;
      observer=new document.defaultView.MutationObserver(check);observer.observe(document.documentElement,{childList:true,subtree:true,characterData:true});
      timer=setTimeout(()=>{cleanup();reject(Error('未能展开完整稿件。请保留原稿并检查是否可展开全部页面。'));},timeout);
      signal?.addEventListener('abort',abort,{once:true});
    });
  }
  async function savedPage(document,url,signal,historyURL){
    const response=await fetch(url,{signal});
    if(!response.ok)throw Error('无法读取原稿以保留已保存的修改');
    if(Number(response.headers.get('Content-Length'))>MAX_HTML)throw Error('动态稿件超过 10 MiB 适配上限');
    const text=await response.text();if(new TextEncoder().encode(text).length>MAX_HTML)throw Error('动态稿件过大');
    const saved=new DOMParser().parseFromString(text,'text/html');
    // GUI edits carry node IDs. A fresh React source has an empty #root and needs no overlay.
    const canvas=saved.querySelector('#root .deck-viewport .slide-canvas');
    const edited=canvas&&(canvas.hasAttribute('data-node-id')||canvas.querySelector('[data-node-id]'));
    const pages=new Map();
    if(historyURL){
      const response=await fetch(historyURL,{signal});
      if(!response.ok)throw Error('无法检查旧格式的已保存页面，请稍后重试');
      const history=await response.json();
      for(const page of history.pages||[]){
        if(!Number.isInteger(page.index)||page.index<0||page.index>=MAX_PAGES||typeof page.html!=='string')continue;
        const node=new DOMParser().parseFromString(page.html,'text/html').querySelector('.slide-canvas');if(node)pages.set(page.index,node);
      }
    }
    if(edited)pages.set(pageIndex(saved),canvas);
    return {canvas:edited?canvas:null,index:pageIndex(saved),pages,notes:saved.querySelector('#speaker-notes')};
  }
  function build(document,pages,{width,height,overrides,baseURL}){
    if(!pages.length||pages.length>MAX_PAGES)throw Error('动态稿件页数超出适配范围');
    const result=document.implementation.createHTMLDocument(document.title);
    result.documentElement.lang=document.documentElement.lang||'zh-CN';result.head.replaceChildren();
    for(const node of document.head.children){
      if(node.matches('base,script,link[as="script"],[data-deckroom-ui]'))continue;
      result.head.append(result.importNode(node,true));
    }
    if(!result.head.querySelector('meta[charset]')){const meta=result.createElement('meta');meta.charset='utf-8';result.head.prepend(meta);}
    const base=result.createElement('base');base.href=new URL('.',baseURL).href;base.dataset.deckroomUi='base';result.head.prepend(base);
    const host=result.createElement('main');host.dataset.jemuDeck='';host.dataset.jemuNormalized='react-list-v1';host.dataset.width=String(width);host.dataset.height=String(height);
    pages.forEach((row,index)=>{
      const original=overrides.pages?.get(index)||(index===overrides.index&&overrides.canvas?overrides.canvas:row.querySelector('.slide-canvas'));
      const canvas=result.importNode(original,true);canvas.style.removeProperty('transform');
      canvas.querySelectorAll('[data-deckroom-ui],script:not([type="application/json"])').forEach(node=>node.remove());
      canvas.querySelectorAll('[contenteditable],[data-deckroom-inline]').forEach(node=>{node.removeAttribute('contenteditable');node.removeAttribute('data-deckroom-inline');});
      canvas.querySelectorAll('video,audio').forEach(media=>{media.removeAttribute('autoplay');media.preload='metadata';});
      const section=result.createElement('section');section.className='slide-wrapper';section.dataset.slideId='page-'+String(index+1).padStart(3,'0');section.append(canvas);host.append(section);
    });
    result.body.replaceChildren(host);
    const notes=overrides.notes||document.querySelector('#speaker-notes');if(notes)result.body.append(result.importNode(notes,true));
    const style=result.createElement('style');style.dataset.jemuNormalizedStyle='';
    style.textContent=`html,body{margin:0;padding:0;background:#181420}main[data-jemu-normalized]{width:${width}px;margin:0 auto;padding:0;display:block;min-height:0;gap:0}main[data-jemu-normalized]>.slide-wrapper{position:relative;width:${width}px;height:${height}px;padding:0;margin:0;overflow:hidden;box-sizing:border-box}main[data-jemu-normalized]>.slide-wrapper>.slide-canvas{width:${width}px;height:${height}px;transform:none!important;box-shadow:none}@page{size:${width}px ${height}px;margin:0}@media print{html,body{width:${width}px!important;height:auto!important;overflow:visible!important;background:white!important}main[data-jemu-normalized]{height:auto!important;overflow:visible!important;position:static!important}main[data-jemu-normalized]>.slide-wrapper{break-after:page;break-inside:avoid;margin:0!important}main[data-jemu-normalized]>.slide-wrapper:last-child{break-after:auto}*{-webkit-print-color-adjust:exact;print-color-adjust:exact}}`;
    result.head.append(style);
    const html='<!doctype html>\n'+result.documentElement.outerHTML;
    if(new TextEncoder().encode(html).length>MAX_HTML)throw Error('展开后的稿件超过 10 MiB，已保留原网页');
    return html;
  }
  async function prepare(document,{url,signal,historyURL}){
    if(!hinted(document))return null;
    await waitFor(document,()=>matches(document),signal);
    const total=expected(document),index=pageIndex(document);
    if(total&&(total<2||total>MAX_PAGES))throw Error('动态稿件页数超出 2～200 页适配范围');
    const overrides=await savedPage(document,url,signal,historyURL);
    const computed=document.defaultView.getComputedStyle(document.querySelector('.slide-canvas'));
    const width=parseFloat(computed.width)||1920,height=parseFloat(computed.height)||1080;
    if(width<100||height<100||width>10000||height>10000)throw Error('稿件画布尺寸无效');
    let pages=rows(document);
    if(!pages.length){
      const button=expandButton(document);if(!button)throw Error('稿件没有明确的展开全部入口');
      const ready=waitFor(document,()=>{const list=rows(document);return list.length>1&&(!total||list.length===total)?list:null;},signal);
      button.click();pages=await ready;
    }
    if(total&&pages.length!==total)throw Error('展开页数与原稿声明不一致，已保留原网页');
    if(signal?.aborted)throw new DOMException('已取消适配','AbortError');
    document.querySelectorAll('video,audio').forEach(media=>media.pause());
    return {html:build(document,pages,{width,height,overrides,baseURL:url}),total:pages.length,index:Math.min(index,pages.length-1)};
  }
  return {matches,hinted,expected,prepare,build,limits:{pages:MAX_PAGES,html:MAX_HTML}};
})();
if(typeof module!=='undefined')module.exports=JemuDynamicDeck;
