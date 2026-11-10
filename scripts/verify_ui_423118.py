#!/usr/bin/env python3
"""Read-only AST and fingerprint verification of reviewed 4.23118 UI strings.

This checks an independently signed official bundle and prepares replacements
in memory. It does not install, translate other unreviewed strings, or change
the currently running Claude Desktop.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import plistlib
import re
import subprocess

from upstream_patch_lib import read_asar_entry, read_asar_header
from update_guard import require_official_signature
from patch_whitelist_423118 import load_reviewed as reviewed_locale_gates, verify_source

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "resources/candidates/claude-desktop-4.23118.0"
MANIFEST = CANDIDATE / "hardcoded-ui-reviewed.json"
VERSION = "4.23118.0"


def validate_manifest(manifest: dict, *, version: str = VERSION) -> dict[str, dict]:
    if not isinstance(manifest, dict) or manifest.get("version") != version:
        raise ValueError("reviewed UI manifest version differs")
    archives = manifest.get("source_archives")
    files = manifest.get("files")
    if (not isinstance(archives, dict) or set(archives) != {"app.asar", "ion-dist.asar"}
            or any(not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
                   for value in archives.values())
            or not isinstance(files, dict) or not files):
        raise ValueError("reviewed UI manifest is incomplete")
    variants = manifest.get("source_variants", [])
    if not isinstance(variants, list) or len(variants) > 2:
        raise ValueError("reviewed official source variants are invalid")
    seen = {tuple(sorted(archives.items()))}
    for variant in variants:
        if (not isinstance(variant, dict) or set(variant) != set(archives)
                or any(not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
                       for value in variant.values())
                or tuple(sorted(variant.items())) in seen):
            raise ValueError("reviewed official source variant is invalid or duplicate")
        seen.add(tuple(sorted(variant.items())))
    for name, spec in files.items():
        if not isinstance(name, str) or "\\" in name or any(
                segment in {".", "..", ""} for segment in name.split("/")):
            raise ValueError("unsafe reviewed UI path")
        path = PurePosixPath(name)
        if (len(path.parts) < 3 or path.parts[0] not in archives
                or path.suffix != ".js"):
            raise ValueError("unsafe reviewed UI path")
        if (not isinstance(spec, dict) or set(spec) != {"sha256", "patched_sha256", "rules"}
                or any(not isinstance(spec[key], str)
                       or re.fullmatch(r"[0-9a-f]{64}", spec[key]) is None
                       for key in ("sha256", "patched_sha256"))):
            raise ValueError("reviewed UI source fingerprint is invalid")
        rules = spec["rules"]
        if (not isinstance(rules, list) or not rules or any(
                not isinstance(rule, dict) or set(rule) != {"context", "source", "expected"}
                or rule["context"] not in {"title", "label", "children", "buttons"}
                or not isinstance(rule["source"], str) or not rule["source"]
                or type(rule["expected"]) is not int or rule["expected"] < 1
                for rule in rules)):
            raise ValueError("reviewed UI rules are invalid")
    return files


def verify_locale_gates(sources: dict[str, bytes], expected: dict[str, dict]) -> int:
    """Require both reviewed locale arrays in an unmodified official bundle."""
    if not expected or set(sources) != set(expected):
        raise ValueError("reviewed locale gates are missing")
    for name, spec in expected.items():
        if verify_source(sources[name], spec) is None:
            raise ValueError("official locale gate is already patched")
    return len(expected)


def verify(app: Path, *, version: str = VERSION) -> dict[str, object]:
    if version not in {"4.23118.0", "4.23118.1"}:
        raise ValueError("unsupported reviewed UI version")
    candidate = CANDIDATE if version == VERSION else (
        ROOT / f"resources/candidates/claude-desktop-{version}"
    )
    manifest_path = MANIFEST if version == VERSION else candidate / "hardcoded-ui-reviewed.json"
    manifest = json.loads(manifest_path.read_text())
    files = validate_manifest(manifest, version=version)
    labels = json.loads((ROOT / "resources/ui-reviewed-zh-CN.json").read_text())
    additions = json.loads((candidate / "hardcoded-ui-zh-CN.json").read_text())
    if (not isinstance(labels, dict) or not isinstance(additions, dict)
            or set(labels) & set(additions)):
        raise ValueError("reviewed UI translation dictionary conflicts")
    translations = {**labels, **additions}
    if any(rule["source"] not in translations for spec in files.values()
           for rule in spec["rules"]):
        raise ValueError("reviewed UI rule has no pinned translation")
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    if (info.get("CFBundleIdentifier") != "com.anthropic.claudefordesktop"
            or info.get("CFBundleShortVersionString") != version):
        raise ValueError("official Claude build identity changed")
    require_official_signature(app)

    try:
        observed_archives = {
            archive: hashlib.sha256((app / "Contents/Resources" / archive).read_bytes()).hexdigest()
            for archive in ("app.asar", "ion-dist.asar")
        }
    except OSError as error:
        raise ValueError("reviewed official ASAR fingerprint changed") from error
    if observed_archives not in [manifest["source_archives"], *manifest.get("source_variants", [])]:
        raise ValueError("reviewed official ASAR fingerprint changed")

    originals = []
    locale_gates = 0
    for archive in ("app.asar", "ion-dist.asar"):
        path = app / "Contents/Resources" / archive
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != observed_archives[archive]:
            raise ValueError("reviewed official ASAR fingerprint changed")
        size, _, header = read_asar_header(raw, path)
        if archive == "ion-dist.asar":
            gate_specs = reviewed_locale_gates(version)
            gate_sources: dict[str, bytes] = {}
            for name in gate_specs:
                content = read_asar_entry(raw, size, header, name)
                if content is None:
                    raise ValueError("reviewed locale gate source is missing")
                gate_sources[name] = content
            locale_gates = verify_locale_gates(gate_sources, gate_specs)
        for name, spec in files.items():
            if not name.startswith(archive + "/"):
                continue
            source = read_asar_entry(raw, size, header, name[len(archive) + 1:])
            if source is None or hashlib.sha256(source).hexdigest() != spec["sha256"]:
                raise ValueError("reviewed official UI entry fingerprint changed")
            originals.append({"file": name, "source": source.decode("utf-8"),
                              "spec": spec, "translations": translations})

    runner = subprocess.run(
        ["node", str(ROOT / "scripts/ui_localization.cjs"), "--patch-inline"],
        input=json.dumps(originals, ensure_ascii=False), text=True,
        capture_output=True, check=False, timeout=90,
    )
    if runner.returncode:
        raise ValueError("reviewed official UI failed AST or replacement verification")
    patched = json.loads(runner.stdout)
    if not isinstance(patched, list) or len(patched) != len(files):
        raise ValueError("reviewed UI patch output count differs")
    for item, output in zip(originals, patched, strict=True):
        if (not isinstance(output, str) or
                hashlib.sha256(output.encode()).hexdigest() != item["spec"]["patched_sha256"]):
            raise ValueError("reviewed UI patch output fingerprint changed")
    return {"version": version, "source_signed": True, "files": len(files),
            "source_archives": observed_archives,
            "locale_gates": locale_gates,
            "reviewed_replacements": sum(rule["expected"] for spec in files.values()
                                         for rule in spec["rules"]),
            "installed": False, "visual_ui": "not_checked"}


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--official-app", type=Path, required=True)
    cli.add_argument("--version", choices=("4.23118.0", "4.23118.1"), default=VERSION)
    args = cli.parse_args()
    try:
        print(json.dumps(verify(args.official_app, version=args.version), sort_keys=True))
    except Exception as error:
        # Never print vendor chunk contents, local user state or signing details.
        print(json.dumps({"status": "unverified", "reason_type": type(error).__name__}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
