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
    def load_module(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "missing.json").write_text("{}")
            with mock.patch.object(sys, "argv", ["translate_missing.py", "--directory", temp]):
                return runpy.run_path(str(ROOT / "scripts/translate_missing.py"))

    def load_parser(self):
        return self.load_module()["parse_response"]

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

    def test_partition_retains_valid_results_when_one_icu_branch_is_wrong(self):
        partition = self.load_module()["partition_response"]
        batch = {"good": "Saved", "bad": "{period, select, daily {Day} other {Other}}"}
        raw = json.dumps({"good": "已保存", "bad": "{period, select, 每日 {每日} other {其他}}"})
        valid, rejected = partition(raw, batch)
        self.assertEqual(valid, {"good": "已保存"})
        self.assertEqual(rejected, {"bad": batch["bad"]})

    def test_retry_requests_only_rejected_items_after_persisting_valid_ones(self):
        translate = self.load_module()["translate"]
        requests, saved = [], []
        batch = {"good": "Saved", "bad": "{period, select, daily {Day} other {Other}}"}

        def request(items):
            requests.append(dict(items))
            if len(requests) == 1:
                return {"good": "已保存"}, {"bad": batch["bad"]}
            return {"bad": "{period, select, daily {每日} other {其他}}"}, {}

        translate.__globals__["request"] = request
        translate.__globals__["write_progress"] = lambda output: saved.append(dict(output))
        output = {}
        translate(batch, output)
        self.assertEqual(requests, [batch, {"bad": batch["bad"]}])
        self.assertEqual(output, {"good": "已保存", "bad": "{period, select, daily {每日} other {其他}}"})
        self.assertEqual(saved[0], {"good": "已保存"})


if __name__ == "__main__":
    unittest.main()
