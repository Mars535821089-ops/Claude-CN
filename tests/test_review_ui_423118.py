"""The new Claude UI manifest must bind exact official archives and contexts."""

import json
import plistlib
import hashlib
import sys
import os
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
    def test_latest_exact_accessibility_and_dom_text_contexts_are_reviewable(self):
        latest = ROOT / "resources/candidates/claude-desktop-4.23118.1"
        manifest = json.loads((latest / "hardcoded-ui-reviewed.json").read_text())
        one = next(iter(manifest["files"].values()))
        original = one["rules"]
        try:
            one["rules"] = [
                {"context": "aria-label", "source": "Read the transcript", "expected": 1},
                {"context": "placeholder", "source": "What's wrong with this response?", "expected": 1},
                {"context": "setAttribute:aria-label", "source": "Copy code", "expected": 1},
                {"context": "textContent", "source": "…and {…} more", "expected": 1},
            ]
            try:
                reviewed = review.validate_manifest(manifest, version="4.23118.1")
            except ValueError as error:
                if str(error) != "reviewed UI rules are invalid":
                    raise
                reviewed = None
            self.assertIsNotNone(reviewed, "reviewed accessibility and DOM text contexts must be supported")
            self.assertEqual(len(reviewed), 7)
        finally:
            one["rules"] = original

    def test_signed_local_arm64_official_variant_reuses_exact_reviewed_ui(self):
        location = os.environ.get("CLAUDE_OFFICIAL_VARIANT_APP")
        if not location:
            self.skipTest("signed local official variant is not supplied")
        app = Path(location)
        source = app / "Contents/Resources/app.asar"
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        self.assertEqual(before, "089b1780ff06a54a63773a10d6f866d451f0a6096e4107d38a50f21392f78d63")
        try:
            outcome = review.verify(app, version="4.23118.1")
        except ValueError as error:
            if str(error) != "reviewed official ASAR fingerprint changed":
                raise
            outcome = None
        self.assertIsNotNone(outcome, "a second exact signed archive variant must be reviewed")
        self.assertEqual(outcome["reviewed_replacements"], 72)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)

    def test_latest_reviewed_manifest_and_both_locale_gates_are_version_bound(self):
        latest = ROOT / "resources/candidates/claude-desktop-4.23118.1"
        manifest = json.loads((latest / "hardcoded-ui-reviewed.json").read_text())
        reviewed = review.validate_manifest(manifest, version="4.23118.1")
        self.assertEqual(len(reviewed), 7)
        self.assertEqual(
            sum(rule["expected"] for item in reviewed.values() for rule in item["rules"]),
            72,
        )
        self.assertEqual(len(whitelist.load_reviewed("4.23118.1")), 2)
        additions = json.loads((latest / "hardcoded-ui-zh-CN.json").read_text())
        self.assertEqual(additions["No structured data available."], "没有可用的结构化数据。")
        self.assertEqual(additions["Prompt Map"], "提示词映射")
        self.assertEqual(additions["Block type"], "区块类型")
        self.assertEqual(additions["What's wrong with this response?"], "这条回复有什么问题？")
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

    def test_latest_rejects_duplicate_or_partial_official_source_variants(self):
        latest = json.loads((ROOT / "resources/candidates/claude-desktop-4.23118.1/hardcoded-ui-reviewed.json").read_text())
        self.assertEqual(len(latest["source_variants"]), 1)
        for variants in ([latest["source_archives"]], [{"app.asar": "0" * 64}],
                         [{"app.asar": "0" * 64, "ion-dist.asar": "not-a-digest"}]):
            bad = json.loads(json.dumps(latest))
            bad["source_variants"] = variants
            with self.subTest(variants=variants), self.assertRaisesRegex(ValueError, "variant"):
                review.validate_manifest(bad, version="4.23118.1")

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
