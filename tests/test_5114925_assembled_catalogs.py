"""No incomplete or stale 5.11492.5 message bundle may be packaged."""

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import assemble_5114925_catalogs as assembler


class AssembledCatalogTests(unittest.TestCase):
    def test_reachable_tag_structure_survives_reordering_and_zh_plural(self):
        english = {"reordered": "Open <b>details</b> and <link>help</link>",
                   "plural": "{n, plural, one {<b>#</b> item} other {<b>#</b> items}}"}
        chinese = {"reordered": "查看 <link>帮助</link> 和 <b>详情</b>",
                   "plural": "{n, plural, other {<b>#</b> 项}}"}
        assembler._validate_catalog("frontend", english, chinese)
        with self.assertRaisesRegex(ValueError, "reachable structure"):
            assembler._validate_catalog("frontend", english,
                {**chinese, "reordered": "查看 <link>帮助</link> 和详情"})

    def test_unchanged_ids_and_exact_new_reviews_cover_source_order(self):
        english = {"old": "Open", "new": "Save {file}"}
        old_english = {"old": "Open", "removed": "Delete"}
        old_chinese = {"old": "打开", "removed": "删除"}
        result = assembler.compose_catalog(
            english, old_english, old_chinese, [{"new": "保存 {file}"}])
        self.assertEqual(result, {"old": "打开", "new": "保存 {file}"})
        with self.assertRaisesRegex(ValueError, "changed source"):
            assembler.compose_catalog(
                {"old": "Open now", "new": "Save {file}"}, old_english,
                old_chinese, [{"new": "保存 {file}"}])
        with self.assertRaisesRegex(ValueError, "uncovered"):
            assembler.compose_catalog(english, old_english, old_chinese, [])
        with self.assertRaisesRegex(ValueError, "overlap"):
            assembler.compose_catalog(english, old_english, old_chinese,
                                      [{"old": "覆盖", "new": "保存 {file}"}])

    def test_source_bound_old_id_correction_does_not_modify_prior_version(self):
        english = {"old": "{n, plural, one {One} other {# others}}"}
        prior = {"old": "{n, plural, other {# 个}}"}
        corrected = "{n, plural, one {一个} other {# 个}}"
        result = assembler.compose_catalog(english, english, prior, [],
                                            overrides={"old": corrected})
        self.assertEqual(result, {"old": corrected})
        self.assertEqual(prior["old"], "{n, plural, other {# 个}}")
        with self.assertRaisesRegex(ValueError, "override"):
            assembler.compose_catalog(english, english, prior, [],
                                      overrides={"unknown": corrected})


if __name__ == "__main__":
    unittest.main()
