"""Rebinding old drafts must never treat a new build as already localized."""
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from review_claude_5114925 import rebind_draft


class LatestClaudeDraftTests(unittest.TestCase):
    def test_exact_source_rebind_preserves_provisional_status_and_old_input(self):
        english = {"new": "Run on {host}."}
        draft = {"version": "5.11492.2", "status": "draft_not_installable",
                 "source_archives_sha256": {"app.asar": "old"},
                 "source_english_sha256": {"new": hashlib.sha256(english["new"].encode()).hexdigest()},
                 "translations": {"new": "在 {host} 上运行。"}}
        new = rebind_draft(draft, english, {"app.asar": "new"}, group="desktop")
        self.assertEqual(new["version"], "5.11492.5")
        self.assertEqual(new["status"], "draft_not_installable")
        self.assertEqual(new["source_archives_sha256"], {"app.asar": "new"})
        self.assertEqual(draft["version"], "5.11492.2")
        self.assertEqual(draft["source_archives_sha256"], {"app.asar": "old"})
        with self.assertRaises(ValueError):
            rebind_draft(draft, {"new": "Run elsewhere."}, {"app.asar": "new"}, group="desktop")
        with self.assertRaises(ValueError):
            rebind_draft({**draft, "status": "installable"}, english,
                         {"app.asar": "new"}, group="desktop")

    def test_frontend_group_cannot_be_dressed_as_desktop(self):
        english = {"new": "Open"}
        draft = {"version": "5.11492.2", "group": "frontend",
                 "status": "draft_not_installable", "source_archives_sha256": {},
                 "source_english_sha256": {"new": hashlib.sha256(b"Open").hexdigest()},
                 "translations": {"new": "打开"}}
        with self.assertRaises(ValueError):
            rebind_draft(draft, english, {}, group="desktop")
        result = rebind_draft(draft, english, {"ion-dist.asar": "new"}, group="frontend")
        self.assertEqual(result["group"], "frontend")
        self.assertEqual(result["status"], "draft_not_installable")


if __name__ == "__main__":
    unittest.main()
