import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'scripts' / 'patch_cowork_compaction.py'
SESSION = 'local_11111111-1111-4111-8111-111111111111'
ANCHOR = 't.Yut.info("[HostLoop] sdkOptions after patch: %o",'
SOURCE = 'async function Ah(e,r){let{sessionId:a,sessionType:o}=r;return 1,' + ANCHOR + '{model:e.model}),{workspaceMcpServer:{}}}'
NEW_ANCHOR = 't.tvt.info("[HostLoop] sdkOptions after patch: %o",'
NEW_SOURCE = 'async function eh(e,r){let{sessionId:i,sessionType:o}=r;return 1,' + NEW_ANCHOR + '{model:e.model}),{workspaceMcpServer:{}}}'
LATEST_SOURCE = 'async function th(e,r){let{sessionId:i,sessionType:o}=r;return 1,' + NEW_ANCHOR + '{model:e.model}),{workspaceMcpServer:{}}}'


class CompactionPatchTests(unittest.TestCase):
    def load(self):
        self.assertTrue(MODULE.exists(), 'Missing version-bound native SDK policy patcher')
        spec = importlib.util.spec_from_file_location('cowork_compaction_patch_test', MODULE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_latest_official_hostloop_retains_session_scoped_policy(self):
        m = self.load()
        fingerprint = hashlib.sha256(LATEST_SOURCE.encode()).hexdigest()
        with patch.object(m, 'LATEST_OFFICIAL_CHUNK_SHA256', fingerprint, create=True):
            result = m.patch_host_loop_source(
                LATEST_SOURCE, '/adapter.cjs', '/policy.json', SESSION,
                version='4.23118.1',
            )
            self.assertEqual(result.count(m.MARKER), 1)
            self.assertIn('applyCompactionPolicy(e,{sessionId:i,policyPath:', result)
            self.assertEqual(result.count(NEW_ANCHOR), 1)
            self.assertNotIn('e.model=', result)
            self.assertEqual(
                m.patch_host_loop_source(result, '/adapter.cjs', '/policy.json', SESSION,
                                         version='4.23118.1'),
                result,
            )
            changed = LATEST_SOURCE.replace('sessionId:i', 'sessionId:z')
            with patch.object(m, 'LATEST_OFFICIAL_CHUNK_SHA256',
                              hashlib.sha256(changed.encode()).hexdigest()):
                with self.assertRaisesRegex(ValueError, 'binding'):
                    m.patch_host_loop_source(
                        changed, '/adapter.cjs', '/policy.json', SESSION,
                        version='4.23118.1',
                    )

    def test_hook_is_session_scoped_and_preserves_existing_option_log(self):
        m = self.load()
        result = m.patch_host_loop_source(SOURCE, '/adapter.cjs', '/policy.json', SESSION)
        self.assertIn(json.dumps(SESSION), result)
        self.assertEqual(result.count(m.MARKER), 1)
        self.assertEqual(result.count(ANCHOR), 1)
        self.assertIn('applyCompactionPolicy(e,{sessionId:a,policyPath:', result)
        self.assertNotIn('e.model=', result)
        self.assertNotIn('e.effort=', result)

    def test_repeat_is_idempotent(self):
        m = self.load()
        once = m.patch_host_loop_source(SOURCE, '/adapter.cjs', '/policy.json', SESSION)
        self.assertEqual(m.patch_host_loop_source(once, '/adapter.cjs', '/policy.json', SESSION), once)

    def test_unknown_build_and_ambiguous_anchor_are_rejected(self):
        m = self.load()
        with self.assertRaisesRegex(ValueError, 'anchor'):
            m.patch_host_loop_source('async function unknown(){}', '/adapter.cjs', '/policy.json', SESSION)
        with self.assertRaisesRegex(ValueError, 'anchor'):
            m.patch_host_loop_source(SOURCE + SOURCE, '/adapter.cjs', '/policy.json', SESSION)

    def test_changed_scope_binding_is_rejected(self):
        m = self.load()
        changed = SOURCE.replace('sessionId:a', 'sessionId:z')
        with self.assertRaisesRegex(ValueError, 'binding'):
            m.patch_host_loop_source(changed, '/adapter.cjs', '/policy.json', SESSION)

    def test_only_target_archive_entry_changes(self):
        m = self.load()
        with tempfile.TemporaryDirectory(prefix='cowork-compact-asar-test-') as tmp:
            root = Path(tmp)
            src = root / 'source'
            src.mkdir()
            (src / 'host.js').write_text(SOURCE)
            (src / 'untouched.bin').write_bytes(b'keep-all-other-bytes\x00\xff')
            archive = root / 'old.asar'
            m.build_asar(src, archive)
            new_source = m.patch_host_loop_source(SOURCE, '/adapter.cjs', '/policy.json', SESSION)
            new_data = m.replace_asar_entry(archive.read_bytes(), 'host.js', new_source.encode())
            hs, _, header = m.read_asar_header(new_data, root / 'new.asar')
            self.assertEqual(m.read_asar_entry(new_data, hs, header, 'host.js'), new_source.encode())
            self.assertEqual(m.read_asar_entry(new_data, hs, header, 'untouched.bin'), b'keep-all-other-bytes\x00\xff')
            self.assertTrue(m.asar_entry(header, 'host.js')['integrity']['hash'])

    def test_hook_executes_policy_before_returning_sdk_options(self):
        m = self.load()
        with tempfile.TemporaryDirectory(prefix='cowork-compact-execution-test-') as tmp:
            root = Path(tmp)
            policy = root / 'policy.json'
            policy.write_text(json.dumps({'version': 1, 'sessions': {SESSION: {'enabled': True, 'windowTokens': 700000}}}))
            source = m.patch_host_loop_source(SOURCE, str(ROOT / 'scripts/cowork_compaction_policy.cjs'), str(policy), SESSION)
            source_path = root / 'host.js'
            source_path.write_text(source)
            code = r'''
const fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{
 const t={Yut:{info(){},warn(){throw new Error('Policy hook unexpectedly fell back')}}};
 const fn=eval('('+fs.readFileSync(process.argv[1],'utf8')+')');
 const opts={model:'configured-model',settings:{permissions:{deny:['Write']}}};
 await fn(opts,{sessionId:process.argv[2]});
 assert.equal(opts.env.CLAUDE_CODE_AUTO_COMPACT_WINDOW,'700000');
 assert.equal(opts.settings.autoCompactWindow,700000);
 assert.equal(opts.model,'configured-model');
 assert.deepEqual(opts.settings.permissions,{deny:['Write']});
 const other={model:'other'};await fn(other,{sessionId:'other-session'});
 assert.deepEqual(other,{model:'other'});
})().catch(e=>{console.error(e.message);process.exitCode=1});
'''
            result = subprocess.run(['node', '-e', code, str(source_path), SESSION], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_new_official_build_keeps_same_session_scope_and_native_options(self):
        m = self.load()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            policy = path / 'policy.json'
            policy.write_text(json.dumps({'version': 1, 'sessions': {
                SESSION: {'enabled': True, 'windowTokens': 700000},
            }}))
            with patch.object(m, 'NEW_OFFICIAL_CHUNK_SHA256',
                              m.hashlib.sha256(NEW_SOURCE.encode()).hexdigest(), create=True):
                source = m.patch_host_loop_source(NEW_SOURCE,
                    str(ROOT / 'scripts/cowork_compaction_policy.cjs'),
                    str(policy), SESSION, version='4.23118.0')
            self.assertEqual(source.count(m.MARKER), 1)
            self.assertEqual(source.count(NEW_ANCHOR), 1)
            (path / 'host.js').write_text(source)
            script = r'''
const fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{const t={tvt:{info(){},warn(_format,type){throw Error('unexpected policy fallback: '+type)}}};
const fn=eval('('+fs.readFileSync(process.argv[1],'utf8')+')');
const opts={model:'configured-model',settings:{permissions:{deny:['Write']}}};
await fn(opts,{sessionId:process.argv[2]});
assert.equal(opts.env.CLAUDE_CODE_AUTO_COMPACT_WINDOW,'700000');
assert.equal(opts.settings.autoCompactWindow,700000);
assert.equal(opts.model,'configured-model');
assert.deepEqual(opts.settings.permissions,{deny:['Write']});
const other={model:'untouched'};await fn(other,{sessionId:'other-session'});
assert.deepEqual(other,{model:'untouched'});})();
'''
            ran = subprocess.run(['node', '-e', script, str(path / 'host.js'), SESSION],
                                 capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stderr)

    def test_new_official_chunk_fingerprint_rejects_unrecognized_build(self):
        m = self.load()
        with self.assertRaisesRegex(ValueError, 'fingerprint'):
            m.patch_host_loop_source(NEW_SOURCE, '/adapter.cjs', '/policy.json', SESSION,
                                     version='4.23118.0')

    def test_stage_refuses_unknown_version_before_touching_app(self):
        m = self.load()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            app = base / 'old.app'
            (app / 'Contents').mkdir(parents=True)
            (app / 'Contents/Info.plist').write_bytes(
                m.plistlib.dumps({'CFBundleShortVersionString': '4.9999.0'}))
            with self.assertRaisesRegex(ValueError, 'Unsupported official Claude build'):
                m.stage_app(app, base / 'candidate.app', '/adapter.cjs', '/policy.json',
                            SESSION, '4.9999.0')
            self.assertFalse((base / 'candidate.app').exists())


if __name__ == '__main__':
    unittest.main()
