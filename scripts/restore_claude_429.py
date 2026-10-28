#!/usr/bin/env python3
"""Restore an explicitly selected, same-version official Claude backup."""
from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
from pathlib import Path

import repair_macos_429 as repair


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--backup", type=Path, required=True)
    cli.add_argument("--app", type=Path, default=Path("/Applications/Claude.app"))
    cli.add_argument("--apply", action="store_true")
    args = cli.parse_args()
    installed = repair.patch.bundle_version(args.app)
    backup_version = repair.patch.bundle_version(args.backup)
    if installed != repair.VERSION or backup_version != installed:
        raise SystemExit(f"Version mismatch: installed={installed}, backup={backup_version}; no change made")
    info = repair.patch.codesign_info(args.backup)
    if not repair.patch.is_officially_signed(args.backup) or "TeamIdentifier=Q6L2SF6YDW" not in info["raw"]:
        raise SystemExit("Backup is not signed by Anthropic; no change made")
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(args.backup)], check=True)
    parked = args.app.with_name(f"Claude.retained-Chinese-{dt.datetime.now():%Y%m%d-%H%M%S}.app")
    if parked.exists() or args.backup.resolve() == args.app.resolve():
        raise SystemExit("Invalid or occupied restore target")
    if not args.apply:
        print(f"Check passed: Claude {installed}. Add --apply to restore; the Chinese app will be retained.")
        return
    repair.quit_and_wait()
    os.replace(args.app, parked)
    try:
        os.replace(args.backup, args.app)
    except Exception:
        os.replace(parked, args.app)
        raise
    try:
        repair.update_locale(Path.home(), "en-US")
    except Exception:
        os.replace(args.app, args.backup)
        os.replace(parked, args.app)
        raise
    subprocess.run(["open", "-a", str(args.app)], check=True)
    print(f"Restored official Claude {installed}; Chinese application retained at {parked}")


if __name__ == "__main__":
    main()
