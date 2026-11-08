"""New Claude locale gating has two independent signed-source arrays."""

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import patch_whitelist_423118 as whitelist
from upstream_patch_lib import BASE_LANGUAGE_LIST


class Claude423118WhitelistTests(unittest.TestCase):
    def fixture(self, root: Path):
        original = BASE_LANGUAGE_LIST + '];const keep="en-US";'
        patched = BASE_LANGUAGE_LIST + ',"zh-CN"];const keep="en-US";'
        targets = {}
        for name in ("shared-67-fixture.js", "shared-99-fixture.js"):
            relative = f"assets/v1/{name}"
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(original)
            path.chmod(0o644)
            targets[relative] = {
                "sha256": hashlib.sha256(original.encode()).hexdigest(),
                "patched_sha256": hashlib.sha256(patched.encode()).hexdigest(),
                "expected": 1,
            }
        return targets, original, patched

    def test_preflights_both_official_arrays_before_any_disposable_stage_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            targets, original, patched = self.fixture(root)
            prepared = whitelist.prepare(root, targets)
            self.assertEqual(len(prepared), 2)
            self.assertTrue(all((root / name).read_text() == original for name in targets))
            whitelist.apply_prepared(prepared)
            self.assertTrue(all((root / name).read_text() == patched for name in targets))
            self.assertEqual(whitelist.prepare(root, targets), [])

    def test_later_wrong_source_or_a_symlink_cannot_modify_first_array(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            targets, original, _ = self.fixture(root)
            names = list(targets)
            (root / names[1]).write_text("changed")
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                whitelist.prepare(root, targets)
            self.assertEqual((root / names[0]).read_text(), original)
            (root / names[1]).unlink()
            (root / names[1]).symlink_to(root / names[0])
            with self.assertRaisesRegex(ValueError, "symlink"):
                whitelist.prepare(root, targets)

    def test_mixed_old_and_new_whitelist_is_not_considered_a_complete_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            targets, _original, patched = self.fixture(root)
            names = list(targets)
            (root / names[0]).write_text(patched)
            with self.assertRaisesRegex(ValueError, "partial"):
                whitelist.prepare(root, targets)

    def test_failed_second_replace_restores_first_bytes_and_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            targets, original, _ = self.fixture(root)
            original_replace = os.replace
            calls = 0

            def fail_second(source, destination):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("disposable stage second replacement failed")
                return original_replace(source, destination)

            with patch.object(whitelist.os, "replace", side_effect=fail_second):
                with self.assertRaises(OSError):
                    whitelist.apply_prepared(whitelist.prepare(root, targets))
            for name in targets:
                self.assertEqual((root / name).read_text(), original)
                self.assertEqual((root / name).stat().st_mode & 0o777, 0o644)

    def test_command_line_never_applies_to_an_arbitrary_extracted_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            targets, original, _ = self.fixture(root)
            with patch.object(sys, "argv", ["patch_whitelist_423118.py",
                                            "--extracted-frontend", str(root), "--apply"]):
                with self.assertRaises(SystemExit) as caught:
                    whitelist.main()
            self.assertEqual(caught.exception.code, 2)
            self.assertTrue(all((root / name).read_text() == original for name in targets))

    def test_signed_archive_entry_can_be_verified_in_memory_without_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            targets, original, patched = self.fixture(Path(tmp))
            spec = next(iter(targets.values()))
            self.assertEqual(whitelist.verify_source(original.encode(), spec), patched.encode())
            self.assertIsNone(whitelist.verify_source(patched.encode(), spec))
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                whitelist.verify_source(b"unknown vendor code", spec)


if __name__ == "__main__":
    unittest.main()
