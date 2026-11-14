#!/usr/bin/env python3
"""Read-only 5.11492.5 source review; optional provisional review output only.

No app patch, version downgrade, launch, language switch, or install path.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path

import claude5_candidate as previous
import review_claude_511492 as prior
import update_guard
from upstream_patch_lib import read_asar_entry, read_asar_header, read_frontend_entry

ROOT = Path(__file__).resolve().parents[1]
VERSION = "5.11492.5"
ARCHIVES = {
    "Contents/Resources/app.asar": "2c515441d5d0504a96ac3e49bcc4c1244085c0e76ad23ae58ddd8f736100479e",
    "Contents/Resources/ion-dist.asar": "1d55d633d14aede748af3cb22c244f4ddc2f795d9b1153b175b993ae5d46aa1b",
}
SOURCE = ROOT / "resources/candidates/claude-desktop-5.11492.2"
DESTINATION = ROOT / "resources/candidates/claude-desktop-5.11492.5"


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def rebind_draft(draft: dict, english: dict[str, str], archives: dict[str, str],
                 *, group: str) -> dict:
    if (not isinstance(draft, dict) or draft.get("version") != "5.11492.2"
            or draft.get("status") != "draft_not_installable"
            or group not in {"desktop", "frontend"}
            or (group == "frontend" and draft.get("group") != "frontend")
            or (group == "desktop" and draft.get("group") not in (None, "desktop"))
            or not isinstance(draft.get("source_english_sha256"), dict)
            or not isinstance(draft.get("translations"), dict)
            or set(draft["source_english_sha256"]) != set(draft["translations"])
            or not isinstance(archives, dict)):
        raise ValueError("old draft cannot be rebound")
    for key, expected in draft["source_english_sha256"].items():
        if (not isinstance(key, str) or not isinstance(expected, str)
                or key not in english or digest(english[key].encode()) != expected
                or not isinstance(draft["translations"][key], str)
                or not draft["translations"][key].strip()):
            raise ValueError("new source text differs from old authored draft")
    return {**draft, "version": VERSION, "source_archives_sha256": dict(archives),
            "status": "draft_not_installable"}


def review(app: Path) -> tuple[dict, dict[str, dict]]:
    if app.is_symlink() or not app.is_dir():
        raise ValueError("official Claude source unavailable")
    update_guard.require_official_signature(app)
    identity = update_guard.bundle_identity(app)
    if identity != {"version": VERSION, "build": VERSION, "archives": ARCHIVES}:
        raise ValueError("current official source fingerprint changed")
    old_source = previous.read_source(previous.BASE / "Claude.app")
    old_manifest = previous.load(previous.CANDIDATE / "manifest.json")
    if old_manifest.get("target_version") != previous.VERSION:
        raise ValueError("previous reviewed translation source changed")
    raw_app = (app / "Contents/Resources/app.asar").read_bytes()
    app_size, _, app_header = read_asar_header(raw_app, app / "Contents/Resources/app.asar")
    raw_frontend = (app / "Contents/Resources/ion-dist.asar").read_bytes()
    front_size, _, front_header = read_asar_header(
        raw_frontend, app / "Contents/Resources/ion-dist.asar")
    old_english, old_chinese, english, plans = {}, {}, {}, {}
    memory: dict[str, set[str]] = {}
    for group, (archive, path) in previous.PATHS.items():
        translation_path = previous.CANDIDATE / f"{group}-zh-CN.json"
        if digest(translation_path.read_bytes()) != old_manifest.get(
                "generated_files_sha256", {}).get(translation_path.name):
            raise ValueError("old reviewed translation bytes changed")
        old_english[group] = json.loads(old_source(archive, path))
        old_chinese[group] = previous.load(translation_path)
        current = (read_asar_entry(raw_app, app_size, app_header, path)
                   if archive == "app.asar" else read_frontend_entry(app, path))
        if current is None:
            raise ValueError("new English catalog missing")
        english[group] = json.loads(current)
        for key, text in old_english[group].items():
            translated = old_chinese[group].get(key)
            if isinstance(text, str) and isinstance(translated, str) and translated != text:
                memory.setdefault(text, set()).add(translated)
    official = read_asar_entry(raw_app, app_size, app_header, "resources/i18n/zh-Hans.json")
    official_desktop = json.loads(official) if official is not None else {}
    groups = {}
    for group in previous.PATHS:
        plan = prior.plan_catalog(old_english[group], english[group], old_chinese[group],
                                  official_desktop if group == "desktop" else {}, memory)
        plans[group] = plan
        groups[group] = {
            "old_ids": len(old_english[group]), "current_ids": len(english[group]),
            "unchanged_id_candidates": len(plan["reused"]),
            "changed_english_ids": len(plan["changed_ids"]),
            "pending_review_ids": len(plan["pending_ids"]),
            "removed_ids": len(plan["removed_ids"]),
            "official_new_proposals": len(plan["official_proposals"]),
            "unique_memory_new_proposals": len(plan["memory_proposals"]),
        }
    prior_gates = json.loads((SOURCE / "language-whitelist-review.json").read_text())
    if (prior_gates.get("version") != "5.11492.2"
            or prior_gates.get("status") != "review_required_not_installable"
            or len(prior_gates.get("files", {})) != 3):
        raise ValueError("old gate review changed")
    gate_paths = []

    def collect(entries, prefix=""):
        for name, item in entries.items():
            path = f"{prefix}/{name}".lstrip("/")
            if isinstance(item, dict) and isinstance(item.get("files"), dict):
                collect(item["files"], path)
            elif path.endswith(".js"):
                gate_paths.append(path)

    collect(front_header["files"])
    gates = prior.gate_candidates(prior_gates["files"], gate_paths,
        lambda path: read_asar_entry(raw_frontend, front_size, front_header, path))
    if gates != prior_gates["files"]:
        raise ValueError("new gate semantics or source changed")
    old_desktop = json.loads((SOURCE / "desktop-authored-draft.json").read_text())
    old_frontend = json.loads((SOURCE / "frontend-authored-draft-01.json").read_text())
    rebound_desktop = rebind_draft(old_desktop, english["desktop"], ARCHIVES, group="desktop")
    rebound_frontend = rebind_draft(old_frontend, english["frontend"], ARCHIVES, group="frontend")
    if (set(rebound_desktop["translations"]) != set(plans["desktop"]["pending_ids"])
            or not set(rebound_frontend["translations"]) <= set(plans["frontend"]["pending_ids"])):
        raise ValueError("authored drafts no longer cover reviewed pending IDs")
    # Recheck placeholders and ICU against current signed bytes. Restore the
    # older reviewer's module constant; other callers never inherit this pin.
    old_version = prior.VERSION
    try:
        prior.VERSION = VERSION
        prior.validate_desktop_draft(rebound_desktop, english["desktop"], identity)
        prior.validate_frontend_draft(rebound_frontend, english["frontend"], identity,
                                      set(plans["frontend"]["pending_ids"]))
    finally:
        prior.VERSION = old_version
    if update_guard.bundle_identity(app) != identity:
        raise ValueError("official app changed during review")
    report = {"version": VERSION, "status": "review_required_not_installable",
              "source_archives_sha256": ARCHIVES, "groups": groups,
              "unchanged_gate_count": len(gates),
              "draft_counts": {"desktop": len(rebound_desktop["translations"]),
                               "frontend": len(rebound_frontend["translations"])},
              "installed": False, "visual_ui_verified": False}
    return report, {"review.json": report,
                    "language-whitelist-review.json": {"version": VERSION,
                        "status": "review_required_not_installable",
                        "source_archives_sha256": ARCHIVES, "files": gates},
                    "desktop-authored-draft.json": rebound_desktop,
                    "frontend-authored-draft-01.json": rebound_frontend}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=Path("/Applications/Claude.app"))
    parser.add_argument("--publish-review", action="store_true")
    args = parser.parse_args()
    with contextlib.redirect_stdout(io.StringIO()):
        report, outputs = review(args.app)
    if args.publish_review:
        if DESTINATION.exists() or DESTINATION.is_symlink():
            raise ValueError("review destination already exists")
        DESTINATION.mkdir(mode=0o755)
        for name, payload in outputs.items():
            (DESTINATION / name).write_text(json.dumps(payload, ensure_ascii=False,
                sort_keys=True, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
