#!/usr/bin/env python3
"""Verify the 4.23118 locale, UI, two language gates and Cowork source together.

No app is copied, patched, signed or opened by this command. Inference,
actual visible Chinese and real-session Cowork behavior require later stages.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import preview_current as preview
from patch_cowork_compaction import NEW_ENTRY, MARKER, patch_host_loop_source
from patch_whitelist_423118 import load_reviewed as load_locale_gates
from update_guard import catalog_fingerprint
from upstream_patch_lib import read_asar_entry, read_asar_header
import verify_ui_423118 as ui

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "resources/candidates/claude-desktop-4.23118.0"


def assert_disjoint(*groups: set[str]) -> int:
    total = sum(len(group) for group in groups)
    if total == 0 or len(set().union(*groups)) != total:
        raise ValueError("reviewed ASAR source paths overlap or are empty")
    return total


def validate_catalog(english: dict, candidate: bytes, spec: dict) -> tuple[int, dict]:
    if catalog_fingerprint(english) != spec.get("official_english_catalog_sha256"):
        raise ValueError("official English catalog differs from the reviewed source")
    chinese = json.loads(candidate)
    if (not isinstance(chinese, dict) or chinese.keys() != english.keys()
            or len(chinese) != spec.get("message_ids")):
        raise ValueError("candidate message IDs differ from the official source")
    if hashlib.sha256(candidate).hexdigest() != spec.get("candidate_sha256"):
        raise ValueError("candidate translation catalog fingerprint changed")
    return len(chinese), chinese


def verify(app: Path) -> dict:
    ui_report = ui.verify(app)
    manifest = json.loads((CANDIDATE / "manifest.json").read_text())
    if (manifest.get("target_version") != ui_report["version"]
            or manifest.get("status") != "locale_candidate_only_not_installed"):
        raise ValueError("new-build locale candidate provenance changed")
    all_catalogs = preview.load_catalogs(app)
    candidate_payload = {}
    for group in preview.PATHS:
        data = (CANDIDATE / f"{group}-zh-CN.json").read_bytes()
        _, chinese = validate_catalog(all_catalogs[group]["en"], data, manifest[group])
        candidate_payload[group] = {"en": all_catalogs[group]["en"], "zh": chinese,
                                    "strictSelections": True}
    icu = subprocess.run(
        ["node", str(ROOT / "scripts/audit_messages.cjs")],
        input=json.dumps({"catalogs": candidate_payload}, ensure_ascii=False),
        text=True, capture_output=True, timeout=180, check=False,
    )
    checked = json.loads(icu.stdout)
    if (icu.returncode or checked.get("errors")
            or checked.get("catalogs") != {name: len(pack["zh"])
                                              for name, pack in candidate_payload.items()}):
        raise ValueError("candidate ICU and selection compatibility failed")

    archive = app / "Contents/Resources/app.asar"
    raw = archive.read_bytes()
    size, _, header = read_asar_header(raw, archive)
    source = read_asar_entry(raw, size, header, NEW_ENTRY)
    if source is None:
        raise ValueError("official Cowork HostLoop is absent")
    patched = patch_host_loop_source(
        source.decode(), "/dev/null", "/dev/null", "local-SYNTHETIC-REVIEW-ONLY",
        version="4.23118.0",
    )
    if patched.count(MARKER) != 1:
        raise ValueError("official Cowork hook is not uniquely bound")
    parsed = subprocess.run(
        ["node", "-e", 'const fs=require("fs"),acorn=require("acorn");'
         'acorn.parse(fs.readFileSync(0,"utf8"),{ecmaVersion:"latest",sourceType:"module"})'],
        input=patched, text=True, capture_output=True, timeout=30, check=False,
        cwd=ROOT,
    )
    if parsed.returncode:
        raise ValueError("patched Cowork HostLoop JavaScript is invalid")

    ui_files = set(json.loads(ui.MANIFEST.read_text())["files"])
    locale_files = {f"ion-dist.asar/{name}" for name in load_locale_gates()}
    catalog_files = {"app.asar/resources/i18n/zh-CN.json", "ion-dist.asar/i18n/zh-CN.json",
                     "ion-dist.asar/i18n/dynamic/zh-CN.json"}
    paths = assert_disjoint(ui_files, locale_files, {f"app.asar/{NEW_ENTRY}"}, catalog_files)
    if ui_report["files"] != len(ui_files) or ui_report["locale_gates"] != len(locale_files):
        raise ValueError("source verification and manifest count differ")
    if hashlib.sha256(raw).hexdigest() != json.loads(ui.MANIFEST.read_text())["source_archives"]["app.asar"]:
        raise ValueError("official signed app changed during combined verification")
    return {"version": ui_report["version"], "signed_official_source": True,
            "catalog_messages": sum(len(pack["zh"]) for pack in candidate_payload.values()),
            "catalog_groups": len(candidate_payload), "reviewed_ui_strings": ui_report["reviewed_replacements"],
            "locale_gates": ui_report["locale_gates"], "cowork_synthetic_hook": "syntax_verified",
            "disjoint_archive_entries": paths, "installed": False, "visual_ui": "not_checked",
            "real_cowork_session": "not_checked"}


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--official-app", type=Path, required=True)
    args = cli.parse_args()
    try:
        print(json.dumps(verify(args.official_app), sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps({"status": "unverified", "reason_type": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
