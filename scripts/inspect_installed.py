#!/usr/bin/env python3
"""Read-only Claude Desktop localization inventory; never attest a visible UI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import plistlib
import re
import subprocess

from upstream_patch_lib import read_asar_entry, read_asar_header


EXPECTED_BUNDLE_ID = "com.anthropic.claudefordesktop"
RESOURCE_PATHS = {
    "desktop": ("app.asar", "resources/i18n/zh-CN.json"),
    "frontend": ("ion-dist.asar", "i18n/zh-CN.json"),
    "dynamic": ("ion-dist.asar", "i18n/dynamic/zh-CN.json"),
}


def verify_signature(app: Path) -> bool:
    try:
        result = subprocess.run(
            ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
            capture_output=True, check=False, timeout=30,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def inspect_app(app: Path) -> dict[str, object]:
    base: dict[str, object] = {
        "status": "invalid_app", "visual_ui_verified": False,
        "latest": "not_checked", "resource_catalogs": {},
    }
    if app.is_symlink() or not app.is_dir():
        return {**base, "status": "unavailable"}
    try:
        info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
        version = info.get("CFBundleShortVersionString")
        if (info.get("CFBundleIdentifier") != EXPECTED_BUNDLE_ID
                or not isinstance(version, str)
                or re.fullmatch(r"\d+\.\d+\.\d+", version) is None
                or not verify_signature(app)):
            return base
        base["version"] = version
        contents: dict[str, bytes] = {}
        for archive in {part[0] for part in RESOURCE_PATHS.values()}:
            path = app / "Contents/Resources" / archive
            if path.is_symlink() or not path.is_file():
                return base
            contents[archive] = path.read_bytes()
        catalogs: dict[str, bool] = {}
        for name, (archive, entry) in RESOURCE_PATHS.items():
            path = app / "Contents/Resources" / archive
            raw = contents[archive]
            size, _, header = read_asar_header(raw, path)
            payload = read_asar_entry(raw, size, header, entry)
            if payload is None:
                catalogs[name] = False
                continue
            parsed = json.loads(payload)
            catalogs[name] = (
                isinstance(parsed, dict) and bool(parsed)
                and any(isinstance(value, str) and any("\u4e00" <= char <= "\u9fff" for char in value)
                        for value in parsed.values())
            )
        base["resource_catalogs"] = catalogs
        base["status"] = ("resources_present_ui_unverified" if all(catalogs.values())
                          else "unlocalized" if not any(catalogs.values()) else "incomplete")
        return base
    except (OSError, ValueError, TypeError, KeyError, SystemExit):
        return base


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=Path("/Applications/Claude.app"))
    args = parser.parse_args()
    print(json.dumps(inspect_app(args.app), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
