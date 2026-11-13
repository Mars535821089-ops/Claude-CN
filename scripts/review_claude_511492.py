#!/usr/bin/env python3
"""Read-only, version-bound 5.11492.2 localization review; never install."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
from typing import Callable

import claude5_candidate as previous
import update_guard
from upstream_patch_lib import (BASE_LANGUAGE_LIST, LANG_LIST_RE, read_asar_entry,
                                read_asar_header, read_frontend_entry)


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


def gate_candidates(old_rules: dict[str, dict], new_paths: list[str],
                    read_entry: Callable[[str], bytes | None]) -> dict[str, dict]:
    """Pin unique renamed JS candidates; no app or extracted file is patched."""
    output = {}
    replacement = BASE_LANGUAGE_LIST + ',"zh-CN","zh-Hans"]'
    for old_path, spec in sorted(old_rules.items()):
        prefix = old_path.rsplit("/", 1)[-1].rsplit("-", 1)[0] + "-"
        parent = old_path.rsplit("/", 1)[0] + "/"
        matches = [name for name in new_paths if name.startswith(parent)
                   and name.rsplit("/", 1)[-1].startswith(prefix) and name.endswith(".js")]
        if len(matches) != 1:
            raise ValueError("new locale gate path is absent or ambiguous")
        path = matches[0]
        raw = read_entry(path)
        if not isinstance(raw, bytes):
            raise ValueError("new locale gate bytes are unavailable")
        text = raw.decode("utf-8")
        match = LANG_LIST_RE.search(text)
        if match is None:
            raise ValueError("new locale gate context changed")
        declaration = re.search(r"([A-Za-z_$][\w$]*)=$", text[max(0, match.start()-32):match.start()])
        if declaration is None:
            raise ValueError("new locale gate context changed")
        variable = declaration.group(1)
        suffix = text[match.end():match.end()+200]
        if prefix == "shared-25-":
            contextual = bool(re.search(r";function\s+[\w$]+\([\w$]+\)\{return\s+"
                                        + re.escape(variable) + r"\.includes\([\w$]+\)\}", suffix))
        elif prefix == "shared-130-":
            contextual = (f"t={variable})" in suffix and "new Map" in suffix
                          and "for(let" in suffix)
        elif prefix == "c6fce776d-":
            contextual = '"signin-hand-over"' in suffix[:80]
        else:
            contextual = False
        if not contextual:
            raise ValueError("new locale gate context changed")
        patched, count = LANG_LIST_RE.subn(replacement, text)
        if count != spec.get("expected") or count != 1:
            raise ValueError("new locale gate context changed")
        output[path] = {"sha256": hashlib.sha256(raw).hexdigest(),
                        "patched_sha256": hashlib.sha256(patched.encode()).hexdigest(),
                        "expected": count, "locales": ["zh-CN", "zh-Hans"],
                        "status": "review_required_not_installable"}
    return output


def validate_desktop_draft(draft: dict, english: dict[str, str], identity: dict) -> dict:
    """Check exact source and ICU structure; never promote draft to installable."""
    if (not isinstance(draft, dict) or draft.get("version") != VERSION
            or draft.get("status") != "draft_not_installable"
            or draft.get("source_archives_sha256") != identity.get("archives")
            or identity.get("version") != VERSION):
        raise ValueError("desktop draft version or source identity differs")
    translations = draft.get("translations")
    source_digests = draft.get("source_english_sha256")
    if (not isinstance(translations, dict) or not translations
            or not isinstance(source_digests, dict)
            or set(translations) != set(source_digests)):
        raise ValueError("desktop draft ID set is incomplete")
    script = Path(__file__).parent / "icu_literals.cjs"
    env = os.environ.copy()
    modules = script.parent.parent / "node_modules"
    if modules.is_dir():
        env["NODE_PATH"] = str(modules)
    for key, translated in translations.items():
        source = english.get(key)
        if (not isinstance(source, str) or not isinstance(translated, str)
                or not translated.strip()
                or hashlib.sha256(source.encode()).hexdigest() != source_digests[key]):
            raise ValueError("desktop draft source text differs")
        args = lambda value: sorted(re.findall(r"\{([\w.$-]+)(?:\s*[,}])", value))
        tags = lambda value: sorted(re.findall(r"</?[A-Za-z][^>]*>", value))
        if args(source) != args(translated) or tags(source) != tags(translated):
            raise ValueError("desktop draft placeholders or markup changed")
        try:
            result = subprocess.run(["node", str(script)], input=json.dumps({
                "operation": "validate", "source": source, "translated": translated,
            }), capture_output=True, text=True, timeout=8, env=env)
            valid = result.returncode == 0 and json.loads(result.stdout).get("valid") is True
        except (OSError, ValueError, subprocess.TimeoutExpired):
            valid = False
        if not valid:
            raise ValueError("desktop draft ICU structure differs")
    return {"status": "review_required_not_installable", "draft_count": len(translations)}


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
    frontend_asar = app / "Contents/Resources/ion-dist.asar"
    frontend_raw = frontend_asar.read_bytes()
    frontend_size, _, frontend_header = read_asar_header(frontend_raw, frontend_asar)
    js_paths: list[str] = []

    def collect(entries: dict, prefix: str = "") -> None:
        for name, item in entries.items():
            path = f"{prefix}/{name}".lstrip("/")
            if isinstance(item, dict) and isinstance(item.get("files"), dict):
                collect(item["files"], path)
            elif path.endswith(".js"):
                js_paths.append(path)

    collect(frontend_header["files"])
    old_gate_path = pack / "language-whitelist-reviewed.json"
    if hashlib.sha256(old_gate_path.read_bytes()).hexdigest() != manifest.get(
            "generated_files_sha256", {}).get(old_gate_path.name):
        raise ValueError("previous locale gate review changed")
    old_gate_rules = previous.load(old_gate_path)["files"]
    if (set(old_gate_rules) != set(previous.GATES)
            or any(rule.get("sha256") != previous.GATES[path]
                   for path, rule in old_gate_rules.items())):
        raise ValueError("previous locale gate identities differ")
    summary["new_gate_candidates"] = gate_candidates(
        old_gate_rules, js_paths,
        lambda path: read_asar_entry(frontend_raw, frontend_size, frontend_header, path),
    )
    authored_path = previous.ROOT / "resources/candidates/claude-desktop-5.11492.2/desktop-authored-draft.json"
    draft = previous.load(authored_path)
    desktop_plan = plan_catalog(old_english["desktop"], current_english["desktop"],
                                old_chinese["desktop"], official_desktop, memory)
    if set(draft.get("translations", {})) != set(desktop_plan["pending_ids"]):
        raise ValueError("desktop draft does not cover the exact pending source IDs")
    summary["desktop_draft"] = validate_desktop_draft(draft, current_english["desktop"], identity)
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
