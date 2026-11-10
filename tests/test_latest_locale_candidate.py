import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1] / "resources/candidates"
PRIOR = ROOT / "claude-desktop-4.23118.0"
LATEST = ROOT / "claude-desktop-4.23118.1"


class LatestLocaleCandidateTests(unittest.TestCase):
    def test_identical_english_catalogs_reuse_exact_reviewed_translation_bytes(self):
        old = json.loads((PRIOR / "manifest.json").read_text())
        new = json.loads((LATEST / "manifest.json").read_text())
        self.assertEqual(new["target_version"], "4.23118.1")
        self.assertEqual(new["status"], "locale_candidate_only_not_installed")
        self.assertEqual(new["derived_from"], "4.23118.0")
        for group in ("desktop", "frontend", "dynamic"):
            name = f"{group}-zh-CN.json"
            self.assertEqual(new[group], old[group])
            self.assertEqual((LATEST / name).read_bytes(), (PRIOR / name).read_bytes())
            self.assertEqual(
                hashlib.sha256((LATEST / name).read_bytes()).hexdigest(),
                new[group]["candidate_sha256"],
            )
