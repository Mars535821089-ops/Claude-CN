#!/usr/bin/env python3
"""Pin six current signed hardcoded-UI chunks without modifying Claude.app."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import subprocess

import claude5_candidate as old
import review_claude_5114925 as current
from upstream_patch_lib import read_asar_entry, read_asar_header, read_frontend_entry

ROOT = Path(__file__).resolve().parents[1]
APP = Path("/Applications/Claude.app")
OLD_REVIEW = ROOT / "resources/candidates/claude-desktop-5.1231.2/hardcoded-ui-reviewed.json"
TARGET = ROOT / "resources/candidates/claude-desktop-5.11492.5/hardcoded-ui-reviewed.json"
PATHS = {
    "app.asar/.vite/build/index.chunk-3CBBolNc.js": "app.asar/.vite/build/index.chunk-PDoDfQ_4.js",
    "app.asar/.vite/build/index.chunk-BV3uOqbK.js": "app.asar/.vite/build/index.chunk-xV3fJhc7.js",
    "app.asar/.vite/build/index.chunk-22xHOiMR.js": "app.asar/.vite/build/index.chunk-2o5dG8Qg.js",
    "ion-dist.asar/assets/v1/cde03b773-DdSfp4q6.js": "ion-dist.asar/assets/v1/cde03b773-dE-LBeAJ.js",
    "ion-dist.asar/assets/v1/c4517f799-DhULzHGF.js": "ion-dist.asar/assets/v1/c4517f799-DhULzHGF.js",
    "ion-dist.asar/assets/v1/c5a5d8e85-C2iEEOLG.js": "ion-dist.asar/assets/v1/c5a5d8e85-DL5vo7jG.js",
}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def pin_rules(old_files: dict, new_paths: dict[str, str], loader, patcher) -> dict:
    if (set(old_files) != set(new_paths) or len(set(new_paths.values())) != len(new_paths)
            or not old_files):
        raise ValueError("hardcoded UI new-build path mapping incomplete or ambiguous")
    result = {}
    for previous, path in new_paths.items():
        rules = old_files[previous].get("rules")
        if not isinstance(rules, list) or not rules:
            raise ValueError("hardcoded UI rule scope missing")
        source = loader(path)
        if not isinstance(source, bytes) or not source:
            raise ValueError("hardcoded UI source chunk missing")
        patched = patcher(path, source, rules)
        if not isinstance(patched, bytes) or patched == source:
            raise ValueError("hardcoded UI review made no verified change")
        result[path] = {"sha256": sha(source), "patched_sha256": sha(patched),
                        "rules": rules, "status": "draft_not_installable"}
    return result


def patch_with_original_rules(path: str, raw: bytes, rules: list, translations: dict) -> bytes:
    program = '''const fs=require('fs'),u=require(process.argv[1]);
const p=JSON.parse(fs.readFileSync(0,'utf8'));
const value=u.applyRules(p.source,p.file,p.rules,p.translations).source;
process.stdout.write(Buffer.from(value,'utf8').toString('base64'));'''
    payload = {"file": path, "source": raw.decode(), "rules": rules,
               "translations": translations}
    proc = subprocess.run(["node", "-e", program,
                           str(ROOT / "scripts/ui_localization.cjs")],
                          input=json.dumps(payload, ensure_ascii=False),
                          capture_output=True, text=True, timeout=100, check=False)
    if proc.returncode:
        raise ValueError("new-build hardcoded UI rule context/count changed")
    return base64.b64decode(proc.stdout, validate=True)


def review(app: Path = APP) -> dict:
    report, _ = current.review(app)
    if report["status"] != "review_required_not_installable":
        raise ValueError("current official source review is not complete")
    original = json.loads(OLD_REVIEW.read_text())
    if original.get("version") != old.VERSION or original.get("source_archives") != old.ARCHIVES:
        raise ValueError("old hardcoded UI source provenance differs")
    app_path = app / "Contents/Resources/app.asar"
    app_data = app_path.read_bytes()
    offset, _, header = read_asar_header(app_data, app_path)
    translations = {**json.loads((ROOT / "resources/ui-reviewed-zh-CN.json").read_text()),
                    **json.loads((ROOT / "resources/candidates/claude-desktop-4.23118.1/hardcoded-ui-zh-CN.json").read_text())}

    def load(path: str) -> bytes:
        archive, relative = path.split("/", 1)
        if archive == "app.asar":
            result = read_asar_entry(app_data, offset, header, relative)
        elif archive == "ion-dist.asar":
            result = read_frontend_entry(app, relative)
        else:
            raise ValueError("unapproved hardcoded UI archive")
        if result is None:
            raise ValueError("new-build hardcoded UI source not found")
        return result

    files = pin_rules(original["files"], PATHS, load,
                      lambda path, raw, rules: patch_with_original_rules(
                          path, raw, rules, translations))
    if (sum(rule["expected"] for item in files.values() for rule in item["rules"]) != 71
            or current.update_guard.bundle_identity(app)["archives"]
               != report["source_archives_sha256"]):
        raise ValueError("hardcoded UI occurrence count or signed source changed")
    return {"version": current.VERSION, "status": "review_required_not_installable",
            "source_archives_sha256": report["source_archives_sha256"],
            "files": files, "installed": False, "visual_ui_verified": False}


if __name__ == "__main__":
    data = review()
    if TARGET.exists() or TARGET.is_symlink():
        raise SystemExit("current hardcoded UI review already exists")
    with TARGET.open("x") as output:
        output.write(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"version": data["version"], "status": data["status"],
                      "files": len(data["files"]),
                      "occurrences": sum(rule["expected"] for item in data["files"].values()
                                         for rule in item["rules"]),
                      "installed": False, "visual_ui_verified": False}))
