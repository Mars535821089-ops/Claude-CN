"""Exact-English localization migration analysis, without baseline acceptance.

Unlike the version-pinned installer, this module never treats an unchanged
message ID as evidence that its source text still means the same thing.
"""
from __future__ import annotations

from collections import defaultdict
import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile

import preview_current as preview
import repair_macos_429 as repair
from update_guard import catalog_fingerprint, require_official_signature


def merge_exact(
    old_english: dict[str, str],
    new_english: dict[str, str],
    old_chinese: dict[str, str],
    *,
    official: dict[str, str] | None = None,
    reviewed: dict[str, str] | None = None,
    preserved: set[str] | None = None,
) -> dict:
    """Reuse only independently matched source text; retain explicit gaps."""
    for name, catalog in (("old English", old_english), ("new English", new_english),
                          ("baseline Chinese", old_chinese)):
        if not isinstance(catalog, dict) or not all(
            isinstance(key, str) and isinstance(value, str) and value
            for key, value in catalog.items()
        ):
            raise ValueError(f"Invalid {name} catalog")
    if old_chinese.keys() != old_english.keys():
        raise ValueError("Baseline English and Chinese message IDs differ")
    official = {} if official is None else official
    reviewed = {} if reviewed is None else reviewed
    preserved = set() if preserved is None else preserved
    by_text: dict[str, set[str]] = defaultdict(set)
    for key, text in old_english.items():
        by_text[text].add(old_chinese[key])
    translations, missing, provenance = {}, {}, {}
    for key, text in new_english.items():
        if key in official:
            target, origin = official[key], "new_official_chinese"
        elif text in reviewed:
            target, origin = reviewed[text], "reviewed_exact_text"
        elif text in preserved:
            target, origin = text, "reviewed_preserved"
        elif key in old_chinese and old_english.get(key) == text:
            target, origin = old_chinese[key], "same_id_same_english"
        elif len(by_text.get(text, set())) == 1:
            target, origin = next(iter(by_text[text])), "unambiguous_same_english"
        else:
            missing[key] = text
            continue
        if not isinstance(target, str) or not target:
            raise ValueError(f"Invalid translation for message {key}")
        translations[key], provenance[key] = target, origin
    return {"translations": translations, "missing": missing, "provenance": provenance}


def apply_review(catalogs: dict, results: dict, review: dict, target_version: str) -> None:
    """Apply authored additions only to their exact reviewed English input."""
    if review.get("version") != target_version or not isinstance(review.get("catalogs"), dict):
        raise ValueError("Reviewed additions target a different version or have invalid catalogs")
    for group, pack in review["catalogs"].items():
        if (group not in catalogs or not isinstance(pack, dict)
                or pack.get("english_sha256") != catalog_fingerprint(catalogs[group]["en"])):
            raise ValueError(f"Reviewed English source fingerprint changed: {group}")
        translations = pack.get("translations")
        if not isinstance(translations, dict) or not all(
            key in catalogs[group]["en"] and isinstance(value, str) and value
            for key, value in translations.items()
        ):
            raise ValueError(f"Invalid reviewed message IDs or translations: {group}")
    # All groups are checked before changing any provisional result.
    for group, pack in review["catalogs"].items():
        for key, value in pack["translations"].items():
            results[group]["translations"][key] = value
            results[group]["provenance"][key] = "reviewed_pinned_source"
            results[group]["missing"].pop(key, None)


def load_review(path: Path) -> dict:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate key in reviewed additions: {key}")
            result[key] = value
        return result

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)


def audit(old_app: Path, new_app: Path, output: Path, target_version: str,
          review_path: Path | None = None) -> dict:
    """Produce a private review, never a ready-to-install locale pack."""
    if (output.exists() or output.is_symlink()
            or output.resolve().is_relative_to(repair.ROOT.resolve())
            or output.resolve().is_relative_to(new_app.resolve())
            or output.resolve().is_relative_to(old_app.resolve())):
        raise ValueError("Review must be a new directory outside the apps and repository")
    # The current installed localized app is not Anthropic signed. Trust its
    # English source only when it matches our reviewed, pinned previous input.
    profile = json.loads(preview.PROFILE.read_text())
    old_info = plistlib.loads((old_app / "Contents/Info.plist").read_bytes())
    if old_info.get("CFBundleShortVersionString") != profile["version"]:
        raise ValueError("Installed localized source version changed")
    require_official_signature(new_app)
    info = plistlib.loads((new_app / "Contents/Info.plist").read_bytes())
    if (info.get("CFBundleShortVersionString") != target_version
            or info.get("CFBundleIdentifier") != "com.anthropic.claudefordesktop"):
        raise ValueError("Candidate is not the requested signed Claude version")
    old = preview.load_catalogs(old_app)
    new = preview.load_catalogs(new_app)
    reviewed, preserved = preview.load_reviewed()
    merged: dict[str, dict] = {}
    metadata = {}
    for group in preview.PATHS:
        if catalog_fingerprint(old[group]["en"]) != profile["catalogs"][group]["en"]:
            raise ValueError(f"Old English source differs from reviewed baseline: {group}")
        relative = ("resources/i18n/zh-CN.json" if group == "desktop" else
                    "i18n/" + ("dynamic/" if group == "dynamic" else "") + "zh-CN.json")
        previous = (repair.asar_json(old_app, relative) if group == "desktop" else
                    json.loads(repair.patch.read_frontend_entry(old_app, relative)))
        result = merge_exact(old[group]["en"], new[group]["en"], previous,
                             official=new[group]["official"], reviewed=reviewed,
                             preserved=preserved)
        merged[group] = result
    if review_path is not None:
        apply_review(new, merged, load_review(review_path), target_version)
    for group, result in merged.items():
        metadata[group] = {
            "new_ids": len(new[group]["en"]), "reused_or_official": len(result["translations"]),
            "missing": len(result["missing"]),
            "new_english_sha256": catalog_fingerprint(new[group]["en"]),
        }
    # Fail if exact matching creates invalid ICU arguments, tags or selection
    # branches. This is only a provisional audit; gaps and UI text still need
    # translation and layout review before installer can accept a new baseline.
    payload = {"catalogs": {group: {
        "en": {key: new[group]["en"][key] for key in pack["translations"]},
        "zh": pack["translations"], "strictSelections": True,
    } for group, pack in merged.items()}}
    checked = subprocess.run(["node", str(repair.ROOT / "scripts/audit_messages.cjs")],
                             input=json.dumps(payload, ensure_ascii=False), text=True,
                             capture_output=True, check=False, timeout=180)
    validation = json.loads(checked.stdout)
    report = {"source_version": old_info["CFBundleShortVersionString"],
              "target_version": target_version, "candidate_signature": True,
              "groups": metadata, "icu_errors": validation["errors"],
              "validation_exit": checked.returncode,
              "status": "review_only_not_installable", "app_modified": False,
              "baseline_accepted": False}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".claude-migration-", dir=output.parent) as temporary:
        stage = Path(temporary) / "review"
        stage.mkdir()
        for group, pack in merged.items():
            for name, key in (("provisional-zh", "translations"),
                              ("unreviewed-en", "missing"), ("provenance", "provenance")):
                (stage / f"{group}-{name}.json").write_text(
                    json.dumps(pack[key], ensure_ascii=False, indent=2) + "\n")
        (stage / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        if output.exists():
            raise ValueError("Review appeared concurrently; preserving both")
        os.rename(stage, output)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-app", type=Path, required=True)
    parser.add_argument("--new-app", type=Path, required=True)
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reviewed-additions", type=Path,
                        help="Authored translations bound to exact source catalog fingerprints")
    args = parser.parse_args()
    result = audit(args.old_app, args.new_app, args.output, args.target_version,
                   args.reviewed_additions)
    print(json.dumps(result, ensure_ascii=False))
    if result["validation_exit"] != 0:
        raise SystemExit(1)
