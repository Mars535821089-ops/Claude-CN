'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const root = path.join(__dirname, '..');
const implementation = path.join(root, 'scripts/cowork_session_adapter.cjs');
const policyFile = path.join(root, 'scripts/cowork_compaction_policy.cjs');
const policyHash = 'dfbcc716a0ec33a6d2d7fe21cc6d36c99f56efa12d3639e5a479e13ac872830b';
const sha = s => crypto.createHash('sha256').update(s).digest('hex');
const anchor = 't.OZt.info("[HostLoop] sdkOptions after patch: %o",';
// Hand-reviewed expected insertion, independent of the implementation.
const hook = '/*local-auto-compact-policy:7.37095.0*/(await(async()=>{try{const p=process.env.CLAUDE_CN_SESSION_COMPACTION_POLICY;if(!p)return;const{pathToFileURL}=await import("node:url");const m=await import(pathToFileURL(require("node:path").join(__dirname,"claude-cn-compaction-policy.cjs")).href);const d=(m.default??m).applyCompactionPolicy(e,{sessionId:r,policyPath:p});t.OZt.info("[LocalAutoCompact] policy %o",d)}catch(z){t.OZt.warn("[LocalAutoCompact] policy unavailable (%s); native defaults retained",z?.name??"Error")}})()),';
const fixture = '"use strict";/* 中文字节和 CRLF 保留 */\r\nconst events=[];const t={OZt:{info:(...x)=>events.push(x),warn:(...x)=>events.push(x)}};async function Bp(e,n){let{sessionId:r}=n;return t.OZt.info("[HostLoop] sdkOptions after patch: %o",e),e}module.exports={Bp,events};\r\n';
const expected = fixture.replace(anchor, hook + anchor);
function profile(source = fixture, output = source.replace(anchor, hook + anchor)) {
  return {
    format_version: 1, target_version: '7.37095.0',
    target: { archive: 'app.asar', path: '.vite/build/fixture.cjs' },
    source_sha256: sha(source), module_path: '.vite/build/claude-cn-compaction-policy.cjs',
    policy_module_sha256: policyHash, owner_function: 'Bp', options_param: 'e',
    session_binding: { parameter: 'n', property: 'sessionId', local: 'r' },
    logger: 't.OZt', anchor, proposed_output_sha256: sha(output),
  };
}
function adapter() {
  assert.ok(fs.existsSync(implementation), 'Reusable source-pinned adapter is not implemented');
  return require(implementation).patchSource;
}
function patch(source = fixture, p = profile(source), version = '7.37095.0') {
  return adapter()(source, p, version);
}

test('inserts in the reviewed function and preserves every other byte', () => {
  assert.equal(patch(), expected);
  assert.equal(patch().replace(hook, ''), fixture);
  assert.equal(sha(fs.readFileSync(policyFile)), policyHash);
});
test('rejects changed source before accepting an otherwise valid anchor', () => {
  assert.throws(() => patch(fixture + ' ', profile()), /source.*(hash|fingerprint)/i);
});
test('requires the requested version and the reviewed output fingerprint', () => {
  assert.throws(() => patch(fixture, profile(), '8.0.0'), /version/i);
  assert.throws(() => patch(fixture, { ...profile(), proposed_output_sha256: '0'.repeat(64) }), /output.*(hash|fingerprint)/i);
});
test('rejects wrong owner even when another function carries the requested session binding', () => {
  const source = fixture + 'async function uh(e,n){let{sessionId:r}=n;return e}';
  assert.throws(() => patch(source, { ...profile(source), owner_function: 'uh' }), /owner|function/i);
});
test('rejects a session binding that does not originate in the second parameter', () => {
  const source = fixture.replace('let{sessionId:r}=n;', 'let{sessionId:r}=e;');
  assert.throws(() => patch(source), /session|binding/i);
  assert.throws(() => patch(fixture, { ...profile(), session_binding: { parameter: 'n', property: 'sessionId', local: 'i' } }), /session|binding/i);
});
test('rejects anchors inside nested functions despite valid outer bindings', () => {
  const source = fixture.replace('return ' + anchor, 'return (async()=>(' + anchor).replace(',e),e}module.exports', ',e),e))()}module.exports');
  require('acorn').parse(source, { ecmaVersion: 'latest' });
  assert.throws(() => patch(source), /nested|owner|function/i);
});
test('rejects duplicate anchors and duplicate owner declarations', () => {
  const source = fixture + 'function extra(){' + anchor + 'null)}';
  assert.throws(() => patch(source), /unique|duplicate|anchor/i);
  const duplicatedOwner = fixture + 'async function Bp(e,n){let{sessionId:r}=n;return e}';
  assert.throws(() => patch(duplicatedOwner), /unique|duplicate|owner|declared/i);
});
test('refuses already-applied input, including a re-pinned patched source', () => {
  assert.throws(() => patch(expected, profile(expected)), /already|marker/i);
});
test('rejects an anchor that is only quoted data', () => {
  const source = 'async function Bp(e,n){let{sessionId:r}=n;return `' + anchor + '`}';
  assert.throws(() => patch(source), /call|anchor/i);
});
test('rejects reassigned or block-shadowed session identifiers', () => {
  const reassigned = fixture.replace('return ' + anchor, 'r="wrong-session";return ' + anchor);
  assert.throws(() => patch(reassigned), /binding|assign/i);
  const shadowed = fixture.replace('return ' + anchor, '{let r="wrong-session";return ' + anchor).replace(',e),e}module.exports', ',e),e}}module.exports');
  assert.throws(() => patch(shadowed), /scope|block|binding/i);
});
test('rejects unreviewed module bytes and code-bearing profile values', () => {
  assert.throws(() => patch(fixture, { ...profile(), policy_module_sha256: '0'.repeat(64) }), /policy.*(hash|fingerprint)/i);
  assert.throws(() => patch(fixture, { ...profile(), logger: 't.OZt;evil()' }), /profile|logger|identifier/i);
  assert.throws(() => patch(fixture, { ...profile(), module_path: '../policy.cjs' }), /module|path/i);
});
test('a future reviewed profile changes bindings without copying the adapter script', () => {
  const source = fixture.replaceAll('Bp', 'NextOwner').replaceAll('sessionId:r', 'sessionId:session').replaceAll('(e,n)', '(options,context)').replaceAll('}=n;', '}=context;').replaceAll(',e),e}', ',options),options}').replaceAll('t.OZt', 't.Log');
  const nextAnchor = 't.Log.info("[HostLoop] sdkOptions after patch: %o",';
  const nextHook = hook.replaceAll('7.37095.0', '8.0.0').replace('applyCompactionPolicy(e,{sessionId:r,', 'applyCompactionPolicy(options,{sessionId:session,').replaceAll('t.OZt', 't.Log');
  const output = source.replace(nextAnchor, nextHook + nextAnchor);
  const p = { ...profile(source, output), target_version: '8.0.0', owner_function: 'NextOwner', options_param: 'options', session_binding: { parameter: 'context', property: 'sessionId', local: 'session' }, logger: 't.Log', anchor: nextAnchor };
  assert.equal(adapter()(source, p, '8.0.0'), output);
});

test('future minified session names cannot be captured by hook-local variables', () => {
  const source = fixture.replace('sessionId:r', 'sessionId:p');
  const safeHook = hook.replace('const p=process.env', 'const p$=process.env').replace('if(!p)', 'if(!p$)').replace('sessionId:r,policyPath:p', 'sessionId:p,policyPath:p$');
  const output = source.replace(anchor, safeHook + anchor);
  const p = { ...profile(source, output), session_binding: { parameter: 'n', property: 'sessionId', local: 'p' } };
  assert.equal(patch(source, p), output);
});
test('conditional destructuring cannot masquerade as the owner session binding', () => {
  const source = fixture.replace('let{sessionId:r}=n;', 'if(n.enabled){var{sessionId:r}=n;}');
  assert.throws(() => patch(source), /session|binding|scope/i);
});
test('loop writes cannot silently replace the reviewed session ID', () => {
  const source = fixture.replace('return ' + anchor, 'for(r of ["other"]){}return ' + anchor);
  assert.throws(() => patch(source), /binding|assign/i);
});

// Only this synthetic, reviewed fixture is executed. Vendor sources are never evaluated.
for (const mode of ['absent', 'missing', 'disabled', 'unlisted', 'enabled', 'invalid', 'native-disabled']) {
  test(`isolated ${mode} policy preserves native selectors and permissions`, t => {
    const transformed = patch();
    // The explicit process-cache override stays available on the user's Mac;
    // ordinary offline CI uses its native temporary directory without a mount.
    const base = process.env.CLAUDE_CN_TEST_TMPDIR ?? require('node:os').tmpdir();
    assert.ok(base && fs.statSync(base).isDirectory(), 'Fixture temporary directory unavailable');
    const dir = fs.mkdtempSync(path.join(base, 'cowork-session-adapter-'));
    t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
    fs.writeFileSync(path.join(dir, 'fixture.cjs'), transformed);
    fs.copyFileSync(policyFile, path.join(dir, 'claude-cn-compaction-policy.cjs'));
    const policyPath = path.join(dir, 'synthetic-policy.json');
    if (!['absent', 'missing'].includes(mode)) {
      fs.writeFileSync(policyPath, mode === 'invalid' ? '{' : JSON.stringify({ version: 1, sessions: { synthetic: { enabled: mode !== 'disabled', windowTokens: 700000 } } }));
    }
    const env = { PATH: process.env.PATH };
    if (mode !== 'absent') env.CLAUDE_CN_SESSION_COMPACTION_POLICY = policyPath;
    const command = `const {Bp,events}=require(${JSON.stringify(path.join(dir, 'fixture.cjs'))});const e={model:'synthetic-model',effort:'high',env:{KEEP:'yes'${mode === 'native-disabled' ? ",DISABLE_AUTO_COMPACT:'1'" : ''}},settings:{permissions:{deny:['Write']}}};Bp(e,{sessionId:${JSON.stringify(mode === 'unlisted' ? 'other' : 'synthetic')}}).then(out=>console.log(JSON.stringify({same:out===e,out,events})));`;
    const result = spawnSync(process.execPath, ['-e', command], { encoding: 'utf8', env, timeout: 10000 });
    assert.equal(result.status, 0, result.stderr);
    const got = JSON.parse(result.stdout);
    assert.equal(got.same, true);
    assert.equal(got.out.model, 'synthetic-model');
    assert.equal(got.out.effort, 'high');
    assert.deepEqual(got.out.settings.permissions, { deny: ['Write'] });
    assert.equal(got.out.env.KEEP, 'yes');
    assert.equal(got.out.env.CLAUDE_CODE_AUTO_COMPACT_WINDOW, mode === 'enabled' ? '700000' : undefined);
    assert.equal(got.out.settings.autoCompactWindow, mode === 'enabled' ? 700000 : undefined);
    assert.equal(got.events.at(-1)[0], '[HostLoop] sdkOptions after patch: %o');
    if (['missing', 'invalid', 'native-disabled'].includes(mode)) assert.match(got.events[0][0], /native defaults retained/);
    if (mode === 'absent') assert.equal(got.events.length, 1);
  });
}
