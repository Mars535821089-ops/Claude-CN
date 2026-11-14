"""Hardcoded UI rules for a new signed build need new path and byte pins."""

import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import review_hardcoded_5114925 as review


class Hardcoded5114925Tests(unittest.TestCase):
    def test_exact_rebind_requires_unique_new_paths_and_matching_edits(self):
        old = {"old.js": {"rules": [{"source": "Open", "expected": 1}]}}
        new = {"old.js": "new.js"}
        result = review.pin_rules(old, new, lambda path: b'label:"Open"',
                                  lambda path, source, rules: source.replace(b'Open', b'\xe6\x89\x93\xe5\xbc\x80'))
        self.assertEqual(result["new.js"]["sha256"], hashlib.sha256(b'label:"Open"').hexdigest())
        self.assertEqual(result["new.js"]["patched_sha256"],
                         hashlib.sha256(b'label:"\xe6\x89\x93\xe5\xbc\x80"').hexdigest())
        self.assertEqual(result["new.js"]["rules"], old["old.js"]["rules"])
        with self.assertRaises(ValueError):
            review.pin_rules(old, {}, lambda path: b'Open', lambda *args: b'\xe6\x89\x93\xe5\xbc\x80')
        with self.assertRaises(ValueError):
            review.pin_rules({"a": old["old.js"], "b": old["old.js"]},
                             {"a": "same", "b": "same"}, lambda path: b'Open',
                             lambda *args: b'\xe6\x89\x93\xe5\xbc\x80')
        with self.assertRaises(ValueError):
            review.pin_rules(old, new, lambda path: b'Open', lambda *args: b'Open')


if __name__ == "__main__":
    unittest.main()
