from __future__ import annotations

import json
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


class TranslationSafetyTests(unittest.TestCase):
    def load_parser(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "missing.json").write_text("{}")
            with mock.patch.object(sys, "argv", ["translate_missing.py", "--directory", temp]):
                return runpy.run_path(str(ROOT / "scripts/translate_missing.py"))["parse_response"]

    def test_rejects_translated_icu_select_branch_names(self):
        parse_response = self.load_parser()
        original = "{period, select, daily {Daily} weekly {Weekly} other {Other}}"
        bad = "{period, select, 每日 {每日} 每周 {每周} other {其他}}"
        with self.assertRaisesRegex(ValueError, "placeholders"):
            parse_response(json.dumps({"id": bad}), {"id": original})

    def test_rejects_changed_html_tag_attributes(self):
        parse_response = self.load_parser()
        original = '<a href="https://example.org">Open details</a>'
        bad = '<a href="https://different.org">打开详情</a>'
        with self.assertRaisesRegex(ValueError, "placeholders"):
            parse_response(json.dumps({"id": bad}), {"id": original})

    def test_accepts_translated_text_with_original_placeholders(self):
        parse_response = self.load_parser()
        original = "{count, plural, one {# item} other {# items}}"
        translated = "{count, plural, one {# 项} other {# 项}}"
        self.assertEqual(parse_response(json.dumps({"id": translated}), {"id": original}),
                         {"id": translated})


if __name__ == "__main__":
    unittest.main()
