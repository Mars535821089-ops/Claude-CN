const test=require('node:test'),assert=require('node:assert/strict');
const c=require('../scripts/claude5_compat.cjs');
test('gated locale aliases preserve all non-Chinese data and code',()=>{
 const src='var e={"en-US":{a:"Hello {name}"},"zh-Hans":{},"zh-Hant":{}};function locale(x){return e[x]||e["en-US"]}';
 const out=c.patchGated(src,{a:'你好，{name}'});
 assert.match(out,/"zh-CN"/);assert.deepEqual(c.gated(out).english,{a:'Hello {name}'});
 assert.throws(()=>c.patchGated(src,{}));
 assert.throws(()=>c.patchGated(out,{a:'你好，{name}'}));
});
test('sidebar mutation cannot silently target an unknown build',()=>{
 assert.throws(()=>c.patchSidebar('var collapsed=false;'));
});
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),os=require('node:os');
const candidate=path.join(__dirname,'../resources/candidates/claude-desktop-5.1231.2');
const fixture=JSON.parse(fs.readFileSync(path.join(candidate,'sidebar-lifecycle-fixture.json')));
const plain=x=>JSON.parse(JSON.stringify(x));
function nativeSidebar(parts,composed){
 const box={lS:composed,Vy:{collapsed:true,sidebarWidth:280},Ly:x=>x,
  q:x=>x!==null&&typeof x==='object'&&!Array.isArray(x),Lx:()=>null,Ix:x=>x,Zv:x=>x,
  Cv:x=>x,Tx:[0,7,30],wx:x=>x,Ex:()=>true,Dx:()=>true,nS:x=>x,qx:x=>x,Jx:()=>true,
  Zx:(x,k)=>x.codeSidebarByScope[k],oS:()=>{},cS:{hovering:false,peeked:false},
  iS:()=>false,pS:x=>x.experimentFrameActive||x.sidebarlessClaims>0,
  fS:x=>x.isCuDocked||x.isNarrowViewport,gS:()=>{}};
 for(const k of ['Uy','uS','VS'])box[k]=vm.runInNewContext('('+parts[k]+')',box);
 const merge=vm.runInNewContext('('+parts.merge+')',box);
 const initial={collapsed:true,composedSidebarOpen:false,composedSidebarMounted:false,
  isCuDocked:false,isNarrowViewport:false,isRouteDrilldown:false,
  pinnedOrder:[],sidebarWidth:280,codeSidebarByScope:{},customGroupsByScope:{},navPinV:2};
 let state={...initial};box.e=fn=>{state={...state,...fn(state)}};
 const set=vm.runInNewContext('('+parts.setCollapsedPref+')',box);
 return {merge,initial,get:()=>plain(state),open:()=>set(false),close:()=>set(true),
  hydrate:p=>{state=merge(structuredClone(p),initial);return plain(state)},
  collapsed:()=>box.VS(state),prepaint:x=>plain(box.Uy(x))};
}
for(const composed of [false,true])test(`native sidebar lifecycle composed=${composed}`,()=>{
 const h=nativeSidebar(fixture.after,composed);
 assert.equal(h.collapsed(),true,'new window starts collapsed');
 const saved={collapsed:false,composedSidebarOpen:true,composedSidebarMounted:true,
  pinnedOrder:['keep-pin'],sidebarWidth:333,navPinV:2,customGroupsByScope:{},codeSidebarByScope:{},
  folderAppearanceByKey:{keep:'untouched'}};
 h.hydrate(saved);assert.equal(h.collapsed(),true,'hydration cannot reopen a fresh window');
 assert.deepEqual(h.get().pinnedOrder,['keep-pin']);assert.equal(h.get().sidebarWidth,333);
 assert.deepEqual(h.get().folderAppearanceByKey,{keep:'untouched'});
 h.open();assert.equal(h.collapsed(),false,'explicit open works within this window');
 h.close();assert.equal(h.collapsed(),true,'native one-click close works');
 h.open();const reopened=nativeSidebar(fixture.after,composed);reopened.hydrate(h.get());
 assert.equal(reopened.collapsed(),true,'refresh/new window closes, keeping other preferences');
 assert.deepEqual(reopened.get().pinnedOrder,['keep-pin']);
});
test('prepaint refuses persisted expansion and tolerates corrupt storage',()=>{
 const h=nativeSidebar(fixture.after,false);
 for(const raw of [null,'{}','bad json','null','{"state":{"collapsed":false,"sidebarWidth":333}}'])
  assert.equal(h.prepaint(raw).collapsed,true);
 assert.equal(h.prepaint('{"state":{"collapsed":false,"sidebarWidth":333}}').sidebarWidth,333);
});
test('raw official traditional hydration reproduces open-on-refresh defect',()=>{
 const old=nativeSidebar(fixture.before,false);old.hydrate({collapsed:false,navPinV:2});
 assert.equal(old.collapsed(),false);
});
test('native global compaction config is not a session allowlist and is refreshable',()=>{
 const f=JSON.parse(fs.readFileSync(path.join(candidate,'cowork-native-fixture.json')));
 let config={autoCompactWindow:700000};const box={Ul:()=>config};
 const get=vm.runInNewContext('('+f.getter+')',box);
 assert.equal(get('session-A'),700000);assert.equal(get('session-B'),700000);
 config={autoCompactWindow:400000};assert.equal(get('session-A'),400000);
 config={};assert.equal(get('session-A'),undefined);
 assert.equal(f.native_equivalent_to_session_allowlist,false);
});
const toy='async function ih(e,r){let{sessionId:i,sessionType:o}=r;t.MTt.info("[HostLoop] sdkOptions after patch: %o",{});return e}';
function hook(env,dirname=path.join(__dirname,'../scripts')){
 const logs=[];const t={MTt:{info:(...x)=>logs.push(x),warn:(...x)=>logs.push(x)}};
 const run=new Function('process','require','__dirname','t',c.patchCowork(toy)+';return ih;')({env},require,dirname,t);
 return {run,logs};
}
test('Cowork no explicit policy is a no-op and missing module retains native options',async()=>{
 const options={env:{CLAUDE_CODE_AUTO_COMPACT_WINDOW:'777777',KEEP:'yes'},settings:{permission:'keep'}};
 const original=structuredClone(options),h=hook({});
 await h.run(options,{sessionId:'synthetic-A'});assert.deepEqual(options,original);
 const unavailable=hook({CLAUDE_CN_SESSION_COMPACTION_POLICY:'/dev/null'},'/nonexistent-synthetic-module');
 await unavailable.run(options,{sessionId:'synthetic-A'});assert.deepEqual(options,original);
 assert.ok(unavailable.logs.some(x=>String(x[0]).includes('native defaults retained')));
 assert.throws(()=>c.patchCowork(c.patchCowork(toy)));assert.throws(()=>c.patchCowork(toy.replace('sessionId:i','sessionId:z')));
});
test('Cowork adapter preserves scope, inline settings and explicit native disable',async t=>{
 const root=path.join('/Volumes/Mars-私人服务器/内置盘写入统一管理/01-编译与构建/Codex-provider-switch/claude5-1231-2-20261111/implementation-01','test-fixtures');
 fs.mkdirSync(root,{recursive:true});const dir=fs.mkdtempSync(path.join(root,'synthetic-policy-'));
 t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
 const policyPath=path.join(dir,'policy.json');fs.writeFileSync(policyPath,JSON.stringify({version:1,sessions:{'synthetic-A':{enabled:true,windowTokens:700000}}}));
 fs.copyFileSync(path.join(__dirname,'../scripts/cowork_compaction_policy.cjs'),path.join(dir,'claude-cn-compaction-policy.cjs'));
 const h=hook({CLAUDE_CN_SESSION_COMPACTION_POLICY:policyPath},dir);
 const selected={model:'synthetic-model',effort:'high',env:{KEEP:'yes'},settings:{permissions:{deny:['Write']}}};
 await h.run(selected,{sessionId:'synthetic-A'});assert.equal(selected.env.CLAUDE_CODE_AUTO_COMPACT_WINDOW,'700000');
 assert.equal(selected.model,'synthetic-model');assert.deepEqual(selected.settings.permissions,{deny:['Write']});
 const other={env:{KEEP:'unchanged'}};await h.run(other,{sessionId:'synthetic-B'});assert.deepEqual(other,{env:{KEEP:'unchanged'}});
 for(const options of [{env:{DISABLE_AUTO_COMPACT:'1'}},{managedSettings:{autoCompactEnabled:false}},{settings:'/synthetic/file.json'}]){
  const before=structuredClone(options);await h.run(options,{sessionId:'synthetic-A'});assert.deepEqual(options,before);
 }
});
