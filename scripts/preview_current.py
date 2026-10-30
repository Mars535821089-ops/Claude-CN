#!/usr/bin/env python3
"""Prepare a fingerprint-pinned, read-only localization review for the current app.

Never installs, changes a locale, quits Claude, or signs an application.
Merged official translations and unresolved source messages stay in local output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path

import audit_ui_sources as audit
import repair_macos_429 as repair
from official_release import compare_latest, fetch_latest
from ui_html import apply_html, scan_html
from update_guard import catalog_fingerprint, decode_catalog, require_official_signature

ROOT = repair.ROOT
PROFILE = repair.RESOURCES / "current-source.json"
REVIEWED = repair.RESOURCES / "current-reviewed-zh-CN.json"
ADDITIONAL = repair.RESOURCES / "current-additional-zh-CN.json"
PRESERVED = repair.RESOURCES / "current-preserved.json"
PATHS = {
    "desktop": ("resources/i18n/en-US.json", "resources/i18n/zh-Hans.json", "desktop-zh-CN.json"),
    "frontend": ("i18n/en-US.json", "i18n/zh-Hans.json", "frontend-zh-CN.json"),
    "dynamic": ("i18n/dynamic/en-US.json", "i18n/dynamic/zh-Hans.json", "dynamic-zh-CN.json"),
}


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def verify_identity(app: Path, profile: dict) -> dict:
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    if (info.get("CFBundleShortVersionString") != profile["version"]
            or info.get("CFBundleVersion") != profile["build"]
            or info.get("CFBundleIdentifier") != "com.anthropic.claudefordesktop"):
        raise ValueError("Current preview supports only the reviewed version, build and bundle ID")
    require_official_signature(app)
    actual = archive_hashes(app)
    if actual != profile["archives"]:
        raise ValueError("Unknown current UI archive fingerprint; no preview was published")
    return {"version": info["CFBundleShortVersionString"], "build": info["CFBundleVersion"],
            "team": "Q6L2SF6YDW", "signature_verified": True, "archives": actual}


def archive_hashes(app: Path) -> dict[str, str]:
    return {str(relative): sha256((app / relative).read_bytes())
            for relative in (repair.APP_ASAR_REL, repair.patch.FRONTEND_ASAR_REL)}


def load_catalogs(app: Path) -> dict:
    desktop = app / repair.APP_ASAR_REL
    data = desktop.read_bytes()
    size, _, header = repair.patch.read_asar_header(data, desktop)

    def read(group, relative):
        if group == "desktop":
            return repair.patch.read_asar_entry(data, size, header, relative)
        return repair.patch.read_frontend_entry(app, relative)

    result = {}
    for group, (source, official, _) in PATHS.items():
        raw = read(group, source)
        if raw is None:
            raise ValueError(f"Current English catalog is missing: {source}")
        translated = read(group, official)
        result[group] = {
            "en": decode_catalog(raw, group),
            "official": decode_catalog(translated, group) if translated is not None else {},
        }
    return result


def merge_catalog(en: dict, official: dict, legacy: dict, reviewed: dict, memory: dict,
                  preserved: set[str] | None = None) -> dict:
    preserved = {"Claude Code"} if preserved is None else preserved
    translated, missing, provenance = {}, {}, {}
    candidates = {}
    for key in en.keys() & legacy.keys():
        candidates.setdefault(en[key], set()).add(legacy[key])
    exact_legacy = {source: next(iter(targets)) for source, targets in candidates.items()
                    if len(targets) == 1}
    for key, source in en.items():
        if key in official:
            target, origin = official[key], "official_local"
        elif source in reviewed:
            target, origin = reviewed[source], "reviewed_current"
        elif source in preserved:
            target, origin = source, "reviewed_preserved"
        elif key in legacy:
            target, origin = legacy[key], "legacy_id"
        elif source in exact_legacy:
            target, origin = exact_legacy[source], "legacy_exact_text"
        elif source in memory:
            target, origin = memory[source], "reviewed_exact_text"
        else:
            missing[key] = source
            continue
        if not isinstance(target, str) or not target:
            raise ValueError(f"Invalid translation for current message {key}")
        translated[key] = target
        provenance[key] = origin
    return {"translations": translated, "missing": missing, "provenance": provenance}


def generate_ui(app: Path, version: str, dictionary: dict) -> dict:
    with (tempfile.TemporaryFile(mode="w+", encoding="utf-8") as payload,
          tempfile.TemporaryDirectory(prefix="claude-ui-dictionary-") as directory):
        resource = Path(directory) / "dictionary.json"
        resource.write_text(json.dumps(dictionary, ensure_ascii=False), encoding="utf-8")
        for item in audit.sources(app):
            payload.write(json.dumps(item, ensure_ascii=False) + "\n")
        payload.seek(0)
        result = subprocess.run(
            ["node", str(ROOT / "scripts/ui_localization.cjs"), "--preview-manifest",
             version, str(resource)], stdin=payload, capture_output=True, text=True, check=True,
        )
    manifest = json.loads(result.stdout)
    for item in audit.bundle_entries(app):
        if not item["file"].endswith(".html"):
            continue
        prefix = item["file"] + "#inline-"
        inline = {file.removeprefix(prefix): manifest["files"].pop(file)
                  for file in list(manifest["files"]) if file.startswith(prefix)}
        groups = {}
        for finding in scan_html(item["source"]).findings:
            if finding["text"] in dictionary:
                key = (finding["context"], finding["text"])
                rule = groups.setdefault(key, {"context": key[0], "source": key[1], "expected": 0})
                rule["expected"] += 1
        if groups or inline:
            rules = list(groups.values())
            patched = apply_html(item["source"], rules, dictionary, inline)
            manifest["files"][item["file"]] = {
                "sha256": sha256(item["source"].encode()),
                "patched_sha256": sha256(patched.encode()),
                "rules": rules, **({"inline_scripts": inline} if inline else {}),
            }
    manifest["preview_only"] = True
    return manifest


def load_reviewed() -> tuple[dict[str, str], set[str]]:
    reviewed = repair.json_file(REVIEWED)
    for source, target in repair.json_file(ADDITIONAL).items():
        if source in reviewed and target != reviewed[source]:
            raise ValueError(f"Conflicting current reviewed translation: {source}")
        reviewed[source] = target
    preserved = json.loads(PRESERVED.read_text())
    if not isinstance(preserved, list) or not all(isinstance(item, str) for item in preserved):
        raise ValueError("Current preserved values must be a list of exact source strings")
    if len(set(preserved)) != len(preserved) or set(preserved) & reviewed.keys():
        raise ValueError("Duplicate or translated current preserved value")
    return reviewed, {"Claude Code", *preserved}


def prepare(app: Path, output: Path, check_latest: bool) -> dict:
    if output.exists() or output.is_symlink():
        raise ValueError(f"Preview output already exists; choose a new directory: {output}")
    output = output.resolve()
    if output.is_relative_to(app.resolve()) or output == app.resolve():
        raise ValueError("Preview output must not be inside the application")
    if output.is_relative_to(ROOT.resolve()):
        raise ValueError("Merged official resources must stay in a local directory outside the repository")
    profile = json.loads(PROFILE.read_text())
    identity = verify_identity(app, profile)
    latest = compare_latest(profile["version"], fetch_latest()) if check_latest else None
    catalogs = load_catalogs(app)
    reviewed, preserved = load_reviewed()
    memory = repair.json_file(repair.RESOURCES / "ui-reviewed-zh-CN.json")
    pairs = json.loads((repair.RESOURCES / "frontend-hardcoded-zh-CN.json").read_text())
    if not isinstance(pairs, list) or not all(
        isinstance(pair, list) and len(pair) == 2
        and all(isinstance(value, str) for value in pair) for pair in pairs
    ):
        raise ValueError("Invalid legacy hardcoded translation memory")
    hardcoded = dict(pairs)
    merged = {}
    counts = {}
    for group, pack in catalogs.items():
        if catalog_fingerprint(pack["en"]) != profile["catalogs"][group]["en"]:
            raise ValueError(f"Current source catalog fingerprint differs: {group}")
        if catalog_fingerprint(pack["official"]) != profile["catalogs"][group]["official_zh_Hans"]:
            raise ValueError(f"Current official Chinese catalog fingerprint differs: {group}")
        legacy = repair.json_file(repair.RESOURCES / PATHS[group][2])
        merged[group] = merge_catalog(pack["en"], pack["official"], legacy,
                                      reviewed, {**hardcoded, **memory}, preserved)
        translations = merged[group]["translations"]
        provenance = merged[group]["provenance"]
        counts[group] = {
            "source": len(pack["en"]), "resolved": len(translations),
            "translated": sum(value != pack["en"][key] for key, value in translations.items()),
            "unchanged": sum(value == pack["en"][key] for key, value in translations.items()),
            "unreviewed": len(merged[group]["missing"]),
            "official_local": sum(origin == "official_local" for origin in provenance.values()),
            "reviewed_current": sum(origin == "reviewed_current" for origin in provenance.values()),
            "legacy_id": sum(origin == "legacy_id" for origin in provenance.values()),
            "legacy_exact_text": sum(origin == "legacy_exact_text" for origin in provenance.values()),
            "reviewed_exact_text": sum(origin == "reviewed_exact_text" for origin in provenance.values()),
            "reviewed_preserved": sum(origin == "reviewed_preserved" for origin in provenance.values()),
        }
    reviewed_icu = {source: target for source, target in reviewed.items() if "{…}" not in source}
    checks = {
        group: {"en": {key: catalogs[group]["en"][key] for key in pack["translations"]},
                "zh": pack["translations"]} for group, pack in merged.items()
    }
    checks["reviewed_current"] = {
        "en": {str(index): source for index, source in enumerate(reviewed_icu)},
        "zh": {str(index): target for index, target in enumerate(reviewed_icu.values())},
        "strictSelections": True,
    }
    validation = subprocess.run(
        ["node", str(ROOT / "scripts/audit_messages.cjs")],
        input=json.dumps({"catalogs": checks}), capture_output=True, text=True, check=True,
    )
    ui = generate_ui(app, profile["version"], {**memory, **reviewed})
    locations = sum(sum(rule["expected"] for rule in spec["rules"])
                    + sum(sum(rule["expected"] for rule in inline["rules"])
                          for inline in spec.get("inline_scripts", {}).values())
                    for spec in ui["files"].values())
    if verify_identity(app, profile) != identity:
        raise ValueError("Claude changed during preview; retry against a stable official app")
    report = {
        "status": "review_required", "application_modified": False,
        "installer_compatible": False, "legacy_installer_version": repair.VERSION,
        "installed": identity, "catalogs": counts,
        "catalog_validation": json.loads(validation.stdout),
        "official_chinese": {
            "desktop_matching_ids": len(catalogs["desktop"]["official"].keys()
                                       & catalogs["desktop"]["en"].keys()),
            "frontend_matching_ids": len(catalogs["frontend"]["official"].keys()
                                        & catalogs["frontend"]["en"].keys()),
            "complete_bundled_catalogs": all(
                pack["en"].keys() <= pack["official"].keys() for pack in catalogs.values()),
            "native_switch_verified": False,
        },
        "input_sha256": {name: sha256((repair.RESOURCES / name).read_bytes()) for name in [
            "current-source.json", "current-reviewed-zh-CN.json", "current-additional-zh-CN.json",
            "current-preserved.json", "ui-reviewed-zh-CN.json",
            "frontend-hardcoded-zh-CN.json", *(item[2] for item in PATHS.values()),
        ]},
        "ui_preview": {"files": len(ui["files"]), "locations": locations,
                       "ast_structure_verified": True},
        "limits": [
            "Preview only; never install with the 4.29.0 installer.",
            "Legacy messages are reused by ID; old English source is not available for semantic diff.",
            "Unreviewed source messages remain explicit gaps; no automatic English fallback.",
            "Partial desktop zh-Hans is not a complete supported Chinese frontend.",
            "Unreviewed runtime dataflow, remote/experimental UI and historical release notes remain.",
        ],
        **({"official_latest": latest} if latest else {}),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".claude-preview-", dir=output.parent) as directory:
        stage = Path(directory) / "preview"
        stage.mkdir()
        for group, pack in merged.items():
            for suffix, data in [("zh-CN", pack["translations"]), ("unreviewed-en", pack["missing"]),
                                 ("provenance", pack["provenance"])]:
                (stage / f"{group}-{suffix}.json").write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (stage / "ui-patch-preview.json").write_text(
            json.dumps(ui, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (stage / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if output.exists():
            raise ValueError("Preview output appeared concurrently; stopped without replacement")
        os.rename(stage, output)
    return report


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--app", type=Path, default=Path("/Applications/Claude.app"))
    cli.add_argument("--output", type=Path, required=True, help="New local review directory")
    cli.add_argument("--check-latest", action="store_true")
    args = cli.parse_args()
    report = prepare(args.app.expanduser().resolve(), args.output.expanduser(), args.check_latest)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2  # A preview is never an installation or a complete runtime acceptance.


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        print(f"Current preview stopped: {error.stderr or error.stdout or error}", file=sys.stderr)
        raise SystemExit(1)
    except (OSError, ValueError, KeyError) as error:
        print(f"Current preview stopped: {error}", file=sys.stderr)
        raise SystemExit(1)
