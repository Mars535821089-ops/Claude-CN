import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from upstream_patch_lib import build_asar, read_asar_header, read_asar_entry


class ReviewedAsarResourcesTests(unittest.TestCase):
    def implementation(self):
        self.assertIsNotNone(importlib.util.find_spec('reviewed_asar_resources'),
                             'Reviewed resource packaging is missing')
        import reviewed_asar_resources
        return reviewed_asar_resources

    def source(self, root):
        files = root / 'input'
        files.mkdir()
        (files / 'i18n').mkdir()
        (files / 'i18n/en-US.json').write_bytes(b'{"id":"Open"}')
        (files / 'keep.js').write_bytes(b'export const unchanged=true;\r\n')
        (files / 'native.node').write_bytes(b'fake native fixture, never executed')
        archive = root / 'source.asar'
        build_asar(files, archive, {'native.node'})
        return archive

    def read(self, path, name):
        raw = path.read_bytes()
        size, _, header = read_asar_header(raw, path)
        return read_asar_entry(raw, size, header, name)

    def test_packaged_additions_preserve_original_entries_and_unpacked_bytes(self):
        module = self.implementation()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.source(root)
            before = source.read_bytes()
            result = module.repack(source, root / 'new-slot',
                {'i18n/zh-CN.json': '中文'.encode()}, hashlib.sha256(before).hexdigest())
            target = root / 'new-slot/source.asar'
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(self.read(target, 'keep.js'), b'export const unchanged=true;\r\n')
            self.assertEqual(self.read(target, 'i18n/en-US.json'), b'{"id":"Open"}')
            self.assertEqual(self.read(target, 'i18n/zh-CN.json'), '中文'.encode())
            self.assertEqual(Path(str(target) + '.unpacked/native.node').read_bytes(),
                             b'fake native fixture, never executed')
            self.assertEqual(result['changed_entries'], ['i18n/zh-CN.json'])
            self.assertEqual(result['status'], 'resource_archive_only_not_installable')
            self.assertFalse(result['installed'])

    def test_wrong_source_hash_fails_before_creating_output(self):
        module = self.implementation()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.source(root)
            with self.assertRaisesRegex(ValueError, 'source'):
                module.repack(source, root / 'new-slot', {'new.json': b'{}'}, '0' * 64)
            self.assertFalse((root / 'new-slot').exists())

    def test_unsafe_target_or_unpacked_replacement_cannot_mutate_output(self):
        module = self.implementation()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.source(root)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            for name in ['../outside', '/absolute', 'a/../b', 'a\\b', 'native.node']:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    module.repack(source, root / 'new-slot', {name: b'data'}, digest)
                self.assertFalse((root / 'new-slot').exists())

    def test_existing_output_and_any_app_ancestor_are_protected(self):
        module = self.implementation()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.source(root)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            existing = root / 'existing'
            existing.mkdir()
            (existing / 'user-data').write_bytes(b'preserve')
            app = root / 'third.app/Contents'
            app.mkdir(parents=True)
            for output in [existing, app / 'new-slot']:
                with self.subTest(output=output), self.assertRaises(ValueError):
                    module.repack(source, output, {'new.json': b'{}'}, digest)
            self.assertEqual((existing / 'user-data').read_bytes(), b'preserve')
            self.assertFalse((app / 'new-slot').exists())


if __name__ == '__main__':
    unittest.main()
