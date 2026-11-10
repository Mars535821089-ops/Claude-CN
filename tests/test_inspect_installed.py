"""A signed app is not a localized app merely because its version is current."""

import importlib
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from upstream_patch_lib import build_asar

try:
    inspector = importlib.import_module("inspect_installed")
except ModuleNotFoundError:
    inspector = None


class InstalledLocalizationTests(unittest.TestCase):
    def fixture(self, base: Path, *, desktop: bool, frontend: bool, dynamic: bool) -> Path:
        app = base / "Claude.app"
        resources = app / "Contents/Resources"
        resources.mkdir(parents=True)
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps({
            "CFBundleIdentifier": "com.anthropic.claudefordesktop",
            "CFBundleShortVersionString": "4.23118.1",
        }))
        source = base / "desktop"
        source.mkdir()
        if desktop:
            (source / "resources/i18n").mkdir(parents=True)
            (source / "resources/i18n/zh-CN.json").write_text('{"label":"中文"}')
        (source / "index.js").write_text("module.exports = true")
        build_asar(source, resources / "app.asar")
        source = base / "frontend"
        source.mkdir()
        (source / "i18n/dynamic").mkdir(parents=True)
        if frontend:
            (source / "i18n/zh-CN.json").write_text('{"label":"中文"}')
        if dynamic:
            (source / "i18n/dynamic/zh-CN.json").write_text('{"label":"中文"}')
        build_asar(source, resources / "ion-dist.asar")
        return app

    def test_official_signed_app_without_chinese_resources_is_not_accepted(self):
        self.assertIsNotNone(inspector, "read-only installed-app inspector is required")
        with tempfile.TemporaryDirectory() as raw:
            app = self.fixture(Path(raw), desktop=False, frontend=False, dynamic=False)
            with patch.object(inspector, "verify_signature", return_value=True):
                result = inspector.inspect_app(app)
            self.assertEqual(result["status"], "unlocalized")
            self.assertEqual(result["version"], "4.23118.1")
            self.assertFalse(result["visual_ui_verified"])

    def test_partial_bundle_is_incomplete_and_complete_bundle_is_not_visual_proof(self):
        self.assertIsNotNone(inspector, "read-only installed-app inspector is required")
        for present, expected in (((True, False, True), "incomplete"),
                                  ((True, True, True), "resources_present_ui_unverified")):
            with self.subTest(present=present), tempfile.TemporaryDirectory() as raw:
                app = self.fixture(Path(raw), desktop=present[0], frontend=present[1], dynamic=present[2])
                with patch.object(inspector, "verify_signature", return_value=True):
                    result = inspector.inspect_app(app)
                self.assertEqual(result["status"], expected)
                self.assertFalse(result["visual_ui_verified"])

    def test_invalid_signature_or_bundle_identity_never_reports_resources_present(self):
        self.assertIsNotNone(inspector, "read-only installed-app inspector is required")
        with tempfile.TemporaryDirectory() as raw:
            app = self.fixture(Path(raw), desktop=True, frontend=True, dynamic=True)
            with patch.object(inspector, "verify_signature", return_value=False):
                self.assertEqual(inspector.inspect_app(app)["status"], "invalid_app")
            plist = app / "Contents/Info.plist"
            details = plistlib.loads(plist.read_bytes())
            details["CFBundleIdentifier"] = "example.wrong-app"
            plist.write_bytes(plistlib.dumps(details))
            with patch.object(inspector, "verify_signature", return_value=True):
                self.assertEqual(inspector.inspect_app(app)["status"], "invalid_app")


if __name__ == "__main__":
    unittest.main()
