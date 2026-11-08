"""A version-bound smoke app must never replace the installed Claude app."""

import hashlib
import json
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_smoke_423118 as smoke
from upstream_patch_lib import build_asar, read_asar_entry, read_asar_header


class SmokeBuilderTests(unittest.TestCase):
    def test_destination_requires_new_path_on_mounted_external_build_root(self):
        with tempfile.TemporaryDirectory() as raw:
            mount = Path(raw) / "external"
            mount.mkdir()
            stage = mount / "review/Claude.app"
            with patch.object(smoke.os.path, "ismount", return_value=True):
                self.assertEqual(smoke.checked_destination(stage, mount), stage.resolve(strict=False))
                with self.assertRaisesRegex(ValueError, "outside"):
                    smoke.checked_destination(Path("/Applications/Claude.app"), mount)
                stage.parent.mkdir()
                stage.mkdir()
                with self.assertRaisesRegex(ValueError, "already exists"):
                    smoke.checked_destination(stage, mount)
                alias = mount / "alias"
                (mount / "other").mkdir()
                alias.symlink_to(mount / "other")
                with self.assertRaisesRegex(ValueError, "redirected"):
                    smoke.checked_destination(alias / "Claude.app", mount)
            with patch.object(smoke.os.path, "ismount", return_value=False):
                with self.assertRaisesRegex(ValueError, "unmounted"):
                    smoke.checked_destination(mount / "other/Claude.app", mount)

    def test_ui_patches_preflight_both_archives_before_writing_any_js(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            roots = {"app.asar": root / "app", "ion-dist.asar": root / "ion"}
            sources = {
                "app.asar/picker.js": 'f({label:"Default model",type:"Default model"})',
                "ion-dist.asar/rail.js": 'f({children:"New chat",icon:"New chat"})',
            }
            translations = {"Default model": "默认模型", "New chat": "新聊天"}
            files = {}
            for name, source in sources.items():
                bundle, relative = name.split("/", 1)
                target = roots[bundle] / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source)
                rules = [{"context": "label" if bundle == "app.asar" else "children",
                          "source": "Default model" if bundle == "app.asar" else "New chat",
                          "expected": 1}]
                patched = source.replace('label:"Default model"', 'label:"默认模型"').replace(
                    'children:"New chat"', 'children:"新聊天"')
                files[name] = {"sha256": hashlib.sha256(source.encode()).hexdigest(),
                               "patched_sha256": hashlib.sha256(patched.encode()).hexdigest(),
                               "rules": rules}
            result = smoke.prepare_ui_edits(roots, {"version": "4.23118.0", "files": files},
                                            translations)
            self.assertEqual(len(result), 2)
            self.assertTrue(all((roots[name.split('/')[0]] / name.split('/', 1)[1]).read_text()
                                == source for name, source in sources.items()))
            (roots["ion-dist.asar"] / "rail.js").write_text("upstream changed")
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                smoke.prepare_ui_edits(roots, {"version": "4.23118.0", "files": files}, translations)
            self.assertEqual((roots["app.asar"] / "picker.js").read_text(), sources["app.asar/picker.js"])

    def test_unknown_official_source_never_copies_or_opens_a_candidate(self):
        with tempfile.TemporaryDirectory() as raw:
            mount = Path(raw) / "external"
            mount.mkdir()
            source = Path(raw) / "unknown-official/Claude.app"
            target = mount / "review/Claude.app"
            with patch.object(smoke.os.path, "ismount", return_value=True), patch.object(
                    smoke.combined, "verify", side_effect=ValueError("official signature changed")), patch.object(
                    smoke.subprocess, "run") as command:
                with self.assertRaisesRegex(ValueError, "official signature"):
                    smoke.build(source, target, mount)
                command.assert_not_called()
                self.assertFalse(target.exists())

    def test_copied_official_archives_are_rechecked_before_unpacking(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            mount = root / "external"
            mount.mkdir()
            source = root / "official/Claude.app"
            resources = source / "Contents/Resources"
            resources.mkdir(parents=True)
            (resources / "app.asar").write_bytes(b"official archive app")
            (resources / "ion-dist.asar").write_bytes(b"official archive frontend")
            stage = mount / "review/Claude.app"

            def copied(argv, **_kwargs):
                if argv[:2] != ['/bin/cp', '-cR']:
                    raise AssertionError('unexpected external command')
                target = Path(argv[-1]) / 'Contents/Resources'
                target.mkdir(parents=True)
                (target / 'app.asar').write_bytes(b"changed archive after source verification")
                (target / 'ion-dist.asar').write_bytes(b"official archive frontend")

            with patch.object(smoke.os.path, 'ismount', return_value=True), patch.object(
                    smoke.combined, 'verify', return_value={
                        'version': '4.23118.0', 'signed_official_source': True}), patch.object(
                    smoke.subprocess, 'run', side_effect=copied), patch.object(
                    smoke.repair.patch, 'prepare_frontend_target') as unpack:
                with self.assertRaisesRegex(ValueError, 'copied source'):
                    smoke.build(source, stage, mount)
                unpack.assert_not_called()

    def test_versioned_frontend_stager_preserves_official_loose_fallback_html(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            app = root / 'Claude.app'
            resources = app / 'Contents/Resources'
            vendor_dir = resources / 'ion-dist'
            vendor_dir.mkdir(parents=True)
            loose = vendor_dir / 'index.html'
            loose.write_text('vendor legacy-window fallback, do not edit')
            (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
                'CFBundleIdentifier': 'com.anthropic.claudefordesktop',
            }))
            source = root / 'source'
            (source / 'i18n').mkdir(parents=True)
            (source / 'i18n/en-US.json').write_text('{"old":"source"}')
            (source / 'assets/v1').mkdir(parents=True)
            (source / 'assets/v1/index-BtOreTmB.js').write_text('export default 1;')
            archive = resources / 'ion-dist.asar'
            build_asar(source, archive)
            prepared = smoke.prepare_frontend_stage(
                app, expected_fallback_sha256=hashlib.sha256(loose.read_bytes()).hexdigest())
            self.assertNotEqual(prepared['work'], vendor_dir)
            self.assertEqual(loose.read_text(), 'vendor legacy-window fallback, do not edit')
            (prepared['work'] / 'i18n/en-US.json').write_text('{"new":"smoke"}')
            smoke.finalize_frontend_stage(app, prepared)
            self.assertEqual(loose.read_text(), 'vendor legacy-window fallback, do not edit')
            size, _, header = read_asar_header(archive.read_bytes(), archive)
            self.assertEqual(json.loads(read_asar_entry(archive.read_bytes(), size, header,
                                                      'i18n/en-US.json')), {"new": "smoke"})
            self.assertFalse(prepared['work'].exists())


if __name__ == "__main__":
    unittest.main()
