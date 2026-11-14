#!/usr/bin/env python3
"""Build only source-verified, non-installable Claude 5.11492.5 locale JSON.

This does not modify, sign, launch or install Claude. Hardcoded UI, Cowork,
sidebar and GUI acceptance remain separate release gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

import claude5_candidate as old
import review_claude_5114925 as review
from upstream_patch_lib import read_asar_entry, read_asar_header, read_frontend_entry

ROOT = Path(__file__).resolve().parents[1]
APP = Path("/Applications/Claude.app")
OUT_ROOT = Path("/Volumes/Mars-私人服务器/内置盘写入统一管理/01-编译与构建/Claude-CN")
CANDIDATE = ROOT / "resources/candidates/claude-desktop-5.11492.5"


def compose_catalog(english: dict[str, str], old_english: dict[str, str],
                    old_chinese: dict[str, str], additions: list[dict[str, str]],
                    *, overrides: dict[str, str] | None = None) -> dict[str, str]:
    if (not all(isinstance(x, dict) for x in additions)
            or not all(isinstance(k, str) and isinstance(v, str)
                       for k, v in english.items())):
        raise ValueError("catalog or authored reviews invalid")
    reused = {}
    for key, source in english.items():
        if key not in old_english:
            continue
        if old_english[key] != source:
            raise ValueError("changed source ID cannot reuse an older translation")
        translated = old_chinese.get(key)
        if not isinstance(translated, str) or not translated.strip():
            raise ValueError("unchanged source has no reviewed translation")
        reused[key] = translated
    authored = {}
    for batch in additions:
        if set(batch) & (set(reused) | set(authored)):
            raise ValueError("authored review ID overlap")
        if not set(batch) <= set(english):
            raise ValueError("authored review outside official source")
        authored.update(batch)
    if set(reused) | set(authored) != set(english):
        raise ValueError("new-version catalog has uncovered IDs")
    if overrides is not None:
        if (not isinstance(overrides, dict) or not set(overrides) <= set(reused)
                or any(not isinstance(text, str) or not text.strip()
                       for text in overrides.values())):
            raise ValueError("reviewed old-ID override invalid")
        reused.update(overrides)
    return {key: reused.get(key, authored.get(key)) for key in english}


def _current_catalogs(app: Path) -> dict[str, dict[str, str]]:
    source = app / "Contents/Resources/app.asar"
    raw = source.read_bytes()
    size, _, header = read_asar_header(raw, source)
    desktop = read_asar_entry(raw, size, header, old.PATHS["desktop"][1])
    if desktop is None:
        raise ValueError("current desktop catalog is missing")
    frontend = read_frontend_entry(app, old.PATHS["frontend"][1])
    dynamic = read_frontend_entry(app, old.PATHS["dynamic"][1])
    if frontend is None or dynamic is None:
        raise ValueError("current frontend catalog is missing")
    return {"desktop": json.loads(desktop), "frontend": json.loads(frontend),
            "dynamic": json.loads(dynamic)}


def _authored(group: str) -> list[dict[str, str]]:
    if group == "dynamic":
        return []
    if group == "desktop":
        files = [CANDIDATE / "desktop-authored-draft.json",
                 CANDIDATE / "desktop-proposals-reviewed.json"]
    else:
        files = sorted(CANDIDATE.glob("frontend-authored-draft-*.json"))
        files += [CANDIDATE / "frontend-memory-reviewed.json"]
    batches = []
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise ValueError("source-bound authored review unavailable")
        item = json.loads(path.read_text())
        if (item.get("version") != review.VERSION
                or item.get("group", "desktop") != group
                or item.get("status") != "draft_not_installable"):
            raise ValueError("authored review is not bound to current source")
        batches.append(item["translations"])
    return batches


def _validate_catalog(group: str, english: dict[str, str], chinese: dict[str, str]) -> None:
    # ICU parses balanced rich-text tags as nodes, so its locale-aware AST
    # comparison checks nesting on every reachable branch. Raw tag order is
    # not invariant under Chinese word order; omitted unreachable `one`
    # branches likewise need not duplicate tags from the source catalog.
    process = subprocess.run(
        ["node", str(ROOT / "scripts/icu_literals.cjs")],
        input=json.dumps({"operation": "validate-batch-locale", "locale": "zh-CN",
                          "source": english,
                          "translated": chinese}, ensure_ascii=False),
        capture_output=True, text=True, timeout=180, check=False,
    )
    if process.returncode:
        raise ValueError("locale ICU validator failed before writing outputs")
    result = json.loads(process.stdout)
    invalid = result.get("invalid")
    if invalid or result.get("checked") != len(english):
        sample = ",".join(invalid[:12]) if isinstance(invalid, list) else "unknown"
        raise ValueError(f"{group} ICU reachable structure differs: count="
                         f"{len(invalid) if isinstance(invalid, list) else 'unknown'} ids={sample}")


def assemble(app: Path, output: Path) -> dict:
    if not Path("/Volumes/Mars-私人服务器").is_mount():
        raise ValueError("external disk unavailable")
    parent = output.parent.resolve(strict=True)
    if (output.exists() or output.is_symlink() or parent != OUT_ROOT.resolve(strict=True)
            or app != APP):
        raise ValueError("output must be a fresh Claude-CN external build slot")
    report, _ = review.review(app)
    if (report["status"] != "review_required_not_installable"
            or report["frontend_candidate_id_coverage"] != 44218
            or report["desktop_candidate_id_coverage"] != 1310):
        raise ValueError("official current-version review is incomplete")
    current = _current_catalogs(app)
    old_source = old.read_source(old.BASE / "Claude.app")
    corrections = json.loads((CANDIDATE / "reused-icu-corrections-reviewed.json").read_text())
    assembled = {}
    for group, (archive, path) in old.PATHS.items():
        source = json.loads(old_source(archive, path))
        prior = json.loads((old.CANDIDATE / f"{group}-zh-CN.json").read_text())
        group_review = corrections["groups"].get(group, {})
        assembled[group] = compose_catalog(current[group], source, prior, _authored(group),
                                            overrides=group_review.get("translations", {}))
        _validate_catalog(group, current[group], assembled[group])
    if review.update_guard.bundle_identity(app)["archives"] != report["source_archives_sha256"]:
        raise ValueError("official app changed while assembling locale catalogs")
    output.mkdir(mode=0o755)
    manifest = {"version": review.VERSION, "status": "candidate_not_installable",
                "installed": False, "visual_ui_verified": False,
                "source_archives_sha256": report["source_archives_sha256"],
                "groups": {}}
    for group, entries in assembled.items():
        data = (json.dumps(entries, ensure_ascii=False, indent=2) + "\n").encode()
        path = output / f"{group}-zh-CN.json"
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        manifest["groups"][group] = {"ids": len(entries),
                                     "sha256": hashlib.sha256(data).hexdigest()}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False,
                                                   sort_keys=True, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(assemble(APP, args.output_dir.absolute()), ensure_ascii=False))
