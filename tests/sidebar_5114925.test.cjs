'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {patchSidebar, patchPinned} = require('../scripts/sidebar_5114925.cjs');

const source = 'var Xv={collapsed:!1,sidebarWidth:Wv};'
  + 'function Qv(e){let r=e;return{collapsed:!!r.collapsed,sidebarWidth:0}}'
  + 'var X=Fi()(Ni((e,t)=>({collapsed:!1,sidebarWidth:0,'
  + 'merge:(e,t)=>{let n={};return{...t,...n}}})));function Bk(){}';

test('new sidebar starts closed, rejects saved expansion and keeps an explicit toggle', () => {
  const output = patchSidebar(source);
  assert.match(output, /var Xv=\{collapsed:!0,sidebarWidth:Wv\}/);
  assert.match(output, /return\{collapsed:!0,sidebarWidth:0\}/);
  assert.match(output, /X=Fi\(\)\(Ni\(\(e,t\)=>\(\{collapsed:!0,/);
  assert.match(output, /return\{\.\.\.t,\.\.\.n,collapsed:!0,composedSidebarOpen:!1,composedSidebarMounted:!1\}/);
  assert.ok(!source.includes('composedSidebarMounted'));
  assert.throws(() => patchSidebar(output));
  assert.throws(() => patchSidebar(source.replace('return{...t,...n}', 'return{...t}')));
  assert.throws(() => patchPinned(source, '0'.repeat(64)));
});

function native(parts, composed) {
  const box = {
    lk:composed,Xv:{collapsed:true,sidebarWidth:280},Kv:x=>x,
    Y:x=>x!==null&&typeof x==='object'&&!Array.isArray(x),WO:()=>null,UO:x=>x,
    Cee:()=>{},wE:x=>x,MO:[0,7,30],jO:x=>x,NO:()=>true,PO:()=>true,
    XT:x=>x,See:x=>x,yee:x=>x,QO:()=>true,ek:(x,k)=>x.codeSidebarByScope[k],
    ak:()=>false,ck:{hovering:false,peeked:false},
    fk:x=>x.isCuDocked||x.isNarrowViewport,
    pk:x=>x.experimentFrameActive||x.sidebarlessClaims>0,gk:()=>{},
  };
  for (const key of ['Qv','uk','Vk']) box[key]=vm.runInNewContext('('+parts[key]+')',box);
  const merge=vm.runInNewContext('('+parts.merge+')',box);
  let state={collapsed:true,composedSidebarOpen:false,composedSidebarMounted:false,
    sidebarWidth:280,navPinV:2,isRouteDrilldown:false,isCuDocked:false,
    isNarrowViewport:false,customGroupsByScope:{},codeSidebarByScope:{},
    pinnedOrder:[],folderAppearanceByKey:{}};
  box.e=fn=>{state={...state,...fn(state)}};
  const set=vm.runInNewContext('('+parts.setCollapsedPref+')',box);
  return {get:()=>JSON.parse(JSON.stringify(state)),
    hydrate:p=>{state=merge(structuredClone(p),state);return state},
    collapsed:()=>box.Vk(state),prepaint:raw=>box.Qv(raw),
    open:()=>set(false),close:()=>set(true)};
}

for (const composed of [false,true]) test(`signed 5.11492.5 sidebar lifecycle composed=${composed}`, () => {
  const fixture=JSON.parse(fs.readFileSync(path.join(__dirname,
    '../resources/candidates/claude-desktop-5.11492.5/sidebar-lifecycle-fixture.json')));
  assert.equal(fixture.status,'draft_not_installable');
  assert.equal(fixture.installed,false);
  const saved={collapsed:false,composedSidebarOpen:true,composedSidebarMounted:true,
    sidebarWidth:333,navPinV:2,isRouteDrilldown:false,isCuDocked:false,
    isNarrowViewport:false,customGroupsByScope:{},codeSidebarByScope:{},
    pinnedOrder:['keep-pin'],folderAppearanceByKey:{keep:'intact'}};
  const official=native(fixture.before,composed);
  official.hydrate(saved);
  assert.equal(official.collapsed(),false,'original session can reopen after hydration');
  const fixed=native(fixture.after,composed);
  assert.equal(fixed.collapsed(),true,'new window starts closed');
  fixed.hydrate(saved);
  assert.equal(fixed.collapsed(),true,'refresh does not re-expand');
  assert.equal(fixed.get().sidebarWidth,333);
  assert.deepEqual(fixed.get().pinnedOrder,['keep-pin']);
  assert.deepEqual(fixed.get().folderAppearanceByKey,{keep:'intact'});
  fixed.open(); assert.equal(fixed.collapsed(),false,'native open still works');
  fixed.close(); assert.equal(fixed.collapsed(),true,'one-click close still works');
  fixed.open();
  const reopened=native(fixture.after,composed);reopened.hydrate(fixed.get());
  assert.equal(reopened.collapsed(),true,'new window closes again');
  assert.equal(reopened.prepaint('{"state":{"collapsed":false,"sidebarWidth":333}}').collapsed,true);
  assert.equal(reopened.prepaint('{"state":{"collapsed":false,"sidebarWidth":333}}').sidebarWidth,333);
  assert.equal(reopened.prepaint('corrupt data').collapsed,true);
});
