import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


class Port512312Tests(unittest.TestCase):
    def helper(self):
        path = ROOT / "scripts/prepare_512312.py"
        self.assertTrue(path.exists(), ".2 exact-source preparation missing")
        spec = importlib.util.spec_from_file_location("prepare_512312_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_only_identical_english_catalog_can_reuse_reviewed_full06(self):
        p = self.helper()
        self.assertEqual(p.reuse_catalog({"a": "Old"}, {"a": "Old"}, {"a": "旧"}), {"a": "旧"})
        for new in ({"a": "Changed"}, {"a": "Old", "b": "New"}, {}):
            with self.assertRaises(ValueError):
                p.reuse_catalog({"a": "Old"}, new, {"a": "旧"})

    def test_missing_chinese_is_not_full_coverage(self):
        with self.assertRaises(ValueError):
            self.helper().reuse_catalog({"a": "Old"}, {"a": "Old"}, {})

    def test_four_x_trust_boundary_does_not_accept_point_two(self):
        import patch_whitelist_423118 as old
        with self.assertRaises(ValueError):
            old.load_reviewed("5.1231.2")


if __name__ == "__main__":
    unittest.main()
