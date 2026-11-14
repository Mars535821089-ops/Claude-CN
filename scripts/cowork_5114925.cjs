'use strict';

// Only a version-bound, opt-in session policy adapter. Without a user policy,
// the signed app's existing autoCompactWindow getter and SDK env stay native.
const crypto=require('node:crypto');
const acorn=require('acorn');
const SOURCE_SHA256='be53e2ca65f39dae7c3cbb7358d309cb27be2a1f6a93c46e9f63d4b97dfb9939';
const FUNCTION='async function jm(e,r){let{sessionId:i,sessionType:o,';
const LOG='t.lDt.info("[HostLoop] sdkOptions after patch: %o",';
const MARKER='local-auto-compact-policy:5.11492.5';

function patchCowork(source){
  if (source.split(FUNCTION).length!==2 || source.split(LOG).length!==2 ||
      source.includes(MARKER)) throw new Error('new-version Cowork ownership or marker changed');
  acorn.parse(source,{ecmaVersion:'latest',sourceType:'module'});
  const hook='/*'+MARKER+'*/(await(async()=>{try{'
    +'const p=process.env.CLAUDE_CN_SESSION_COMPACTION_POLICY;if(!p)return;'
    +'const{pathToFileURL}=await import("node:url");'
    +'const m=await import(pathToFileURL(require("node:path").join(__dirname,"claude-cn-compaction-policy.cjs")).href);'
    +'const d=(m.default??m).applyCompactionPolicy(e,{sessionId:i,policyPath:p});'
    +'t.lDt.info("[LocalAutoCompact] policy %o",d)'
    +'}catch(z){t.lDt.warn("[LocalAutoCompact] policy unavailable (%s); native defaults retained",z?.name??"Error")}'
    +'})()),';
  const result=source.replace(LOG,hook+LOG);
  acorn.parse(result,{ecmaVersion:'latest',sourceType:'module'});
  return result;
}

function patchPinned(source,expected=SOURCE_SHA256){
  if (expected!==SOURCE_SHA256 ||
      crypto.createHash('sha256').update(source).digest('hex')!==SOURCE_SHA256)
    throw new Error('signed current-version Cowork source changed');
  return patchCowork(source);
}
module.exports={patchCowork,patchPinned,SOURCE_SHA256};
