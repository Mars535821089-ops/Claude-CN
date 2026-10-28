import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import repair_macos_429 as repair


class LocaleTransactionTests(unittest.TestCase):
    def make_profile(self, home, name, value):
        path = Path(home) / "Library/Application Support" / name / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def test_locale_change_preserves_nested_preferences(self):
        with tempfile.TemporaryDirectory() as home:
            data = {"locale": "en-US", "provider": {"name": "example"}, "autoUpdates": True, "draftSettings": [1, 2]}
            path = self.make_profile(home, "Claude-3p", data)
            before = path.read_bytes()
            originals = repair.update_locale(Path(home), "zh-CN")
            self.assertEqual(json.loads(path.read_text()), dict(data, locale="zh-CN"))
            self.assertEqual(originals[path], before)
            self.assertFalse((Path(home) / "Library/Application Support/Claude").exists())

    def test_write_failure_restores_earlier_profile(self):
        with tempfile.TemporaryDirectory() as home:
            first = self.make_profile(home, "Claude", {"locale": "en-US", "x": 1})
            second = self.make_profile(home, "Claude-3p", {"locale": "en-US", "x": 2})
            before = [first.read_bytes(), second.read_bytes()]
            actual_replace = repair.os.replace
            def replace(source, target):
                if Path(target) == second:
                    raise OSError("simulated failure")
                return actual_replace(source, target)
            with patch.object(repair.os, "replace", side_effect=replace):
                with self.assertRaises(OSError):
                    repair.update_locale(Path(home), "zh-CN")
            self.assertEqual([first.read_bytes(), second.read_bytes()], before)

    def test_invalid_second_profile_does_not_modify_first(self):
        with tempfile.TemporaryDirectory() as home:
            first = self.make_profile(home, "Claude", {"locale": "en-US"})
            second = self.make_profile(home, "Claude-3p", {})
            second.write_text("{")
            before = first.read_bytes()
            with self.assertRaises(json.JSONDecodeError):
                repair.update_locale(Path(home), "zh-CN")
            self.assertEqual(first.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
