import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


class MigrationAuditTests(unittest.TestCase):
    def implementation(self):
        self.assertIsNotNone(importlib.util.find_spec("audit_migration"),
                             "Exact-English migration audit is missing")
        import audit_migration
        return audit_migration

    def test_changed_english_under_same_id_is_not_blindly_reused(self):
        audit = self.implementation()
        result = audit.merge_exact(
            {"a": "Delete", "unchanged": "Open"},
            {"a": "Archive", "unchanged": "Open"},
            {"a": "删除", "unchanged": "打开"},
        )
        self.assertEqual(result["translations"], {"unchanged": "打开"})
        self.assertEqual(result["missing"], {"a": "Archive"})
        self.assertEqual(result["provenance"], {"unchanged": "same_id_same_english"})

    def test_new_id_reuses_unambiguous_exact_text_but_not_a_conflict(self):
        audit = self.implementation()
        result = audit.merge_exact(
            {"a": "Open", "b": "Open", "c": "Close"},
            {"new": "Open", "same": "Close"},
            {"a": "打开", "b": "开启", "c": "关闭"},
        )
        self.assertEqual(result["translations"], {"same": "关闭"})
        self.assertEqual(result["missing"], {"new": "Open"})

    def test_official_and_reviewed_sources_override_safe_old_matches(self):
        audit = self.implementation()
        result = audit.merge_exact(
            {"old": "Open"}, {"old": "Open", "new": "Close", "brand": "Claude Code"},
            {"old": "开启"}, official={"old": "打开"}, reviewed={"Close": "关闭"},
            preserved={"Claude Code"},
        )
        self.assertEqual(result["translations"],
                         {"old": "打开", "new": "关闭", "brand": "Claude Code"})
        self.assertEqual(result["missing"], {})
        self.assertEqual(result["provenance"]["old"], "new_official_chinese")
        self.assertEqual(result["provenance"]["brand"], "reviewed_preserved")

    def test_missing_or_nonstring_baseline_translation_is_rejected(self):
        audit = self.implementation()
        for translations in ({}, {"a": None}, {"a": ""}, {"a": "打开", "extra": "多余"}):
            with self.subTest(translations=translations), self.assertRaises(ValueError):
                audit.merge_exact({"a": "Open"}, {"a": "Open"}, translations)

    def test_unknown_new_text_stays_missing_without_english_fallback(self):
        audit = self.implementation()
        result = audit.merge_exact({}, {"new": "Unreviewed text"}, {})
        self.assertEqual(result["translations"], {})
        self.assertEqual(result["missing"], {"new": "Unreviewed text"})

    def test_version_and_full_source_fingerprint_bind_reviewed_additions(self):
        audit = self.implementation()
        catalogs = {"desktop": {"en": {"new": "Delete {count} files"}, "official": {}}}
        results = {"desktop": audit.merge_exact({}, catalogs["desktop"]["en"], {})}
        review = {
            "version": "4.23118.0",
            "catalogs": {"desktop": {
                "english_sha256": audit.catalog_fingerprint(catalogs["desktop"]["en"]),
                "translations": {"new": "删除 {count} 个文件"},
            }},
        }
        audit.apply_review(catalogs, results, review, "4.23118.0")
        self.assertEqual(results["desktop"]["missing"], {})
        self.assertEqual(results["desktop"]["translations"]["new"], "删除 {count} 个文件")
        self.assertEqual(results["desktop"]["provenance"]["new"], "reviewed_pinned_source")
        for version, fingerprint in (("4.29.0", review["catalogs"]["desktop"]["english_sha256"]),
                                     ("4.23118.0", "0" * 64)):
            review["catalogs"]["desktop"]["english_sha256"] = fingerprint
            with self.assertRaises(ValueError):
                audit.apply_review(catalogs, results, review, version)

    def test_review_rejects_unknown_ids_and_invalid_values_before_any_application(self):
        audit = self.implementation()
        catalogs = {"desktop": {"en": {"new": "Open"}, "official": {}}}
        for invalid in ({"unknown": "打开"}, {"new": ""}, {"new": None}):
            results = {"desktop": audit.merge_exact({}, catalogs["desktop"]["en"], {})}
            review = {"version": "4.23118.0", "catalogs": {"desktop": {
                "english_sha256": audit.catalog_fingerprint(catalogs["desktop"]["en"]),
                "translations": invalid,
            }}}
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                audit.apply_review(catalogs, results, review, "4.23118.0")
            self.assertEqual(results["desktop"]["translations"], {})

    def test_nested_review_file_loads_and_duplicate_ids_are_refused(self):
        audit = self.implementation()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review.json"
            path.write_text('{"version":"4.23118.0","catalogs":{"desktop":{'
                            '"english_sha256":"hash","translations":{"id":"打开"}}}}')
            self.assertEqual(audit.load_review(path)["catalogs"]["desktop"]
                             ["translations"], {"id": "打开"})
            path.write_text('{"catalogs":{"desktop":{"translations":{"id":"打开","id":"关闭"}}}}')
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                audit.load_review(path)


if __name__ == "__main__":
    unittest.main()
