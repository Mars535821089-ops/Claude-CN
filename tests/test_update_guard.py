import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import update_guard


class UpdateGuardTests(unittest.TestCase):
    def test_catalog_diff_reports_added_removed_and_changed_ids(self):
        diff = update_guard.catalog_diff(
            {"kept": "same", "removed": "old text", "changed": "before"},
            {"kept": "same", "added": "new text", "changed": "after"},
        )
        self.assertEqual(diff["added"], {"added": "new text"})
        self.assertEqual(diff["removed"], {"removed": "old text"})
        self.assertEqual(diff["changed"], {"changed": {"before": "before", "after": "after"}})

    def test_translation_alignment_reports_catalog_coverage(self):
        result = update_guard.translation_alignment(
            {"translated": "Hello", "fallback": "Fallback", "new": "New"},
            {"translated": "你好", "fallback": "Fallback", "stale": "旧"},
        )
        self.assertEqual(result["translated_count"], 1)
        self.assertEqual(result["untranslated_count"], 1)
        self.assertEqual(result["missing_ids"], ["new"])
        self.assertEqual(result["unused_ids"], ["stale"])

    def test_baseline_write_is_atomic_and_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "baseline.json"
            snapshot = {
                "version": "4.29.0",
                "catalogs": {"frontend": {"id": "text"}},
                "fingerprints": {"frontend": "abc"},
            }
            update_guard.save_baseline(path, snapshot)
            self.assertEqual(update_guard.load_baseline(path)["snapshot"], snapshot)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_inspection_compares_full_resources_without_writing_app(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "Claude.app"
            app.mkdir()
            state = Path(directory) / "state.json"
            previous = {
                "version": "4.29.0",
                "catalogs": {
                    "desktop": {"same": "unchanged", "changed": "before"},
                    "frontend": {"old": "removed"},
                },
                "fingerprints": {"desktop": "old", "frontend": "old"},
            }
            update_guard.save_baseline(state, previous)
            current = {
                "version": "4.30.0",
                "catalogs": {
                    "desktop": {"same": "unchanged", "changed": "after", "added": "new"},
                    "frontend": {"new": "added"},
                },
                "fingerprints": {"desktop": "new", "frontend": "new"},
            }
            translations = {
                "desktop": {"same": "不变"},
                "frontend": {"new": "新增"},
            }
            with (
                patch.object(update_guard, "make_snapshot", return_value=current),
                patch.object(update_guard.repair, "json_file", side_effect=lambda path: translations[
                    "desktop" if "desktop" in str(path) else "frontend"
                ]),
            ):
                report = update_guard.inspect(app, state)

            self.assertEqual(report["status"], "changed")
            self.assertEqual(report["diff"]["desktop"]["changed"]["changed"],
                             {"before": "before", "after": "after"})
            self.assertEqual(report["diff"]["desktop"]["added"], {"added": "new"})
            self.assertEqual(report["diff"]["frontend"]["removed"], {"old": "removed"})
            self.assertEqual(list(app.iterdir()), [])

    def test_unknown_locale_shape_stops_with_clear_error(self):
        with self.assertRaisesRegex(ValueError, "Unsupported frontend English catalog structure"):
            update_guard.decode_catalog(b'["not", "an", "object"]', "frontend")


if __name__ == "__main__":
    unittest.main()
