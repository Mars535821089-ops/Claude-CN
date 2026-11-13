"""A new Claude version must not inherit translated bytes by ID alone."""

import importlib.util
import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest import mock
import json

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


if __name__ == "__main__":
    unittest.main()
