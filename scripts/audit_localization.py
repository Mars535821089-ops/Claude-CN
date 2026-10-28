#!/usr/bin/env python3
"""Verify installed translation IDs/ICU and parse all modified frontend JS."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import repair_macos_429 as repair


def frontend_entries(app: Path):
    asar = app / repair.patch.FRONTEND_ASAR_REL
    data = asar.read_bytes()
    size, _, header = repair.patch.read_asar_header(data, asar)
    return data, size, header


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--app", type=Path, default=Path("/Applications/Claude.app"))
    cli.add_argument("--official-backup", type=Path)
    args = cli.parse_args()
    counts = repair.validate_locales(args.app)
    desktop_en = repair.asar_json(args.app, "resources/i18n/en-US.json")
    desktop_zh = repair.asar_json(args.app, "resources/i18n/zh-CN.json")
    data, size, header = frontend_entries(args.app)
    read = lambda relative: repair.patch.read_asar_entry(data, size, header, relative)
    en = json.loads(read("i18n/en-US.json"))
    zh = json.loads(read("i18n/zh-CN.json"))
    if zh != repair.json_file(repair.RESOURCES / "frontend-zh-CN.json"):
        raise SystemExit("Published frontend pack differs from the installed catalog")
    if desktop_zh != repair.json_file(repair.RESOURCES / "desktop-zh-CN.json"):
        raise SystemExit("Published desktop pack differs from the installed catalog")
    scripts = {}
    if args.official_backup:
        if repair.patch.bundle_version(args.official_backup) != repair.VERSION:
            raise SystemExit("Official backup version does not match")
        original, original_size, original_header = frontend_entries(args.official_backup)
        for name, _ in repair.patch.walk_asar_entries(header):
            if name.endswith(".js"):
                source = read(name)
                before = repair.patch.read_asar_entry(original, original_size, original_header, name)
                if before != source:
                    scripts[name] = source.decode("utf-8")
    payload = {"catalogs": {"frontend": {"en": en, "zh": zh},
                             "desktop": {"en": desktop_en, "zh": desktop_zh}},
               "scripts": scripts}
    result = subprocess.run(["node", str(repair.ROOT / "scripts/audit_messages.cjs")],
                            input=json.dumps(payload), text=True, capture_output=True)
    print(result.stdout, end="")
    if result.returncode:
        raise SystemExit(result.stderr or "Locale audit failed")
    repair.patch.verify_electron_asar_integrity(args.app)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(args.app)], check=True)
    print(f"Verified exact message ID coverage: desktop={counts[0]}, frontend={counts[1]}")


if __name__ == "__main__":
    main()
