"""The new Claude UI manifest must bind exact official archives and contexts."""

import json
import plistlib
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import verify_ui_423118 as review
import patch_whitelist_423118 as whitelist
from upstream_patch_lib import BASE_LANGUAGE_LIST


class Reviewed423118UITests(unittest.TestCase):
    def test_latest_reviewed_manifest_and_both_locale_gates_are_version_bound(self):
        latest = ROOT / "resources/candidates/claude-desktop-4.23118.1"
        manifest = json.loads((latest / "hardcoded-ui-reviewed.json").read_text())
        reviewed = review.validate_manifest(manifest, version="4.23118.1")
        self.assertEqual(len(reviewed), 6)
        self.assertEqual(
            sum(rule["expected"] for item in reviewed.values() for rule in item["rules"]),
            26,
        )
        self.assertEqual(len(whitelist.load_reviewed("4.23118.1")), 2)
        with self.assertRaisesRegex(ValueError, "version"):
            review.validate_manifest(manifest, version="4.23118.0")

    def test_manifest_is_versioned_and_rejects_unsafe_or_empty_rule_sets(self):
        manifest = json.loads((ROOT / "resources/candidates/claude-desktop-4.23118.0/hardcoded-ui-reviewed.json").read_text())
        reviewed = review.validate_manifest(manifest)
        self.assertEqual(len(reviewed), 6)
        self.assertEqual(sum(rule["expected"] for item in reviewed.values() for rule in item["rules"]), 26)
        bad = json.loads(json.dumps(manifest))
        bad["files"]["app.asar/../../escape.js"] = bad["files"].pop(next(iter(bad["files"])))
        with self.assertRaisesRegex(ValueError, "unsafe"):
            review.validate_manifest(bad)
        bad = json.loads(json.dumps(manifest))
        bad["files"][next(iter(bad["files"]))]["rules"] = []
        with self.assertRaisesRegex(ValueError, "rules"):
            review.validate_manifest(bad)
        bad = json.loads(json.dumps(manifest))
        bad["version"] = "4.4212.0"
        with self.assertRaisesRegex(ValueError, "version"):
            review.validate_manifest(bad)

    def test_only_new_translations_are_added_without_rewriting_existing_terminology(self):
        existing = json.loads((ROOT / "resources/ui-reviewed-zh-CN.json").read_text())
        added = json.loads((ROOT / "resources/candidates/claude-desktop-4.23118.0/hardcoded-ui-zh-CN.json").read_text())
        self.assertFalse(set(existing) & set(added))
        self.assertEqual(existing["Default effort level"], "默认思考强度")
        self.assertEqual(added["Collapse all"], "全部收起")
        self.assertEqual(added["Open System Settings"], "打开系统设置")
        self.assertEqual(added["I'll clean up sessions myself"], "我会自行清理会话")

    def test_unknown_archive_stops_before_any_ui_patch_or_app_write(self):
        with tempfile.TemporaryDirectory() as raw:
            app = Path(raw) / "Claude.app"
            contents = app / "Contents"
            resources = contents / "Resources"
            resources.mkdir(parents=True)
            (contents / "Info.plist").write_bytes(plistlib.dumps({
                "CFBundleIdentifier": "com.anthropic.claudefordesktop",
                "CFBundleShortVersionString": "4.23118.0",
            }))
            (resources / "app.asar").write_bytes(b"unknown archive")
            before = sorted(p.relative_to(app).as_posix() for p in app.rglob("*") if p.is_file())
            with patch.object(review, "require_official_signature", return_value=None):
                with self.assertRaisesRegex(ValueError, "fingerprint"):
                    review.verify(app)
            self.assertEqual(before, sorted(p.relative_to(app).as_posix()
                                           for p in app.rglob("*") if p.is_file()))
            self.assertEqual((resources / "app.asar").read_bytes(), b"unknown archive")

    def test_both_locale_gates_must_match_signed_source_before_chinese_preview(self):
        before = (BASE_LANGUAGE_LIST + '];const locale="en-US";').encode()
        after = (BASE_LANGUAGE_LIST + ',"zh-CN"];const locale="en-US";').encode()
        spec = {"sha256": hashlib.sha256(before).hexdigest(),
                "patched_sha256": hashlib.sha256(after).hexdigest(), "expected": 1}
        names = ["assets/v1/shared-67.js", "assets/v1/shared-99.js"]
        files = {name: before for name in names}
        expected = {name: spec for name in names}
        self.assertEqual(review.verify_locale_gates(files, expected), 2)
        files[names[1]] = b"changed upstream language guard"
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            review.verify_locale_gates(files, expected)


if __name__ == "__main__":
    unittest.main()
