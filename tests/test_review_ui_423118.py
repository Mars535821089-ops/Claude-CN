"""The new Claude UI manifest must bind exact official archives and contexts."""

import json
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import verify_ui_423118 as review


class Reviewed423118UITests(unittest.TestCase):
    def test_manifest_is_versioned_and_rejects_unsafe_or_empty_rule_sets(self):
        manifest = json.loads((ROOT / "resources/candidates/claude-desktop-4.23118.0/hardcoded-ui-reviewed.json").read_text())
        reviewed = review.validate_manifest(manifest)
        self.assertEqual(len(reviewed), 5)
        self.assertEqual(sum(rule["expected"] for item in reviewed.values() for rule in item["rules"]), 8)
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


if __name__ == "__main__":
    unittest.main()
