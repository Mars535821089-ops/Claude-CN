from __future__ import annotations

import json
import hashlib
import os
import runpy
import subprocess
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

    def test_remaining_queue_translates_plain_text_before_icu_without_dropping_entries(self):
        order = self.load_module()["ordered_remaining"]
        missing = {
            "icu": "{count, plural, one {One} other {Many}}",
            "used": "Already translated",
            "short": "Hello",
            "html": '<a href="https://example.org">Open</a>',
            "long": "This is a longer plain interface message",
        }
        remaining = order(missing, {}, {"used": "已翻译"})
        self.assertEqual(list(remaining), ["short", "long", "html", "icu"])
        self.assertEqual(set(remaining), set(missing) - {"used"})

    def test_literal_fallback_preserves_nested_icu_selectors_and_arguments(self):
        source = "{count, plural, =1 {Draft} one {# Draft} other {# Draft}}"
        helper = ROOT / "scripts/icu_literals.cjs"
        env = os.environ.copy()
        inherited_modules = ROOT.parents[1] / "node_modules"
        if inherited_modules.is_dir():
            env["NODE_PATH"] = str(inherited_modules)

        def invoke(payload):
            proc = subprocess.run(["node", str(helper)], input=json.dumps(payload),
                                  capture_output=True, text=True, env=env, timeout=10)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return json.loads(proc.stdout)

        parts = invoke({"operation": "segments", "source": source})["segments"]
        self.assertEqual([part["text"] for part in parts], ["Draft", " Draft", " Draft"])
        translated = invoke({"operation": "compose", "source": source,
                             "replacements": ["草稿", " 草稿", " 草稿"]})["text"]
        self.assertEqual(translated,
                         "{count, plural, =1 {草稿} one {# 草稿} other {# 草稿}}")
        self.load_parser()(json.dumps({"id": translated}), {"id": source})

        conditional = "{first, select, yes {New section} other {New section {n}}}"
        self.assertEqual([p["text"] for p in invoke({"operation": "segments", "source": conditional})["segments"]],
                         ["New section", "New section "])
        result = invoke({"operation": "compose", "source": conditional,
                         "replacements": ["新章节", "新章节 "]})["text"]
        self.assertEqual(result, "{first, select, yes {新章节} other {新章节 {n}}}")
        self.load_parser()(json.dumps({"id": result}), {"id": conditional})

    def test_icu_translation_batches_are_bounded_below_plain_text_batches(self):
        chunks = self.load_module()["chunks"]
        plain = {f"p{i}": "Hello" for i in range(46)}
        icu = {f"i{i}": "{count, plural, one {One} other {Many}}" for i in range(12)}
        self.assertEqual([len(batch) for batch in chunks(plain)], [45, 1])
        batches = list(chunks(icu))
        self.assertEqual([len(batch) for batch in batches], [8, 4])
        self.assertEqual({k for batch in batches for k in batch}, set(icu))

    def test_icu_validation_allows_chinese_word_order_without_changing_branches(self):
        original = "Edit the {period, select, daily {daily} other {monthly}} limit for {target}."
        translated = "编辑 {target} 的{period, select, daily {每日} other {每月}}限额。"
        self.assertEqual(self.load_parser()(json.dumps({"id": translated}), {"id": original}),
                         {"id": translated})

    def test_batch_icu_audit_reports_only_changed_branch_keys(self):
        source = {"good": "Edit {period, select, daily {daily} other {monthly}} for {target}",
                  "bad": "{count, plural, one {Draft} other {# Draft}}"}
        translated = {"good": "编辑 {target} 的{period, select, daily {每日} other {每月}}",
                      "bad": "{count, plural, few {草稿} other {# 草稿}}"}
        env = os.environ.copy()
        inherited_modules = ROOT.parents[1] / "node_modules"
        if inherited_modules.is_dir():
            env["NODE_PATH"] = str(inherited_modules)
        proc = subprocess.run(["node", str(ROOT / "scripts/icu_literals.cjs")],
                              input=json.dumps({"operation": "validate-batch", "source": source,
                                                "translated": translated}),
                              capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"checked": 2, "invalid": ["bad"]})

    def test_versioned_claude_candidate_binds_all_catalog_bytes(self):
        base = ROOT / "resources/candidates/claude-desktop-4.23118.0"
        manifest = json.loads((base / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "locale_candidate_only_not_installed")
        for name in ("desktop", "frontend", "dynamic"):
            data = (base / f"{name}-zh-CN.json").read_bytes()
            catalog = json.loads(data)
            self.assertEqual(len(catalog), manifest[name]["message_ids"])
            self.assertEqual(hashlib.sha256(data).hexdigest(), manifest[name]["candidate_sha256"])
        frontend = json.loads((base / "frontend-zh-CN.json").read_text())
        for file in ("icu-overrides-4.23118.0.json", "reused-icu-fixes-4.23118.0.json"):
            for key, value in json.loads((ROOT / "resources" / file).read_text()).items():
                self.assertEqual(frontend[key], value)


if __name__ == "__main__":
    unittest.main()
