"""Rebinding old drafts must never treat a new build as already localized."""
import hashlib
from pathlib import Path
import tempfile
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from review_claude_5114925 import rebind_draft
import review_claude_5114925 as candidate


class LatestClaudeDraftTests(unittest.TestCase):
    def test_desktop_proposals_require_all_ids_and_valid_icu(self):
        english = {"official": "{n, plural, one {Open one} other {Open #}}",
                   "memory": "Keep {name}"}
        translated = {"official": "{n, plural, one {打开一个} other {打开 # 个}}",
                      "memory": "保留 {name}"}
        archives = {"Contents/Resources/app.asar": "a" * 64,
                    "Contents/Resources/ion-dist.asar": "b" * 64}
        draft = {"version": candidate.VERSION, "group": "desktop",
                 "status": "draft_not_installable", "source_archives_sha256": archives,
                 "source_english_sha256": {key: hashlib.sha256(text.encode()).hexdigest()
                                           for key, text in english.items()},
                 "translations": translated}
        identity = {"version": candidate.VERSION, "archives": archives}
        self.assertEqual(candidate.validate_desktop_proposals_review(
            draft, english, identity, set(english)), 2)
        with self.assertRaises(ValueError):
            candidate.validate_desktop_proposals_review(
                {**draft, "translations": {"official": translated["official"]}},
                english, identity, set(english))
        with self.assertRaises(ValueError):
            candidate.validate_desktop_proposals_review(
                {**draft, "translations": {**translated,
                    "official": "{n, plural, other {打开 # 个}}"}},
                english, identity, set(english))

    def test_frontend_coverage_rejects_overlap_even_if_counts_add_up(self):
        self.assertEqual(candidate.ensure_frontend_coverage(
            {"old"}, {"pending"}, {"memory"}, 1,
            {"old", "pending", "memory"}), 3)
        with self.assertRaises(ValueError):
            candidate.ensure_frontend_coverage(
                {"old"}, {"pending"}, {"old"}, 1,
                {"old", "pending", "uncovered"})
        with self.assertRaises(ValueError):
            candidate.ensure_frontend_coverage(
                {"old"}, {"pending"}, {"memory"}, 0,
                {"old", "pending", "memory"})

    def test_memory_suggestions_require_exact_version_bound_authored_review(self):
        english = {"new": "Open {file}", "other": "Close {file}"}
        proposals = {"new": "打开 {file}", "other": "关闭 {file}"}
        archives = {"Contents/Resources/app.asar": "a" * 64,
                    "Contents/Resources/ion-dist.asar": "b" * 64}
        review = {"version": candidate.VERSION, "group": "frontend",
                  "status": "draft_not_installable", "source_archives_sha256": archives,
                  "source_english_sha256": {key: hashlib.sha256(text.encode()).hexdigest()
                                            for key, text in english.items()},
                  "translations": proposals}
        identity = {"version": candidate.VERSION, "archives": archives}
        self.assertEqual(candidate.validate_frontend_memory_review(
            review, english, identity, proposals), 2)
        with self.assertRaises(ValueError):
            candidate.validate_frontend_memory_review(
                {**review, "translations": {"new": proposals["new"]}}, english,
                identity, proposals)
        with self.assertRaises(ValueError):
            candidate.validate_frontend_memory_review(
                {**review, "translations": {**proposals, "other": "已关闭"}}, english,
                identity, proposals)
        with self.assertRaises(ValueError):
            candidate.validate_frontend_memory_review(
                {**review, "source_archives_sha256": {}}, english,
                identity, proposals)

    def test_incremental_draft_is_version_bound_disjoint_and_provisional(self):
        import json
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            english = {"new": "Please try again."}
            archives = {"Contents/Resources/app.asar": "a" * 64,
                        "Contents/Resources/ion-dist.asar": "b" * 64}
            draft = {"version": "5.11492.5", "group": "frontend",
                     "status": "draft_not_installable", "source_archives_sha256": archives,
                     "source_english_sha256": {"new": hashlib.sha256(english["new"].encode()).hexdigest()},
                     "translations": {"new": "请重试。"}}
            (root / "frontend-authored-draft-02.json").write_text(json.dumps(draft))
            result = candidate.validate_frontend_additions(root, english,
                {"version": "5.11492.5", "archives": archives}, {"new"}, set())
            self.assertEqual(result, {"frontend-authored-draft-02.json": 1})
            with self.assertRaises(ValueError):
                candidate.validate_frontend_additions(root, english,
                    {"version": "5.11492.5", "archives": archives}, {"new"}, {"new"})
            with self.assertRaises(ValueError):
                candidate.validate_frontend_additions(root, {"new": "Different source."},
                    {"version": "5.11492.5", "archives": archives}, {"new"}, set())

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
