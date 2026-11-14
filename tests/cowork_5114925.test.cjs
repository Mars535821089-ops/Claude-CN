'use strict';

const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {patchCowork,patchPinned}=require('../scripts/cowork_5114925.cjs');

const toy='async function jm(e,r){let{sessionId:i,sessionType:o,hostProcessCwd:d}=r;'
  +'t.lDt.info("[HostLoop] sdkOptions after patch: %o",{});return e}';

function runHook(environment,dirname){
  const logs=[];const t={lDt:{info:(...x)=>logs.push(x),warn:(...x)=>logs.push(x)}};
  const run=new Function('process','require','__dirname','t',patchCowork(toy)+';return jm;')(
    {env:environment},require,dirname,t);
  return {run,logs};
}

test('source-bound Cowork hook is no-op without an explicit policy and is single-use',async()=>{
  const h=runHook({},path.join(__dirname,'../scripts'));
  const options={model:'selected',effort:'high',env:{KEEP:'yes'},settings:{deny:['Write']}};
  const original=structuredClone(options);
  await h.run(options,{sessionId:'fixture-A'});
  assert.deepEqual(options,original);
  assert.throws(()=>patchCowork(patchCowork(toy)));
  assert.throws(()=>patchPinned(toy,'0'.repeat(64)));
});

test('only explicitly selected Cowork session receives policy window',async t=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'claude-5114925-policy-'));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  fs.copyFileSync(path.join(__dirname,'../scripts/cowork_compaction_policy.cjs'),
    path.join(root,'claude-cn-compaction-policy.cjs'));
  const policy=path.join(root,'policy.json');
  fs.writeFileSync(policy,JSON.stringify({version:1,sessions:{'fixture-A':{enabled:true,windowTokens:700000}}}));
  const h=runHook({CLAUDE_CN_SESSION_COMPACTION_POLICY:policy},root);
  const selected={model:'selected',effort:'high',env:{KEEP:'yes'},settings:{deny:['Write']}};
  await h.run(selected,{sessionId:'fixture-A'});
  assert.equal(selected.env.CLAUDE_CODE_AUTO_COMPACT_WINDOW,'700000');
  assert.equal(selected.model,'selected');
  assert.equal(selected.effort,'high');
  assert.deepEqual(selected.settings.deny,['Write']);
  assert.equal(selected.settings.autoCompactWindow,700000);
  const other={env:{KEEP:'yes'}};
  await h.run(other,{sessionId:'fixture-B'});
  assert.deepEqual(other,{env:{KEEP:'yes'}});
  const disabled={env:{DISABLE_AUTO_COMPACT:'1',KEEP:'yes'}};
  await h.run(disabled,{sessionId:'fixture-A'});
  assert.equal(disabled.env.CLAUDE_CODE_AUTO_COMPACT_WINDOW,undefined);
});
