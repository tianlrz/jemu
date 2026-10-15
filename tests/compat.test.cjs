const assert=require('node:assert/strict');
const compat=require('../web/compat.js');
function fixture({marked=false,legacy=false,native=false,nested=false}={}){
  const parent={},pages=[{tagName:'SECTION',parentElement:parent},{tagName:'SECTION',parentElement:nested?{}:parent}];
  const stage=legacy?{goTo(){},children:pages}:null;
  return {pages,document:{documentElement:{dataset:native?{jemuMode:'native'}:{}},querySelector(selector){return selector==='deck-stage'?stage:selector==='[data-jemu-deck]'&&marked?parent:null;},querySelectorAll(selector){if(selector==='[data-jemu-deck] > section')return marked?pages:[];if(selector==='.slide,[data-slide-id]')return pages;return [];}}};
}
assert.equal(compat.detect(fixture().document).kind,'native','A class named slide alone cannot opt into takeover');
assert.equal(compat.detect(fixture({marked:true}).document).kind,'pure');
assert.equal(compat.detect(fixture({legacy:true}).document).kind,'legacy');
assert.equal(compat.detect(fixture({legacy:true}).document,'native').kind,'native');
assert.equal(compat.detect(fixture({marked:true,native:true}).document).kind,'native');
assert.equal(compat.detect(fixture().document,'slides').kind,'pure');
assert.equal(compat.detect(fixture({nested:true}).document,'slides').kind,'native','Do not reparent incompatible nested slide structures');
assert.equal(compat.documentEntry('http://127.0.0.1:8831/documents/deck-a/sub/%E6%8A%A5%E5%91%8A%231.html','deck-a','http://127.0.0.1:8831'),'sub/报告#1.html');
assert.equal(compat.documentEntry('https://example.com/index.html','deck-a','http://127.0.0.1:8831'),null);
assert.equal(compat.documentEntry('http://127.0.0.1:8831/documents/deck-b/index.html','deck-a','http://127.0.0.1:8831'),null);
console.log('PASS: conservative auto detection, manual override, nested fallback and local page identity.');
