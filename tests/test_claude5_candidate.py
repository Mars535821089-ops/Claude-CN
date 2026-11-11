"""5.x is a separate, fail-closed candidate; 4.x acceptance must not widen."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import claude5_candidate as c
from upstream_patch_lib import BASE_LANGUAGE_LIST


class Claude5CandidateTests(unittest.TestCase):
    def fixture(self, root):
        before = (BASE_LANGUAGE_LIST + '];').encode()
        after = (BASE_LANGUAGE_LIST + ',"zh-CN"];').encode()
        specs = {}
        for name in c.GATES:
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(before)
            specs[name] = dict(sha256=c.sha(before), patched_sha256=c.sha(after), expected=1)
        return before, after, specs

    def test_all_three_gates_and_idempotence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, after, specs = self.fixture(root)
            edits = c.prepare_gates(root, specs)
            self.assertEqual(len(edits), 3)
            c.apply_prepared(edits)
            self.assertTrue(all((root / n).read_bytes() == after for n in specs))
            self.assertEqual(c.prepare_gates(root, specs), [])

    def test_partial_wrong_or_symlink_gate_fails_before_write(self):
        for failure in ('partial', 'wrong', 'symlink'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                before, after, specs = self.fixture(root)
                names = list(specs)
                p = root / names[-1]
                if failure == 'symlink':
                    p.unlink()
                    p.symlink_to(root / names[0])
                else:
                    p.write_bytes(after if failure == 'partial' else b'unknown source')
                with self.assertRaises(ValueError):
                    c.prepare_gates(root, specs)
                self.assertEqual((root / names[0]).read_bytes(), before)

    def test_missing_gate_is_not_two_gate_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, _, specs = self.fixture(root)
            specs.pop(next(iter(specs)))
            with self.assertRaises(ValueError):
                c.prepare_gates(root, specs)

    def test_authored_translation_never_changes_official_or_source(self):
        source = {'a': 'Hello {name}', 'b': 'Official'}
        base = {'b': '官方'}
        self.assertEqual(c.merge_authored(source, base, {'Hello {name}': '你好，{name}'}),
                         ({'b': '官方', 'a': '你好，{name}'}, {}))
        with self.assertRaises(ValueError):
            c.merge_authored(source, base, {'Official': '覆盖官方'})
        with self.assertRaises(ValueError):
            c.merge_authored(source, base, {'Hello {name}': 'Hello {name}'})
        with self.assertRaises(ValueError):
            c.merge_authored(source, base, {'Stale English': '过时'})

    def test_incomplete_catalog_cannot_be_packaged(self):
        with self.assertRaisesRegex(ValueError, 'missing'):
            c.require_complete({'frontend': {'untranslated': 'not Chinese'}})

    def test_old_gate_loader_still_rejects_five(self):
        import patch_whitelist_423118 as old
        with self.assertRaises(ValueError):
            old.load_reviewed('5.1231.0')

    def test_production_profile_accepts_both_simplified_chinese_aliases(self):
        profile = c.load(c.CANDIDATE / 'language-whitelist-reviewed.json')
        for spec in profile['files'].values():
            self.assertEqual(spec['locales'], ['zh-CN', 'zh-Hans'])


if __name__ == '__main__':
    unittest.main()
