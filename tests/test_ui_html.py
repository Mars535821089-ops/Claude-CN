import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ui_html import apply_html, patch_html_bundle, scan_html

ROOT = Path(__file__).resolve().parents[1]


class HTMLUITests(unittest.TestCase):
    def test_scans_rendered_text_and_attributes_not_scripts_styles_or_data(self):
        source = '<p>Copy</p><input placeholder="Search" value="Copy" data-kind="Copy">' \
                 '<script>const text="Copy"</script><style>.x{content:"Copy"}</style>'
        scanner = scan_html(source)
        self.assertEqual([(x["context"], x["text"]) for x in scanner.findings],
                         [("html:text", "Copy"), ("html:placeholder", "Search")])
        self.assertEqual(scanner.scripts, ['const text="Copy"'])

    def test_replaces_only_the_reviewed_context_without_reformatting_html(self):
        source = '中文\n <p>  Copy  </p><input placeholder="Copy"><script>"Copy"</script>'
        result = apply_html(source, [{"context": "html:text", "source": "Copy", "expected": 1}],
                            {"Copy": "复制"})
        self.assertEqual(result, '中文\n <p>  复制  </p><input placeholder="Copy"><script>"Copy"</script>')

    def test_attribute_translation_escapes_markup_and_quotes(self):
        result = apply_html('<input placeholder="Search">', [
            {"context": "html:placeholder", "source": "Search", "expected": 1}],
            {"Search": '搜索 "<内容>"'})
        self.assertEqual(result, '<input placeholder="搜索 &quot;&lt;内容&gt;&quot;">')

    def test_changed_html_stops(self):
        with self.assertRaisesRegex(ValueError, "HTML UI source changed"):
            apply_html("<p>Changed</p>", [
                {"context": "html:text", "source": "Copy", "expected": 1}], {"Copy": "复制"})

    def test_json_script_is_not_treated_as_javascript(self):
        self.assertEqual(scan_html('<script type="application/json">{"x":1}</script>').scripts, [])

    def test_inline_script_patch_preserves_logic_and_refuses_unknown_fingerprint(self):
        source = 'f({children:"Copy",icon:"Copy"})'
        patched = 'f({children:"复制",icon:"Copy"})'
        spec = {
            "sha256": hashlib.sha256(source.encode()).hexdigest(),
            "patched_sha256": hashlib.sha256(patched.encode()).hexdigest(),
            "rules": [{"context": "children", "source": "Copy", "expected": 1}],
        }
        html = f'<script>{source}</script><p>Unchanged</p>'
        self.assertEqual(apply_html(html, [], {}, {"0": spec}),
                         f'<script>{patched}</script><p>Unchanged</p>')
        with self.assertRaises(subprocess.CalledProcessError) as raised:
            apply_html(html.replace('icon:"Copy"', 'icon:"Changed"'), [], {}, {"0": spec})
        self.assertIn("Unknown inline UI fingerprint", raised.exception.stderr)

    def test_html_bundle_preflights_every_file_and_is_idempotent(self):
        source = "<p>Copy</p>"
        patched = "<p>复制</p>"
        spec = {
            "sha256": hashlib.sha256(source.encode()).hexdigest(),
            "patched_sha256": hashlib.sha256(patched.encode()).hexdigest(),
            "rules": [{"context": "html:text", "source": "Copy", "expected": 1}],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resources = root / "resources"
            resources.mkdir()
            (resources / "ui-reviewed-zh-CN.json").write_text(json.dumps({"Copy": "复制"}))
            (resources / "ui-patch-manifest.json").write_text(json.dumps({
                "files": {"app.asar/first.html": spec, "app.asar/second.html": spec},
            }))
            first, second = root / "first.html", root / "second.html"
            first.write_text(source)
            second.write_text("changed")
            with self.assertRaisesRegex(ValueError, "Unknown HTML UI fingerprint"):
                patch_html_bundle(root, "app.asar", resources)
            self.assertEqual(first.read_text(), source)
            second.write_text(source)
            patch_html_bundle(root, "app.asar", resources)
            patch_html_bundle(root, "app.asar", resources)
            self.assertEqual(first.read_text(), patched)
            self.assertEqual(second.read_text(), patched)

    def test_current_review_manifest_is_not_an_installation_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ui-patch-manifest.json").write_text(json.dumps({
                "version": "4.4212.0", "preview_only": True, "files": {},
            }))
            with self.assertRaisesRegex(ValueError, "Review-only HTML manifest cannot be applied"):
                patch_html_bundle(root, "app.asar", root)

    def test_reviewed_resources_have_no_duplicate_keys_and_valid_rules(self):
        def unique_pairs(pairs):
            result = {}
            for key, value in pairs:
                self.assertNotIn(key, result, f"Duplicate JSON key: {key}")
                result[key] = value
            return result

        dictionary = json.loads((ROOT / "resources/ui-reviewed-zh-CN.json").read_text(),
                                object_pairs_hook=unique_pairs)
        manifest = json.loads((ROOT / "resources/ui-patch-manifest.json").read_text(),
                              object_pairs_hook=unique_pairs)
        self.assertEqual(manifest["version"], "4.29.0")
        for source, target in dictionary.items():
            self.assertIsInstance(target, str)
            self.assertTrue(target)
            self.assertNotEqual(source, target)
            self.assertEqual(source.count("{…}"), target.count("{…}"), source)
        for file, spec in manifest["files"].items():
            self.assertIn(file.split("/")[0], {"app.asar", "ion-dist.asar"})
            self.assertNotIn("..", Path(file).parts)
            for entry in [spec, *spec.get("inline_scripts", {}).values()]:
                self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")
                self.assertRegex(entry["patched_sha256"], r"^[0-9a-f]{64}$")
                for rule in entry["rules"]:
                    self.assertIn(rule["source"], dictionary)
                    self.assertGreater(rule["expected"], 0)
                    self.assertNotIn(rule["context"], {"message", "errorMessage"})
