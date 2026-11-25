"""Exact-English localization migration analysis, without baseline acceptance.

Unlike the version-pinned installer, this module never treats an unchanged
message ID as evidence that its source text still means the same thing.
"""
from __future__ import annotations

from collections import defaultdict
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile

import preview_current as preview
import repair_macos_429 as repair
from install_current import verify_stage as verify_installed_localized_app
from update_guard import catalog_fingerprint, require_official_signature

# Current installed 4.4212.0 has a session-scoped Cowork auto-compaction hook
# added after the original localization receipt. The one-entry inverse ASAR
# reconstruction was independently SHA-256-matched to the pre-hook receipt.
# These exact bytes are allowlisted for reading translations ONLY. Porting the
# hook to the new app remains a separate, required installation gate.
OLD_BASE_ASAR_SHA256 = "97331448080f13f42ef0051bb289fe2898c30099ad029dec16d69b8758890819"
OLD_COWORK_PATCHED_ASAR_SHA256 = "3a109f18e7e5e6aecfe068296a05b8683468af73b11a9d783bc2bf758e0afbcb"
OLD_HOOK_MARKER = b"local-auto-compact-policy:v1"


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
    shapes = {}
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
        rules = pack.get("plural_shapes", {})
        if not isinstance(rules, dict):
            raise ValueError("Invalid plural-shape review")
        shapes[group] = {}
        for key, rule in rules.items():
            source = catalogs[group]["en"].get(key)
            current = results[group]["translations"].get(key)
            argument = rule.get("argument") if isinstance(rule, dict) else None
            if (key in translations or not isinstance(source, str)
                    or not isinstance(current, str) or not isinstance(argument, str)
                    or re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", argument) is None
                    or hashlib.sha256(source.encode()).hexdigest() != rule.get("english_sha256")
                    or hashlib.sha256(current.encode()).hexdigest() != rule.get("translation_sha256")
                    or f"{{{argument}, plural, one {{" not in source
                    or " other {" not in source or "plural," in current
                    or len(re.findall(r",\s*(?:plural|select|selectordinal)\s*,", source)) != 1
                    or re.search(r"(?:offset:\s*\d|=\d+\s*\{)", source)
                    or current.count("{" + argument + "}") != 1):
                raise ValueError(f"Plural-shape source or translation changed: {group}/{key}")
            shapes[group][key] = ("{" + argument + ", plural, one {" + current
                                  + "} other {" + current + "}}")
    # All groups are checked before changing any provisional result.
    for group, pack in review["catalogs"].items():
        for key, value in shapes[group].items():
            results[group]["translations"][key] = value
            results[group]["provenance"][key] = "reviewed_plural_shape"
        for key, value in pack["translations"].items():
            results[group]["translations"][key] = value
            results[group]["provenance"][key] = "reviewed_pinned_source"
            results[group]["missing"].pop(key, None)


def validate_draft_batch(draft: dict, english: dict[str, str], target_version: str,
                         group: str, expected_ids: set[str]) -> dict[str, str]:
    """Check one worker draft's identity/coverage; never promote it to reviewed.

    ICU and semantic review are separate required gates. The caller supplies
    assigned IDs from the immutable input, not from the draft's own claims.
    """
    if (not isinstance(draft, dict) or draft.get("version") != target_version
            or draft.get("group") != group or draft.get("status") != "draft_not_installable"
            or not isinstance(english, dict) or not expected_ids
            or not isinstance(expected_ids, set) or not expected_ids <= english.keys()):
        raise ValueError("Draft batch identity or assignment mismatch")
    hashes, translations = draft.get("source_english_sha256"), draft.get("translations")
    if (not isinstance(hashes, dict) or not isinstance(translations, dict)
            or hashes.keys() != expected_ids or translations.keys() != expected_ids):
        raise ValueError("Draft batch coverage differs from its assignment")
    for key in expected_ids:
        if (not isinstance(english[key], str)
                or hashes[key] != hashlib.sha256(english[key].encode()).hexdigest()
                or not isinstance(translations[key], str) or not translations[key].strip()):
            raise ValueError(f"Draft source changed or translation missing: {key}")
    return dict(translations)


def merge_draft_batches(english: dict[str, str], resolved: dict,
                        batches: list[tuple[dict, set[str]]], target_version: str,
                        group: str) -> dict:
    """Assemble complete assigned gaps without calling new drafts reviewed."""
    translated, missing, provenance = (resolved.get(key) for key in
                                       ("translations", "missing", "provenance"))
    if (not all(isinstance(value, dict) for value in (translated, missing, provenance))
            or translated.keys() & missing.keys()
            or translated.keys() | missing.keys() != english.keys()
            or provenance.keys() != translated.keys()
            or any(english[key] != value for key, value in missing.items())):
        raise ValueError("Resolved catalog does not partition the English source")
    collected = {}
    for draft, assigned in batches:
        if (not isinstance(assigned, set) or not assigned <= missing.keys()
                or assigned & collected.keys()):
            raise ValueError("Draft assignments overlap or overwrite resolved messages")
        collected.update(validate_draft_batch(draft, english, target_version, group, assigned))
    if collected.keys() != missing.keys():
        raise ValueError("Draft batches do not cover all unresolved messages")
    return {"translations": {**translated, **collected}, "missing": {},
            "provenance": {**provenance,
                           **{key: "validated_draft_not_reviewed" for key in collected}}}


def parse_review(raw: str | bytes) -> dict:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate key in reviewed additions: {key}")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique_object)


def load_review(path: Path) -> dict:
    return parse_review(path.read_bytes())


def verify_old_stage(app: Path) -> str:
    # The local build is ad-hoc signed: its own installation receipt, official
    # source fingerprint, all archive hashes and actual locales are the trust
    # boundary for recycling previously translated messages.
    try:
        verify_installed_localized_app(app)
        return "receipt_exact"
    except (ValueError, OSError, KeyError) as error:
        if str(error) != "Staged archive fingerprint changed":
            raise ValueError("tampered localized baseline or unrecognized installed build") from error
    try:
        profile = json.loads(preview.PROFILE.read_text())
        receipt = json.loads((app / "Contents/Resources/claude-cn-install.json").read_text())
        info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
        archives = preview.archive_hashes(app)
        old_asar = (app / repair.APP_ASAR_REL).read_bytes()
        expected_archives = receipt["archives"]
        if (info.get("CFBundleIdentifier") != "com.anthropic.claudefordesktop"
                or info.get("CFBundleShortVersionString") != profile["version"]
                or info.get("CFBundleVersion") != profile["build"]
                or receipt["version"] != profile["version"]
                or receipt["source_archives"] != profile["archives"]
                or expected_archives["Contents/Resources/app.asar"] != OLD_BASE_ASAR_SHA256
                or archives["Contents/Resources/app.asar"] != OLD_COWORK_PATCHED_ASAR_SHA256
                or archives["Contents/Resources/ion-dist.asar"] !=
                   expected_archives["Contents/Resources/ion-dist.asar"]
                or old_asar.count(OLD_HOOK_MARKER) != 1):
            raise ValueError("customized baseline hash, marker or identity changed")
        catalogs = preview.load_catalogs(app)
        for group in preview.PATHS:
            if catalog_fingerprint(catalogs[group]["en"]) != profile["catalogs"][group]["en"]:
                raise ValueError("reviewed English catalog changed: " + group)
            relative = ("resources/i18n/zh-CN.json" if group == "desktop" else
                        "i18n/" + ("dynamic/" if group == "dynamic" else "") + "zh-CN.json")
            locale = (repair.asar_json(app, relative) if group == "desktop" else
                      json.loads(repair.patch.read_frontend_entry(app, relative)))
            digest = hashlib.sha256(json.dumps(locale, ensure_ascii=False,
                                               sort_keys=True).encode()).hexdigest()
            if locale.keys() != catalogs[group]["en"].keys() or digest != receipt["catalog_sha256"][group]:
                raise ValueError("reviewed Chinese catalog changed: " + group)
        repair.patch.verify(app, "zh-CN")
    except (ValueError, OSError, KeyError, TypeError) as error:
        raise ValueError("tampered localized baseline or unrecognized installed build") from error
    return "verified_cowork_hook"


def audit(old_app: Path, new_app: Path, output: Path, target_version: str,
          review_path: Path | None = None) -> dict:
    """Produce a private review, never a ready-to-install locale pack."""
    if (output.exists() or output.is_symlink()
            or output.resolve().is_relative_to(repair.ROOT.resolve())
            or output.resolve().is_relative_to(new_app.resolve())
            or output.resolve().is_relative_to(old_app.resolve())):
        raise ValueError("Review must be a new directory outside the apps and repository")
    old_customization = verify_old_stage(old_app)
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
    if verify_old_stage(old_app) != old_customization:
        raise ValueError("Localized baseline changed during audit")
    require_official_signature(new_app)
    report = {"source_version": old_info["CFBundleShortVersionString"],
              "target_version": target_version, "candidate_signature": True,
              "old_customization": old_customization,
              "cowork_hook_ported_to_candidate": False,
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
