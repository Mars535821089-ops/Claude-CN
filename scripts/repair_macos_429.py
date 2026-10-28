#!/usr/bin/env python3
"""Version-checked Claude 4.29.0 macOS desktop UI localization.

This patches the desktop application, not Claude Code CLI. It never modifies
the updater, provider credentials, conversations, or user profiles except the
locale field in existing Claude configuration files.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import upstream_patch_lib as patch

ROOT = Path(__file__).resolve().parent.parent
RESOURCES = ROOT / "resources"
VERSION = "4.29.0"
FRONTEND_REL = Path("Contents/Resources/ion-dist/assets/v1")
APP_ASAR_REL = Path("Contents/Resources/app.asar")
NAV_RE = re.compile(
    r'(?P<prefix>(?:key|mode):"(?P<kind>chat|task|cowork|code)"'
    r'(?:,icon:"[^"]+")?,label:)"(?P<label>Chat|Cowork|Code)"'
)
NAV_LABELS = {"chat": ("Chat", "聊天"), "task": ("Cowork", "协作"),
              "cowork": ("Cowork", "协作"), "code": ("Code", "编程")}


def json_file(path: Path) -> dict[str, str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise ValueError(f"Expected a string catalog: {path}")
    return value


def asar_json(app: Path, relative: str) -> dict[str, str]:
    path = app / APP_ASAR_REL
    data = path.read_bytes()
    header_size, _, header = patch.read_asar_header(data, path)
    raw = patch.read_asar_entry(data, header_size, header, relative)
    if raw is None:
        raise ValueError(f"Missing desktop resource in app.asar: {relative}")
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError(f"Invalid desktop resource: {relative}")
    return obj


def validate_locales(app: Path) -> tuple[int, int]:
    desktop_en = asar_json(app, "resources/i18n/en-US.json")
    desktop_zh = asar_json(app, "resources/i18n/zh-CN.json")
    if desktop_en.keys() != desktop_zh.keys():
        raise ValueError("Desktop locale has missing or unexpected message IDs")
    frontend_en_raw = patch.read_frontend_entry(app, "i18n/en-US.json")
    frontend_zh_raw = patch.read_frontend_entry(app, "i18n/zh-CN.json")
    if not frontend_en_raw or not frontend_zh_raw:
        raise ValueError("Missing frontend locale")
    frontend_en = json.loads(frontend_en_raw)
    frontend_zh = json.loads(frontend_zh_raw)
    if frontend_en.keys() != frontend_zh.keys():
        raise ValueError("Frontend locale has missing or unexpected message IDs")
    return len(desktop_zh), len(frontend_zh)


def patch_navigation(app: Path) -> int:
    root = app / FRONTEND_REL
    count = 0
    for path in root.glob("*.js"):
        body = path.read_text(encoding="utf-8")

        def replace(match: re.Match[str]) -> str:
            nonlocal count
            original, translated = NAV_LABELS[match["kind"]]
            if match["label"] != original:
                return match.group(0)
            count += 1
            return match["prefix"] + '"' + translated + '"'

        modified = NAV_RE.sub(replace, body)
        if modified != body:
            path.write_text(modified, encoding="utf-8")
    if count < 6:
        raise ValueError(f"Claude navigation structure changed; matched only {count} labels")
    print(f"Translated {count} desktop navigation labels", flush=True)
    return count


def patch_desktop_asar(app: Path) -> None:
    asar = app / APP_ASAR_REL
    source = RESOURCES / "desktop-zh-CN.json"
    en = asar_json(app, "resources/i18n/en-US.json")
    zh = json_file(source)
    if en.keys() != zh.keys():
        raise ValueError("Desktop translation pack does not match this Claude version")
    directory = app / "Contents/Resources/app-asar-zh-work"
    unpacked: set[str] = set()
    temporary = asar.with_name("app.asar.zh-new")
    try:
        patch.extract_asar(asar, directory, unpacked)
        target = directory / "resources/i18n/zh-CN.json"
        target.write_text(json.dumps(zh, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        patch.build_asar(directory, temporary, unpacked)
        os.replace(temporary, asar)
        patch.update_electron_asar_integrity(app, "Resources/app.asar", asar)
    finally:
        if directory.exists():
            shutil.rmtree(directory)
        temporary.unlink(missing_ok=True)
        extra_unpacked = Path(str(temporary) + ".unpacked")
        if extra_unpacked.exists():
            shutil.rmtree(extra_unpacked)


def build_stage(original: Path, staged: Path) -> None:
    patch.copy_app(original, staged)
    frontend = patch.prepare_frontend_target(staged)
    try:
        patch.patch_language_whitelist(staged, "zh-CN")
        patch.patch_hardcoded_frontend_strings(staged, "zh-CN")
        patch.patch_slash_command_menu(staged, "zh-CN")
        patch.patch_language_display_names(staged)
        patch_navigation(staged)
        _, fallback, _ = patch.merge_frontend_locale(staged, "zh-CN")
        if fallback:
            raise ValueError(f"Frontend catalog has {fallback} untranslated message IDs")
        patch.install_statsig_locale(staged, "zh-CN")
        patch.install_dynamic_locale(staged, "zh-CN")
        patch.install_desktop_locale(staged, "zh-CN")
        patch_desktop_asar(staged)
        patch.finalize_frontend_target(staged, frontend)
    finally:
        patch.cleanup_frontend_target(frontend)
    patch.resign_app(staged)
    patch.clear_quarantine(staged)
    verify_stage(staged)


def verify_stage(app: Path) -> None:
    if patch.bundle_version(app) != VERSION:
        raise ValueError(f"Expected Claude {VERSION}, got {patch.bundle_version(app)}")
    desktop_count, frontend_count = validate_locales(app)
    patch.verify(app, "zh-CN")
    print(f"Verified desktop {desktop_count} and frontend {frontend_count} message IDs", flush=True)


def locale_config_paths(home: Path) -> list[Path]:
    base = home / "Library/Application Support"
    return [base / "Claude/config.json", base / "Claude-3p/config.json"]


def update_locale(home: Path, locale: str) -> dict[Path, bytes]:
    originals: dict[Path, bytes] = {}
    modified: list[Path] = []
    prepared: dict[Path, dict] = {}
    for path in locale_config_paths(home):
        if not path.is_file():
            continue
        original = path.read_bytes()
        data = json.loads(original)
        if not isinstance(data, dict):
            raise ValueError(f"Unexpected Claude config format: {path}")
        originals[path] = original
        if data.get("locale") == locale:
            continue
        data["locale"] = locale
        prepared[path] = data
    try:
        for path, data in prepared.items():
            fd, name = tempfile.mkstemp(prefix=".claude-locale-", dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as out:
                    json.dump(data, out, ensure_ascii=False, indent=2)
                    out.write("\n")
                os.chmod(name, path.stat().st_mode & 0o777)
                os.replace(name, path)
                modified.append(path)
            finally:
                Path(name).unlink(missing_ok=True)
    except Exception:
        for path in modified:
            path.write_bytes(originals[path])
        raise
    return originals


def restore_locale(originals: dict[Path, bytes]) -> None:
    for path, data in originals.items():
        path.write_bytes(data)


def quit_and_wait() -> None:
    subprocess.run(["osascript", "-e", 'tell application "Claude" to quit'], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(25):
        active = subprocess.run(["pgrep", "-f", r"/Applications/Claude.app/Contents/MacOS/Claude$"],
                                capture_output=True, text=True)
        if active.returncode != 0:
            return
        time.sleep(1)
    raise RuntimeError("Claude did not quit gracefully; installation stopped without forcing it closed")


def install(original: Path, staged: Path, home: Path, launch: bool) -> Path:
    verify_stage(staged)
    if patch.bundle_version(original) != VERSION:
        raise RuntimeError("Claude upgraded while preparing the patch; installation stopped")
    if not patch.is_officially_signed(original):
        raise RuntimeError("Current app is not officially signed; refusing to overwrite it")
    if original.resolve() == staged.resolve():
        raise ValueError("Stage and installed app must be different")
    backup = original.with_name(
        f"Claude.backup-before-zh-CN-{VERSION}-{dt.datetime.now():%Y%m%d-%H%M%S}.app")
    if backup.exists():
        raise FileExistsError(backup)
    quit_and_wait()
    if patch.bundle_version(original) != VERSION or not patch.is_officially_signed(original):
        raise RuntimeError("Claude changed before replacement; no application files were moved")
    originals: dict[Path, bytes] = {}
    moved_original = False
    installed_new = False
    try:
        os.replace(original, backup)
        moved_original = True
        os.replace(staged, original)
        installed_new = True
        originals = update_locale(home, "zh-CN")
        verify_stage(original)
    except Exception:
        if originals:
            restore_locale(originals)
        if installed_new and original.exists():
            failed = original.with_name(f"Claude.failed-zh-CN-{dt.datetime.now():%Y%m%d-%H%M%S}.app")
            os.replace(original, failed)
        if moved_original and backup.exists():
            os.replace(backup, original)
        raise
    if launch:
        subprocess.run(["open", "-a", str(original)], check=True)
    print(f"Installed Claude {VERSION} Simplified Chinese. Official backup: {backup}", flush=True)
    print("Auto-update preferences were preserved; reapply after a future version upgrade.", flush=True)
    return backup


def check(original: Path, home: Path) -> None:
    result = {"installed_version": patch.bundle_version(original) if original.exists() else None,
              "supported_version": VERSION,
              "official_signature": patch.is_officially_signed(original) if original.exists() else None,
              "profiles": {}}
    for path in locale_config_paths(home):
        if path.is_file():
            result["profiles"][path.parent.name] = json.loads(path.read_text()).get("locale")
    try:
        result["catalog_counts"] = dict(zip(("desktop", "frontend"), validate_locales(original)))
    except Exception:
        result["catalog_counts"] = None
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    cli = argparse.ArgumentParser(description="Claude 4.29.0 macOS desktop UI Chinese repair")
    cli.add_argument("--app", type=Path, default=Path("/Applications/Claude.app"))
    cli.add_argument("--user-home", type=Path, default=Path.home())
    action = cli.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true")
    action.add_argument("--install-staged", type=Path)
    cli.add_argument("--no-launch", action="store_true")
    args = cli.parse_args()
    if not args.apply and not args.install_staged:
        check(args.app, args.user_home)
        return
    if patch.bundle_version(args.app) != VERSION:
        raise SystemExit(f"Installed Claude version changed. This patch supports {VERSION} only; update source translations first.")
    if args.install_staged:
        install(args.app, args.install_staged, args.user_home, not args.no_launch)
        return
    if not patch.is_officially_signed(args.app):
        raise SystemExit("The installed app is already modified; refusing to patch it again")
    with tempfile.TemporaryDirectory(prefix="claude-cn-429-", dir=args.app.parent) as directory:
        stage = Path(directory) / "Claude.app"
        build_stage(args.app, stage)
        install(args.app, stage, args.user_home, not args.no_launch)


if __name__ == "__main__":
    main()
