const assert=require('node:assert/strict');
const Fullscreen=require('../web/fullscreen.js');
(async()=>{
  let callback,calls=[],exits=0;
  const d={title:'Jemu',fullscreenEnabled:true,fullscreenElement:null,addEventListener(name,fn){callback=fn;},removeEventListener(){},documentElement:{requestFullscreen(options){calls.push(options);d.fullscreenElement={};return Promise.resolve();}},exitFullscreen(){d.fullscreenElement=null;callback();return Promise.resolve();}};
  const browser=new Fullscreen({document:d,api(){throw Error('not native');},onExit(){exits++;}});
  const entering=browser.enter();assert.equal(calls.length,1,'fullscreen is requested synchronously before async saves or popup calls');await entering;
  assert.equal(browser.active,true);await browser.exit();assert.equal(exits,0,'explicit exit must not trigger a second leave');
  await browser.enter();d.fullscreenElement=null;callback();assert.equal(exits,1,'browser Esc synchronizes the app state');assert.equal(browser.active,false);
  const disabled=new Fullscreen({document:{...d,fullscreenEnabled:false},api(){}});await assert.rejects(()=>disabled.enter(),/桌面窗口/);
  const nativeCalls=[];const native=new Fullscreen({document:d,key:'owned',api:async(path,data)=>{nativeCalls.push({path,data});return {attached:true};}});
  await native.attach();assert.equal(native.native,true);await native.enter();assert.equal(native.active,true);await native.exit();
  assert.deepEqual(nativeCalls.map(c=>c.data.enabled),[undefined,true,false]);
  assert.equal(calls.length,2,'native window fullscreen needs no browser activation and must not request a second browser fullscreen');
  browser.dispose();native.dispose();console.log('PASS: immediate browser fullscreen, Esc state, native enter/restore and unsupported-host fallback.');
})().catch(error=>{console.error(error);process.exitCode=1;});
