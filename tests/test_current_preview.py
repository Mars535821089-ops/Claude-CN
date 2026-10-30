import contextlib
import io
import json
import plistlib
import subprocess
import sys
import tempfile
import urllib.error
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import official_release
import preview_current as preview
import update_guard


class OfficialReleaseTests(unittest.TestCase):
    def payload(self, version="4.4212.0"):
        return {"currentRelease": version, "releases": [
            {"version": version, "updateTo": {
                "version": version, "pub_date": "2026-10-29T06:13:21.454Z",
                "url": f"https://downloads.claude.ai/releases/darwin/universal/{version}/Claude.zip",
            }},
        ]}

    def test_numeric_version_order_and_public_feed_identity(self):
        release = official_release.validate_release(self.payload())
        self.assertEqual(official_release.compare_latest("4.29.0", release)["status"],
                         "update_available")
        self.assertEqual(official_release.compare_latest("4.4212.0", release)["status"], "current")
        self.assertEqual(official_release.compare_latest("4.5000.0", release)["status"],
                         "installed_ahead_of_public_feed")

    def test_missing_ambiguous_mismatched_or_foreign_artifacts_stop(self):
        for field in ("version", "url"):
            payload = self.payload()
            payload["releases"][0]["updateTo"][field] = "https://example.com/Claude.zip"
            with self.assertRaises(ValueError):
                official_release.validate_release(payload)

        payload = self.payload()
        payload["releases"] *= 2
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            official_release.validate_release(payload)
        for url in [
            "http://downloads.claude.ai/releases/darwin/universal/4.4212.0/Claude.zip",
            "https://downloads.claude.ai.example.com/releases/darwin/universal/4.4212.0/Claude.zip",
            "https://downloads.claude.ai/releases/darwin/universal/4.4212.0/../Claude.zip",
            "https://downloads.claude.ai/releases/darwin/universal/4.4212.0/%2e%2e/Claude.zip",
        ]:
            payload = self.payload()
            payload["releases"][0]["updateTo"]["url"] = url
            with self.assertRaises(ValueError):
                official_release.validate_release(payload)

    def test_network_failure_does_not_return_a_success_shaped_latest_version(self):
        with patch.object(official_release.urllib.request, "urlopen",
                          side_effect=urllib.error.URLError("offline")):
            with self.assertRaises(urllib.error.URLError):
                official_release.fetch_latest()


class CurrentPreviewTests(unittest.TestCase):
    def test_official_current_and_legacy_id_precedence_and_no_english_fallback(self):
        pack = preview.merge_catalog(
            {"official": "Open", "new": "Allow", "old": "Copy", "gap": "Unknown"},
            {"official": "打开"}, {"official": "旧译", "old": "复制"},
            {"Open": "新译", "Allow": "允许"}, {},
        )
        self.assertEqual(pack["translations"], {"official": "打开", "new": "允许", "old": "复制"})
        self.assertEqual(pack["missing"], {"gap": "Unknown"})
        self.assertEqual(pack["provenance"]["official"], "official_local")

    def test_exact_source_reuse_rejects_ambiguous_legacy_translations(self):
        result = preview.merge_catalog(
            {"a": "Same", "b": "Same", "new": "Same", "c": "Other", "next": "Other"},
            {}, {"a": "相同", "b": "同样", "c": "其他"}, {}, {},
        )
        self.assertEqual(result["missing"], {"new": "Same"})
        self.assertEqual(result["provenance"]["next"], "legacy_exact_text")
        self.assertEqual(result["translations"]["next"], "其他")

    def test_product_name_is_explicitly_preserved_not_counted_as_translated(self):
        result = preview.merge_catalog({"brand": "Claude Code"}, {}, {}, {}, {})
        self.assertEqual(result["translations"]["brand"], "Claude Code")
        self.assertEqual(result["provenance"]["brand"], "reviewed_preserved")

    def test_existing_preview_and_app_output_paths_stop_before_identity_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "Claude.app"
            app.mkdir()
            with patch.object(preview, "verify_identity") as identity:
                for output in (app, app / "new-output"):
                    with self.assertRaises(ValueError):
                        preview.prepare(app, output, False)
                identity.assert_not_called()
            link = root / "link"
            link.symlink_to(app, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "inside the application"):
                preview.prepare(app, link / "new-output", False)

    def test_wrong_version_and_unknown_publisher_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory)
            (app / "Contents").mkdir()
            info = {"CFBundleShortVersionString": "4.4212.0", "CFBundleVersion": "4.4212.0",
                    "CFBundleIdentifier": "com.anthropic.claudefordesktop"}
            (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
            profile = {"version": "4.4212.0", "build": "4.4212.0", "archives": {}}
            response = subprocess.CompletedProcess([], 0, stdout="", stderr="TeamIdentifier=OTHER")
            with patch.object(preview.subprocess, "run", return_value=response):
                with self.assertRaisesRegex(ValueError, "official Anthropic"):
                    preview.verify_identity(app, profile)
            profile["version"] = "4.29.0"
            with self.assertRaisesRegex(ValueError, "reviewed version"):
                preview.verify_identity(app, profile)

    def test_failed_integrity_check_is_not_accepted_as_an_official_app(self):
        signed = "TeamIdentifier=Q6L2SF6YDW\n" \
                 "Authority=Developer ID Application: Anthropic PBC (Q6L2SF6YDW)\n" \
                 "Identifier=com.anthropic.claudefordesktop\n"
        with patch.object(update_guard.subprocess, "run", side_effect=[
            subprocess.CompletedProcess([], 0, stdout="", stderr=signed),
            subprocess.CalledProcessError(1, ["codesign"], stderr="modified resource"),
        ]):
            with self.assertRaisesRegex(ValueError, "modified resource"):
                update_guard.require_official_signature(Path("/unused"))

    def test_reviewed_and_preserved_lists_cannot_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base, extra, preserved = (root / name for name in ("base.json", "extra.json", "keep.json"))
            base.write_text(json.dumps({"Allow": "允许"}))
            extra.write_text(json.dumps({"Open": "打开"}))
            preserved.write_text(json.dumps(["API"]))
            with (patch.object(preview, "REVIEWED", base),
                  patch.object(preview, "ADDITIONAL", extra),
                  patch.object(preview, "PRESERVED", preserved)):
                translations, keep = preview.load_reviewed()
                self.assertEqual(translations, {"Allow": "允许", "Open": "打开"})
                self.assertEqual(keep, {"Claude Code", "API"})
                preserved.write_text(json.dumps(["Open"]))
                with self.assertRaisesRegex(ValueError, "translated current preserved"):
                    preview.load_reviewed()

    def test_invalid_merged_icu_never_publishes_a_preview_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app, output, profile = root / "Claude.app", root / "review", root / "profile.json"
            app.mkdir()
            pack = {"en": {"id": "Value {arg}"}, "official": {}}
            profile.write_text(json.dumps({
                "version": "4.4212.0", "catalogs": {
                    group: {"en": update_guard.catalog_fingerprint(pack["en"]),
                            "official_zh_Hans": update_guard.catalog_fingerprint({})}
                    for group in preview.PATHS
                },
            }))
            with (
                patch.object(preview, "PROFILE", profile),
                patch.object(preview, "verify_identity", return_value={"version": "4.4212.0"}),
                patch.object(preview, "load_catalogs", return_value={
                    group: pack for group in preview.PATHS}),
                patch.object(preview, "load_reviewed", return_value=({"Value {arg}": "值"}, set())),
                patch.object(preview.repair, "json_file", return_value={}),
            ):
                with self.assertRaises(subprocess.CalledProcessError) as raised:
                    preview.prepare(app, output, False)
            self.assertIn("mismatch", raised.exception.stdout)
            self.assertFalse(output.exists())
            self.assertEqual(list(app.iterdir()), [])

    def test_new_review_dictionary_has_unique_keys_and_preserves_icu_and_template_arguments(self):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                self.assertNotIn(key, result, key)
                result[key] = value
            return result
        dictionary = {}
        for path in (preview.REVIEWED, preview.ADDITIONAL):
            entries = json.loads(path.read_text(), object_pairs_hook=unique)
            self.assertFalse(dictionary.keys() & entries.keys())
            dictionary.update(entries)
        preserved = json.loads(preview.PRESERVED.read_text())
        self.assertEqual(len(preserved), len(set(preserved)))
        self.assertFalse(set(preserved) & dictionary.keys())
        for source, target in dictionary.items():
            self.assertTrue(target)
            self.assertNotEqual(source, target)
            self.assertEqual(source.count("{…}"), target.count("{…}"), source)
        # Dynamic JavaScript template tokens are not ICU syntax.
        catalog = {str(index): {"source": source, "target": target}
                   for index, (source, target) in enumerate(dictionary.items()) if "{…}" not in source}
        result = subprocess.run(
            ["node", str(preview.ROOT / "scripts/audit_messages.cjs")],
            input=json.dumps({"catalogs": {"reviewed": {
                "en": {key: pair["source"] for key, pair in catalog.items()},
                "zh": {key: pair["target"] for key, pair in catalog.items()},
                "strictSelections": True,
            }}}), capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_committed_current_ui_manifest_is_review_only_and_uses_reviewed_sources(self):
        manifest = json.loads((preview.repair.RESOURCES / "current-ui-patch-preview.json").read_text())
        profile = json.loads(preview.PROFILE.read_text())
        dictionary, _ = preview.load_reviewed()
        dictionary = {**preview.repair.json_file(
            preview.repair.RESOURCES / "ui-reviewed-zh-CN.json"), **dictionary}
        self.assertEqual(manifest["version"], profile["version"])
        self.assertIs(manifest["preview_only"], True)
        self.assertTrue(manifest["files"])
        for file, spec in manifest["files"].items():
            self.assertIn(file.split("/")[0], {"app.asar", "ion-dist.asar"})
            self.assertNotIn("..", Path(file).parts)
            for entry in [spec, *spec.get("inline_scripts", {}).values()]:
                self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")
                self.assertRegex(entry["patched_sha256"], r"^[0-9a-f]{64}$")
                for rule in entry["rules"]:
                    self.assertIn(rule["source"], dictionary)
                    self.assertGreater(rule["expected"], 0)
                    self.assertNotIn(rule["context"], {"message", "errorMessage"})

    def test_strict_icu_check_rejects_lost_select_branches_and_exact_plural_cases(self):
        en = {"select": "{role, select, admin {Admin} other {User}}",
              "plural": "{n, plural, =0 {None} one {One} other {# items}}"}
        zh = {"select": "{role, select, other {用户}}",
              "plural": "{n, plural, other {# 项}}"}
        result = subprocess.run(
            ["node", str(preview.ROOT / "scripts/audit_messages.cjs")],
            input=json.dumps({"catalogs": {"invalid": {
                "en": en, "zh": zh, "strictSelections": True,
            }}}), capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(json.loads(result.stdout)["errors"]), 2)


class MonitorRegressionTests(unittest.TestCase):
    def test_app_update_during_inspection_stops_before_a_snapshot_is_returned(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory)
            with (
                patch.object(update_guard, "require_official_signature"),
                patch.object(update_guard.repair.patch, "bundle_version", return_value="4.4212.0"),
                patch.object(update_guard, "read_source_catalogs", return_value={}),
                patch.object(update_guard, "bundle_identity", side_effect=[
                    {"version": "4.4212.0", "archives": {"app.asar": "old"}},
                    {"version": "4.4212.0", "archives": {"app.asar": "new"}},
                ]),
            ):
                with self.assertRaisesRegex(ValueError, "changed during inspection"):
                    update_guard.make_snapshot(app)

    def test_monitor_log_uses_resolved_state_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            state = home / "state"
            with (
                patch.object(Path, "home", return_value=home),
                patch.object(update_guard, "load_baseline", return_value={"snapshot": {}}),
                patch.object(update_guard.subprocess, "run",
                             return_value=subprocess.CompletedProcess([], 0, "", "")),
            ):
                path = update_guard.install_monitor(home / "Claude.app", state)
            data = plistlib.loads(path.read_bytes())
            self.assertEqual(data["StandardOutPath"], str(state.resolve() / "update-monitor.log"))
            self.assertEqual(data["StandardErrorPath"], data["StandardOutPath"])

    def test_unchanged_but_unsupported_version_still_requires_attention(self):
        report = {"status": "unchanged", "installer_compatible": False,
                  "current": {"version": "4.4212.0"}, "message": "Unsupported"}
        with (
            patch.object(sys, "argv", ["update_guard.py", "--json"]),
            patch.object(update_guard, "inspect", return_value=report),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(update_guard.main(), 2)
