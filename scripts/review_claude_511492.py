#!/usr/bin/env python3
"""Read-only, version-bound 5.11492.2 localization review; never install."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path
import plistlib
import re

import claude5_candidate as previous
import update_guard
from upstream_patch_lib import read_asar_entry, read_asar_header, read_frontend_entry


VERSION = "5.11492.2"
ARCHIVES = {
    "Contents/Resources/app.asar": "1e38950b8666ee7a271fdac0fc407f62bf47dc692e2ecd7f2f4f8bf6000a78fc",
    "Contents/Resources/ion-dist.asar": "6717da52172b568ce601f9ef28411a7c35afb51ca7d5e0518f3089d1e42b0c15",
}


def plan_catalog(old_en: dict[str, str], new_en: dict[str, str],
                 old_zh: dict[str, str], official_zh: dict[str, str],
                 memory: dict[str, set[str]]) -> dict:
    """Safe reuse by unchanged ID; all new translations remain proposals."""
    if not all(isinstance(value, dict) for value in (old_en, new_en, old_zh, official_zh, memory)):
        raise ValueError("catalogs must be objects")
    if any(not isinstance(key, str) or not isinstance(value, str)
           for catalog in (old_en, new_en, old_zh, official_zh)
           for key, value in catalog.items()):
        raise ValueError("catalog entries must be strings")
    shared = set(old_en) & set(new_en)
    changed = sorted(key for key in shared if old_en[key] != new_en[key])
    reused = {key: old_zh[key] for key in sorted(shared)
              if old_en[key] == new_en[key] and key in old_zh}
    official_proposals: dict[str, str] = {}
    memory_proposals: dict[str, str] = {}
    pending = set(changed)
    ambiguous = []
    for key in sorted(set(new_en) - set(old_en)):
        source = new_en[key]
        official = official_zh.get(key)
        if isinstance(official, str) and re.search(r"[\u4e00-\u9fff]", official):
            official_proposals[key] = official
            continue
        candidates = memory.get(source, set())
        if len(candidates) == 1:
            memory_proposals[key] = next(iter(candidates))
        else:
            pending.add(key)
            if len(candidates) > 1:
                ambiguous.append(key)
    return {"status": "review_required_not_installable", "reused": reused,
            "official_proposals": official_proposals,
            "memory_proposals": memory_proposals,
            "pending_ids": sorted(pending), "ambiguous_ids": sorted(ambiguous),
            "changed_ids": changed, "removed_ids": sorted(set(old_en)-set(new_en))}


def review(app: Path) -> dict:
    if app.is_symlink() or not app.is_dir():
        raise ValueError("current official Claude app unavailable")
    update_guard.require_official_signature(app)
    identity = update_guard.bundle_identity(app)
    if (identity.get("version") != VERSION or identity.get("build") != VERSION
            or identity.get("archives") != ARCHIVES):
        raise ValueError("official source version or archive fingerprints changed")
    old_app = previous.BASE / "Claude.app"
    old_entry = previous.read_source(old_app)
    pack = previous.CANDIDATE
    manifest = previous.load(pack / "manifest.json")
    if manifest.get("target_version") != previous.VERSION:
        raise ValueError("previous translation pack is not the reviewed version")
    raw_app = (app / "Contents/Resources/app.asar").read_bytes()
    size, _, header = read_asar_header(raw_app, app / "Contents/Resources/app.asar")
    old_english, old_chinese, current_english = {}, {}, {}
    for group, (archive, path) in previous.PATHS.items():
        translation_path = pack / f"{group}-zh-CN.json"
        digest = manifest.get("generated_files_sha256", {}).get(translation_path.name)
        if hashlib.sha256(translation_path.read_bytes()).hexdigest() != digest:
            raise ValueError("previous reviewed translation changed")
        old_english[group] = json.loads(old_entry(archive, path))
        old_chinese[group] = previous.load(translation_path)
        current = (read_asar_entry(raw_app, size, header, path)
                   if archive == "app.asar" else read_frontend_entry(app, path))
        if current is None:
            raise ValueError("new official catalog path is unavailable")
        current_english[group] = json.loads(current)
    official_raw = read_asar_entry(raw_app, size, header, "resources/i18n/zh-Hans.json")
    official_desktop = json.loads(official_raw) if official_raw is not None else {}
    memory: dict[str, set[str]] = {}
    for group in previous.PATHS:
        for key, text in old_english[group].items():
            translated = old_chinese[group].get(key)
            if isinstance(text, str) and isinstance(translated, str) and translated and translated != text:
                memory.setdefault(text, set()).add(translated)
    summary = {"status": "review_required_not_installable", "previous_version": previous.VERSION,
               "current_version": VERSION, "current_archives_sha256": identity["archives"],
               "groups": {}}
    for group in previous.PATHS:
        plan = plan_catalog(old_english[group], current_english[group], old_chinese[group],
                            official_desktop if group == "desktop" else {}, memory)
        summary["groups"][group] = {
            "old_ids": len(old_english[group]), "current_ids": len(current_english[group]),
            "added_ids": len(set(current_english[group])-set(old_english[group])),
            "unchanged_id_translations_reusable": len(plan["reused"]),
            "official_new_proposals": len(plan["official_proposals"]),
            "unique_memory_new_proposals": len(plan["memory_proposals"]),
            "pending_review_ids": len(plan["pending_ids"]),
            "ambiguous_memory_ids": len(plan["ambiguous_ids"]),
            "changed_english_ids": len(plan["changed_ids"]),
            "removed_ids": len(plan["removed_ids"]),
        }
    summary["old_js_gate_paths_missing"] = sum(
        read_frontend_entry(app, path) is None for path in previous.GATES
    )
    if update_guard.bundle_identity(app) != identity:
        raise ValueError("official app changed during read-only inspection")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=Path("/Applications/Claude.app"))
    args = parser.parse_args()
    # Existing ASAR integrity readers print progress to stdout. Preserve a
    # single machine-readable result; failed verification still raises.
    with contextlib.redirect_stdout(io.StringIO()):
        result = review(args.app)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
