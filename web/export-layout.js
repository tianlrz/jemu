'use strict';
// A static document for export only. Never change the live preview or its page.
const JemuExportLayout=(()=>{
  function build(document,{pages,width,height}){
    if(!pages?.length||pages.length>200)throw Error('整稿导出需要明确的 1～200 页幻灯片');
    const result=document.implementation.createHTMLDocument(document.title);result.head.replaceChildren();
    result.documentElement.lang=document.documentElement.lang||'zh-CN';
    for(const node of document.head.children){
      if(node.matches('base,script,[data-deckroom-ui],link[as="script"],link[rel="modulepreload"]'))continue;
      result.head.append(result.importNode(node,true));
    }
    const meta=result.createElement('meta');meta.charset='utf-8';result.head.prepend(meta);
    const parent=pages[0].parentElement;
    const host=parent&&parent!==document.body?result.importNode(parent,false):result.createElement('main');
    host.removeAttribute('data-deckroom-ui');host.dataset.jemuExport='';
    for(const page of pages){
      const clone=result.importNode(page,true);clone.setAttribute('data-jemu-export-page','');
      for(const node of [clone,...clone.querySelectorAll('*')]){
        if(node.matches('script,iframe,object,embed,[data-deckroom-ui]')){node.remove();continue;}
        for(const attr of [...node.attributes])if(attr.name.startsWith('on')||['data-jemu-page','data-deck-active','data-deckroom-inline','contenteditable','data-jemu-suppressed','slot'].includes(attr.name))node.removeAttribute(attr.name);
      }
      host.append(clone);
    }
    result.body.replaceChildren(host);
    const style=result.createElement('style');style.dataset.jemuExportLayout='';
    style.textContent=`html,body{margin:0!important;padding:0!important;width:${width}px!important;height:auto!important;overflow:visible!important;background:white!important}[data-jemu-export]{display:block!important;position:static!important;width:${width}px!important;height:auto!important;margin:0!important;padding:0!important;gap:0!important}[data-jemu-export-page]{display:block!important;visibility:visible!important;position:relative!important;inset:auto!important;width:${width}px!important;height:${height}px!important;min-height:${height}px!important;max-height:${height}px!important;margin:0!important;overflow:hidden!important;box-sizing:border-box!important;break-after:page;break-inside:avoid;box-shadow:none!important;transform:none!important;zoom:1!important}[data-jemu-export-page]:last-child{break-after:auto}[data-jemu-export-page]>.slide-canvas{transform:none!important;box-shadow:none!important}*{animation:none!important;transition:none!important;-webkit-print-color-adjust:exact!important;print-color-adjust:exact!important}@page{size:${width}px ${height}px;margin:0}`;
    result.head.append(style);
    result.querySelectorAll('*').forEach(node=>{for(const attr of [...node.attributes])if(attr.name.startsWith('on'))node.removeAttribute(attr.name);});
    const html='<!doctype html>\n'+result.documentElement.outerHTML;
    if(new TextEncoder().encode(html).length>10*1024*1024)throw Error('导出 HTML 超过 10 MiB');
    return html;
  }
  return {build};
})();
