const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const adapterPath = path.join(__dirname, '..', 'scripts', 'cowork_compaction_policy.cjs');
const original = 'local_11111111-1111-4111-8111-111111111111';

function fixture(t, windowTokens = 700000) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-compact-policy-test-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const policyPath = path.join(dir, 'policy.json');
  const policy = { version: 1, sessions: { [original]: { enabled: true, windowTokens } } };
  fs.writeFileSync(policyPath, JSON.stringify(policy));
  return { dir, policyPath, policy };
}

function adapter() {
  assert.ok(fs.existsSync(adapterPath), 'Missing session-scoped automatic compaction adapter');
  return require(adapterPath).applyCompactionPolicy;
}

test('production policy reaches native environment and inline SDK settings without changing selectors', t => {
  const { policyPath } = fixture(t);
  const options = { model: 'configured-model[1m]', effort: 'high', env: { ANTHROPIC_AUTH_TOKEN: '[in-memory credential sentinel]', OTHER: 'keep' }, settings: { permissions: { deny: ['Write'] }, disableAutoMode: true } };
  const result = adapter()(options, { sessionId: original, policyPath });
  assert.equal(result.applied, true);
  assert.equal(result.windowTokens, 700000);
  assert.equal(options.env.CLAUDE_CODE_AUTO_COMPACT_WINDOW, '700000');
  assert.equal(options.settings.autoCompactWindow, 700000);
  assert.equal(options.model, 'configured-model[1m]');
  assert.equal(options.effort, 'high');
  assert.equal(options.env.ANTHROPIC_AUTH_TOKEN, '[in-memory credential sentinel]');
  assert.equal(options.env.OTHER, 'keep');
  assert.deepEqual(options.settings.permissions, { deny: ['Write'] });
  assert.equal(options.settings.disableAutoMode, true);
  assert.ok(!JSON.stringify(result).includes('credential'));
});

test('controlled 100k window is supported without changing the declared model capacity', t => {
  const { policyPath } = fixture(t, 100000);
  const options = { model: 'configured-model[1m]', settings: {} };
  adapter()(options, { sessionId: original, policyPath });
  assert.equal(options.env.CLAUDE_CODE_AUTO_COMPACT_WINDOW, '100000');
  assert.equal(options.model, 'configured-model[1m]');
});

test('unlisted sessions are unchanged', t => {
  const { policyPath } = fixture(t);
  const options = { model: 'other-model', env: { EXISTING: 'unchanged' }, settings: { permissions: { allow: [] } } };
  const before = structuredClone(options);
  assert.equal(adapter()(options, { sessionId: 'different-session', policyPath }).applied, false);
  assert.deepEqual(options, before);
});

test('disabled policy is a no-op', t => {
  const { policyPath, policy } = fixture(t);
  policy.sessions[original].enabled = false;
  fs.writeFileSync(policyPath, JSON.stringify(policy));
  const options = { model: 'configured-model' };
  assert.equal(adapter()(options, { sessionId: original, policyPath }).applied, false);
  assert.deepEqual(options, { model: 'configured-model' });
});

test('invalid window fails before mutating options', t => {
  for (const value of [99999, 1000001, '700000', 700000.5]) {
    const { policyPath } = fixture(t, value);
    const options = { model: 'configured-model', env: { KEEP: 'yes' } };
    const before = structuredClone(options);
    assert.throws(() => adapter()(options, { sessionId: original, policyPath }), /windowTokens/);
    assert.deepEqual(options, before);
  }
});

test('file-based inline settings are not silently replaced', t => {
  const { policyPath } = fixture(t);
  const options = { settings: '/must-not-drop-existing-permissions.json' };
  assert.throws(() => adapter()(options, { sessionId: original, policyPath }), /inline settings/);
  assert.equal(options.settings, '/must-not-drop-existing-permissions.json');
});

test('an explicitly disabled native automatic compactor is detected, not falsely reported enabled', t => {
  const { policyPath, dir } = fixture(t);
  fs.writeFileSync(path.join(dir, '.claude.json'), JSON.stringify({ autoCompactEnabled: false }));
  const options = { env: { CLAUDE_CONFIG_DIR: dir } };
  assert.throws(() => adapter()(options, { sessionId: original, policyPath }), /autoCompactEnabled/);
  assert.equal(options.env.CLAUDE_CODE_AUTO_COMPACT_WINDOW, undefined);
});

test('explicit disable environment is detected before applying the policy', t => {
  const { policyPath } = fixture(t);
  const options = { env: { DISABLE_AUTO_COMPACT: '1' } };
  assert.throws(() => adapter()(options, { sessionId: original, policyPath }), /DISABLE_AUTO_COMPACT/);
  assert.equal(options.env.CLAUDE_CODE_AUTO_COMPACT_WINDOW, undefined);
});

test('temporary diagnostics capture native decisions without changing normal settings', t => {
  const { policyPath, policy } = fixture(t);
  policy.sessions[original].diagnostics = true;
  fs.writeFileSync(policyPath, JSON.stringify(policy));
  const options = {};
  adapter()(options, { sessionId: original, policyPath });
  assert.equal(options.debugFile, path.join(path.dirname(policyPath), 'native-compaction-diagnostic.log'));
});
