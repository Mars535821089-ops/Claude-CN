"""One source-bound gate for all new-build localization and Cowork inputs."""

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import verify_combined_423118 as combined
from update_guard import catalog_fingerprint


class Combined423118Tests(unittest.TestCase):
    def test_latest_candidate_does_not_reuse_the_old_version_path(self):
        latest = combined.candidate_for_version("4.23118.1")
        self.assertEqual(latest.name, "claude-desktop-4.23118.1")
        self.assertTrue((latest / "manifest.json").is_file())
        with self.assertRaisesRegex(ValueError, "version"):
            combined.candidate_for_version("4.9999.0")

    def test_manifest_sources_cannot_edit_the_same_archive_entry_twice(self):
        ui = {"app.asar/.vite/build/new.js", "ion-dist.asar/assets/v1/menu.js"}
        locale = {"ion-dist.asar/assets/v1/shared-67.js", "ion-dist.asar/assets/v1/shared-99.js"}
        cowork = {"app.asar/.vite/build/hostloop.js"}
        catalogs = {"app.asar/resources/i18n/zh-CN.json", "ion-dist.asar/i18n/zh-CN.json",
                    "ion-dist.asar/i18n/dynamic/zh-CN.json"}
        self.assertEqual(combined.assert_disjoint(ui, locale, cowork, catalogs), 8)
        with self.assertRaisesRegex(ValueError, "overlap"):
            combined.assert_disjoint(ui, locale, {"app.asar/.vite/build/new.js"}, catalogs)

    def test_catalog_contract_rejects_missing_ids_without_weakening_icu(self):
        english = {"a": "Hello {name}", "b": "Open"}
        chinese = {"a": "您好，{name}", "b": "打开"}
        candidate = json.dumps(chinese, ensure_ascii=False).encode()
        spec = {"message_ids": 2,
                "official_english_catalog_sha256": catalog_fingerprint(english),
                "candidate_sha256": hashlib.sha256(candidate).hexdigest()}
        self.assertEqual(combined.validate_catalog(english, candidate, spec)[0], 2)
        wrong = json.dumps({"a": chinese["a"]}, ensure_ascii=False).encode()
        with self.assertRaisesRegex(ValueError, "message IDs"):
            combined.validate_catalog(english, wrong, spec)
        altered = dict(spec, official_english_catalog_sha256="0" * 64)
        with self.assertRaisesRegex(ValueError, "English"):
            combined.validate_catalog(english, candidate, altered)


if __name__ == "__main__":
    unittest.main()
