#!/usr/bin/env python3
"""Review only the signed 5.11492.5 sidebar candidate; no app patch/install."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import review_claude_5114925 as current
from upstream_patch_lib import read_frontend_entry

ROOT = Path(__file__).resolve().parents[1]
APP = Path("/Applications/Claude.app")
RESOURCE = "assets/v1/shared-98-CG0W8eqd.js"
CANDIDATE = ROOT / "resources/candidates/claude-desktop-5.11492.5"
EXPECTED = "6a54cf575da69d4539e9157f97a0fef36d5f87edbd5d9617656e3ca7e2f8a9f7"


def run(operation: str, text: str):
    result = subprocess.run(["node", str(ROOT / "scripts/sidebar_5114925.cjs")],
                            input=json.dumps({"operation": operation, "source": text}),
                            capture_output=True, text=True, timeout=75, check=False)
    if result.returncode:
        raise ValueError("new-version sidebar source or behavior anchor changed")
    return json.loads(result.stdout)


def review(app: Path = APP) -> tuple[dict, dict]:
    report, _ = current.review(app)
    raw = read_frontend_entry(app, RESOURCE)
    if raw is None or hashlib.sha256(raw).hexdigest() != EXPECTED:
        raise ValueError("signed current sidebar source fingerprint differs")
    before = run("parts", raw.decode())
    patched = run("patch", raw.decode())
    after = run("parts", patched)
    if (before["setCollapsedPref"] != after["setCollapsedPref"]
            or before["partialize"] != after["partialize"]
            or current.update_guard.bundle_identity(app)["archives"]
               != report["source_archives_sha256"]):
        raise ValueError("native toggle, persistence or signed source changed")
    digest = hashlib.sha256(patched.encode()).hexdigest()
    fixture = {"version": current.VERSION, "status": "draft_not_installable",
               "source_sha256": EXPECTED, "patched_sha256": digest,
               "before": before, "after": after,
               "installed": False, "visual_ui_verified": False}
    summary = {"version": current.VERSION, "status": "review_required_not_installable",
               "source_archives_sha256": report["source_archives_sha256"],
               "resource": RESOURCE, "source_sha256": EXPECTED,
               "patched_sha256": digest,
               "native_toggle_unchanged": True, "persistence_fields_unchanged": True,
               "installed": False, "visual_ui_verified": False}
    return fixture, summary


if __name__ == "__main__":
    fixture, summary = review()
    targets = [(CANDIDATE / "sidebar-lifecycle-fixture.json", fixture),
               (CANDIDATE / "sidebar-reviewed.json", summary)]
    if any(path.exists() or path.is_symlink() for path, _ in targets):
        raise SystemExit("sidebar review candidate already exists")
    for path, value in targets:
        with path.open("x") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"status": summary["status"], "source_sha256": EXPECTED,
                      "patched_sha256": summary["patched_sha256"],
                      "installed": False, "visual_ui_verified": False}))
