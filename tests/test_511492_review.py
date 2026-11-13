"""A new Claude version must not inherit translated bytes by ID alone."""

import importlib.util
import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest import mock
import json
import hashlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


class NewClaudeVersionReviewTests(unittest.TestCase):
    def planner(self):
        self.assertIsNotNone(importlib.util.find_spec("review_claude_511492"),
                             "new official version needs its own non-installing review")
        from review_claude_511492 import plan_catalog
        return plan_catalog

    def test_unchanged_english_reuses_id_but_changed_english_is_blocked(self):
        plan = self.planner()(
            {"same": "Open", "changed": "Retry", "removed": "Delete"},
            {"same": "Open", "changed": "Retry now", "added": "New"},
            {"same": "打开", "changed": "重试", "removed": "删除"},
            {}, {},
        )
        self.assertEqual(plan["reused"], {"same": "打开"})
        self.assertEqual(plan["changed_ids"], ["changed"])
        self.assertEqual(plan["removed_ids"], ["removed"])
        self.assertEqual(plan["pending_ids"], ["added", "changed"])
        self.assertEqual(plan["status"], "review_required_not_installable")

    def test_official_and_unique_memory_are_proposals_not_installable_translations(self):
        plan = self.planner()(
            {"old": "Open"},
            {"old": "Open", "official": "New", "memory": "Open", "ambiguous": "Try", "missing": "Else"},
            {"old": "打开"},
            {"official": "新增"}, {"Open": {"打开"}, "Try": {"尝试", "试试"}},
        )
        self.assertEqual(plan["reused"], {"old": "打开"})
        self.assertEqual(plan["official_proposals"], {"official": "新增"})
        self.assertEqual(plan["memory_proposals"], {"memory": "打开"})
        self.assertEqual(plan["ambiguous_ids"], ["ambiguous"])
        self.assertEqual(plan["pending_ids"], ["ambiguous", "missing"])
        self.assertNotIn("official", plan["reused"])
        self.assertNotIn("memory", plan["reused"])
        self.assertEqual(plan["status"], "review_required_not_installable")

    def test_cli_json_is_not_polluted_by_source_verifier_progress(self):
        module = __import__("review_claude_511492")
        output = io.StringIO()
        def noisy_review(_app):
            print("Verified source archive")
            return {"status": "review_required_not_installable"}
        with mock.patch.object(sys, "argv", ["review_claude_511492.py"]), \
             mock.patch.object(module, "review", side_effect=noisy_review), \
             contextlib.redirect_stdout(output):
            self.assertEqual(module.main(), 0)
        self.assertTrue(output.getvalue().startswith("{"),
                        "progress text must not corrupt the machine-readable JSON result")
        self.assertEqual(json.loads(output.getvalue()),
                         {"status": "review_required_not_installable"})

    def test_new_locale_gate_candidates_require_unique_paths_and_both_aliases(self):
        module = __import__("review_claude_511492")
        candidate = getattr(module, "gate_candidates", None)
        self.assertIsNotNone(candidate, "new version needs exact dual-alias gate review")
        old = {"assets/v1/shared-25-OLD.js": {"expected": 1}}
        raw = b'var oq=["en-US","de-DE","fr-FR","ko-KR","ja-JP","es-419","es-ES","it-IT","hi-IN","pt-BR","id-ID"];function sq(e){return oq.includes(e)}'
        paths = ["assets/v1/shared-25-NEW.js"]
        report = candidate(old, paths, lambda _path: raw)
        spec = report[paths[0]]
        expected = raw.replace(b'"id-ID"]', b'"id-ID","zh-CN","zh-Hans"]')
        self.assertEqual(spec["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(spec["patched_sha256"], hashlib.sha256(expected).hexdigest())
        self.assertEqual(spec["locales"], ["zh-CN", "zh-Hans"])
        self.assertEqual(spec["status"], "review_required_not_installable")
        with self.assertRaises(ValueError):
            candidate(old, paths + ["assets/v1/shared-25-OTHER.js"], lambda _path: raw)
        with self.assertRaises(ValueError):
            candidate(old, [], lambda _path: raw)

    def test_gate_with_same_language_array_but_no_membership_check_is_rejected(self):
        module = __import__("review_claude_511492")
        old = {"assets/v1/shared-25-OLD.js": {"expected": 1}}
        raw = b'var oq=["en-US","de-DE","fr-FR","ko-KR","ja-JP","es-419","es-ES","it-IT","hi-IN","pt-BR","id-ID"];function sq(e){return true}'
        with self.assertRaisesRegex(ValueError, "context"):
            module.gate_candidates(old, ["assets/v1/shared-25-NEW.js"], lambda _path: raw)

    def test_pinned_new_gate_review_does_not_advertise_installable_profile(self):
        path = Path(__file__).resolve().parents[1] / (
            "resources/candidates/claude-desktop-5.11492.2/language-whitelist-review.json")
        value = json.loads(path.read_text())
        self.assertEqual(value["version"], "5.11492.2")
        self.assertEqual(value["status"], "review_required_not_installable")
        self.assertEqual(len(value["files"]), 3)
        self.assertTrue(all(item["status"] == "review_required_not_installable"
                            for item in value["files"].values()))
        self.assertNotIn("install_profile", value)
        self.assertNotIn("approved", value)

    def test_draft_translation_binds_source_placeholder_and_noninstallable_status(self):
        module = __import__("review_claude_511492")
        validate = getattr(module, "validate_desktop_draft", None)
        self.assertIsNotNone(validate, "new-version authored draft needs strict source validation")
        archives = {"Contents/Resources/app.asar": "a" * 64,
                    "Contents/Resources/ion-dist.asar": "b" * 64}
        source = {"new": "Run on {host}."}
        draft = {"version": "5.11492.2", "status": "draft_not_installable",
                 "source_archives_sha256": archives,
                 "source_english_sha256": {"new": hashlib.sha256(source["new"].encode()).hexdigest()},
                 "translations": {"new": "在 {host} 上运行。"}}
        result = validate(draft, source, {"version": "5.11492.2", "archives": archives})
        self.assertEqual(result, {"status": "review_required_not_installable", "draft_count": 1})
        with self.assertRaises(ValueError):
            validate(draft, {"new": "Run here instead."},
                     {"version": "5.11492.2", "archives": archives})
        with self.assertRaises(ValueError):
            validate({**draft, "translations": {"new": "在这里运行。"}}, source,
                     {"version": "5.11492.2", "archives": archives})
        with self.assertRaises(ValueError):
            validate({**draft, "status": "installable"}, source,
                     {"version": "5.11492.2", "archives": archives})

    def test_frontend_draft_is_restricted_to_pending_ids_and_correct_group(self):
        module = __import__("review_claude_511492")
        validate = getattr(module, "validate_frontend_draft", None)
        self.assertIsNotNone(validate, "frontend drafts need a group/pending-ID boundary")
        archives = {"Contents/Resources/app.asar": "a" * 64,
                    "Contents/Resources/ion-dist.asar": "b" * 64}
        english = {"pending": "Open {file}"}
        draft = {"version": "5.11492.2", "group": "frontend",
                 "status": "draft_not_installable", "source_archives_sha256": archives,
                 "source_english_sha256": {"pending": hashlib.sha256(english["pending"].encode()).hexdigest()},
                 "translations": {"pending": "打开 {file}"}}
        identity = {"version": "5.11492.2", "archives": archives}
        self.assertEqual(validate(draft, english, identity, {"pending"})["draft_count"], 1)
        with self.assertRaises(ValueError):
            validate({**draft, "group": "desktop"}, english, identity, {"pending"})
        with self.assertRaises(ValueError):
            validate(draft, english, identity, {"another"})


if __name__ == "__main__":
    unittest.main()
