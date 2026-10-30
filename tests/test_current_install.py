import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))


class CurrentInstallTests(unittest.TestCase):
    def module(self):
        spec = importlib.util.find_spec('install_current')
        self.assertIsNotNone(spec, 'Current-version installer is missing')
        import install_current
        return install_current

    def test_preview_identity_and_zero_gaps_are_required(self):
        m = self.module()
        profile = json.loads(m.preview.PROFILE.read_text())
        report = {'installed': {'version': profile['version'], 'build': profile['build'],
                               'archives': profile['archives']},
                  'catalogs': {g: {'unreviewed': 0} for g in m.preview.PATHS}}
        m.check_report(report, profile)
        report['catalogs']['frontend']['unreviewed'] = 1
        with self.assertRaisesRegex(ValueError, 'unreviewed'):
            m.check_report(report, profile)
        report['catalogs']['frontend']['unreviewed'] = 0
        report['installed']['version'] = 'unknown'
        with self.assertRaisesRegex(ValueError, 'identity'):
            m.check_report(report, profile)

    def test_reviewed_file_tampering_and_symlinks_refused(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            file = root / 'x.js'; file.write_text('const x="Allow";')
            spec = {'sha256': m.digest(file.read_bytes()), 'patched_sha256': 'unused', 'rules': []}
            self.assertEqual(m.checked_source(root, 'x.js', spec), file)
            file.write_text('changed')
            with self.assertRaisesRegex(ValueError, 'fingerprint'):
                m.checked_source(root, 'x.js', spec)
            (root / 'link.js').symlink_to(file)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                m.checked_source(root, 'link.js', spec)
            with self.assertRaisesRegex(ValueError, 'Unsafe'):
                m.checked_source(root, '../outside', spec)

    def test_install_verification_failure_restores_app_and_locales(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); app = root/'Claude.app'; stage=root/'stage.app'; backups=root/'backup'
            app.mkdir(); stage.mkdir(); (app/'identity').write_text('official'); (stage/'identity').write_text('patched')
            checks = [None, RuntimeError('verification failed')]
            with patch.object(m, 'verify_stage', side_effect=checks), patch.object(m.preview, 'verify_identity'), patch.object(m.repair, 'quit_and_wait'), patch.object(m.repair, 'update_locale', return_value={'config': b'old'}) as locale, patch.object(m.repair, 'restore_locale') as restore:
                with self.assertRaisesRegex(RuntimeError, 'verification failed'):
                    m.install(app, stage, root, backups)
            self.assertEqual((app/'identity').read_text(), 'official')
            restore.assert_called_once_with({'config': b'old'})
            locale.assert_called_once_with(root, 'zh-CN')


if __name__ == '__main__':
    unittest.main()
