import importlib.util
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))


class Claude7LocaleTests(unittest.TestCase):
    def implementation(self):
        self.assertIsNotNone(importlib.util.find_spec('claude7_locales'),
                             'Claude 7 reproducible locale assembler is missing')
        import claude7_locales
        return claude7_locales

    def catalogs(self):
        return {'desktop': {'en': {'menu': 'Open'}, 'official': {'menu': '打开'}},
                'frontend': {'en': {'new': 'Hello {name}'}, 'official': {}},
                'dynamic': {'en': {'cancel': 'Cancel'}, 'official': {}}}

    def legacy(self):
        return {'frontend': {'en': {'old': 'Hello {name}'},
                             'zh': {'old': '你好，{name}'}},
                'dynamic': {'en': {'cancel': 'Cancel'}, 'zh': {'cancel': '取消'}}}

    def review(self, catalogs, translations):
        from update_guard import catalog_fingerprint
        return {'version': '7.37095.0', 'catalogs': {'frontend': {
            'english_sha256': catalog_fingerprint(catalogs['frontend']['en']),
            'translations': translations}}}

    def test_reuses_identical_english_under_new_id_and_preserves_native_chinese(self):
        module = self.implementation()
        result = module.compose(self.catalogs(), self.legacy(), [])
        self.assertEqual(result['frontend']['translations'], {'new': '你好，{name}'})
        self.assertEqual(result['desktop']['translations'], {'menu': '打开'})
        self.assertEqual(result['dynamic']['translations'], {'cancel': '取消'})

    def test_changed_meaning_under_old_id_requires_explicit_review(self):
        module = self.implementation()
        catalogs = self.catalogs()
        catalogs['frontend']['en'] = {'old': 'Delete {name}'}
        with self.assertRaisesRegex(ValueError, 'unreviewed'):
            module.compose(catalogs, self.legacy(), [])
        review = self.review(catalogs, {'old': '删除 {name}'})
        result = module.compose(catalogs, self.legacy(), [review])
        self.assertEqual(result['frontend']['translations'], {'old': '删除 {name}'})

    def test_new_additions_cannot_overwrite_verified_or_prior_reviewed_translations(self):
        module = self.implementation()
        catalogs = self.catalogs()
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            module.compose(catalogs, self.legacy(),
                           [self.review(catalogs, {'new': '别的意思'})])
        catalogs['frontend']['en'] = {'new': 'Delete {name}'}
        review = self.review(catalogs, {'new': '删除 {name}'})
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            module.compose(catalogs, self.legacy(), [review, review])

    def test_pinned_review_fails_on_source_drift_before_publication(self):
        module = self.implementation()
        catalogs = self.catalogs()
        catalogs['frontend']['en'] = {'new': 'Delete {name}'}
        review = self.review(catalogs, {'new': '删除 {name}'})
        catalogs['frontend']['en']['new'] = 'Archive {name}'
        with self.assertRaisesRegex(ValueError, 'fingerprint'):
            module.compose(catalogs, self.legacy(), [review])

    def test_any_unresolved_group_refuses_complete_candidate(self):
        module = self.implementation()
        catalogs = self.catalogs()
        catalogs['dynamic']['en']['extra'] = 'Confirm'
        with self.assertRaisesRegex(ValueError, 'unreviewed'):
            module.compose(catalogs, self.legacy(), [])

    def test_parameter_loss_is_rejected_by_real_icu_validator(self):
        module = self.implementation()
        catalogs = self.catalogs()
        catalogs['frontend']['en'] = {'new': 'Delete {name}'}
        with self.assertRaisesRegex(ValueError, 'ICU'):
            module.compose(catalogs, self.legacy(),
                           [self.review(catalogs, {'new': '删除'})])

    def test_desktop_additions_cannot_overwrite_native_wording(self):
        module = self.implementation()
        from update_guard import catalog_fingerprint
        catalogs = self.catalogs()
        review = {'version': '7.37095.0', 'catalogs': {'desktop': {
            'english_sha256': catalog_fingerprint(catalogs['desktop']['en']),
            'translations': {'menu': '删除所有文件'}}}}
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            module.compose(catalogs, self.legacy(), [review])

    def test_review_snapshot_hash_binds_exact_bytes_that_are_parsed(self):
        module = self.implementation()
        first = b'{"version":"7.37095.0","catalogs":{}}'
        second = b'{"version":"wrong-generation","catalogs":{}}'
        with patch.object(Path, 'read_bytes', side_effect=[first, second]):
            review, digest = module.read_review_snapshot(Path('review.json'))
        self.assertEqual(review, {'version': '7.37095.0', 'catalogs': {}})
        self.assertEqual(digest, hashlib.sha256(first).hexdigest())
        with patch.object(Path, 'read_bytes', return_value=b'{"catalogs":{},"catalogs":{}}'):
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                module.read_review_snapshot(Path('duplicate.json'))

    def test_source_only_output_cannot_modify_a_third_app(self):
        module = self.implementation()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / 'another.app/Contents'
            parent.mkdir(parents=True)
            with (patch.object(module, 'VOLUME', root),
                  patch.object(module, 'OUTPUT_ROOT', root),
                  patch.object(Path, 'is_mount', return_value=True),
                  patch.object(module, 'fetch_latest', side_effect=AssertionError('Must reject before network'))):
                with self.assertRaisesRegex(ValueError, 'outside apps'):
                    module.assemble(root / 'source.app', root / 'old.app', parent / 'new-output')
            self.assertFalse((parent / 'new-output').exists())


if __name__ == '__main__':
    unittest.main()
