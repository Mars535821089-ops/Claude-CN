#!/usr/bin/env python3
"""Read-only Claude Desktop upstream localization change detector."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import repair_macos_429 as repair
from official_release import compare_latest, fetch_latest

FORMAT_VERSION = 1
MONITOR_LABEL = "com.claude-cn.update-guard"
DEFAULT_STATE_DIR = Path.home() / "Library/Application Support/Claude-CN"
CATALOGS = {
    "desktop": ("resources/i18n/en-US.json", repair.RESOURCES / "desktop-zh-CN.json"),
    "frontend": ("i18n/en-US.json", repair.RESOURCES / "frontend-zh-CN.json"),
}


def decode_catalog(raw: bytes, label: str) -> dict[str, str]:
    try:
        catalog = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read {label} English catalog: {error}") from error
    if not isinstance(catalog, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in catalog.items()
    ):
        raise ValueError(f"Unsupported {label} English catalog structure")
    return catalog


def read_source_catalogs(app: Path) -> dict[str, dict[str, str]]:
    desktop_asar = app / repair.APP_ASAR_REL
    data = desktop_asar.read_bytes()
    header_size, _, header = repair.patch.read_asar_header(data, desktop_asar)
    desktop_raw = repair.patch.read_asar_entry(
        data, header_size, header, CATALOGS["desktop"][0]
    )
    frontend_raw = repair.patch.read_frontend_entry(app, CATALOGS["frontend"][0])
    if desktop_raw is None or frontend_raw is None:
        raise ValueError("Claude's expected English localization resources are missing")
    return {
        "desktop": decode_catalog(desktop_raw, "desktop"),
        "frontend": decode_catalog(frontend_raw, "frontend"),
    }


def catalog_fingerprint(catalog: dict[str, str]) -> str:
    canonical = json.dumps(
        catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def require_official_signature(app: Path) -> None:
    try:
        result = subprocess.run(["codesign", "-dv", "--verbose=4", str(app)],
                                capture_output=True, text=True, check=True)
        if not {"TeamIdentifier=Q6L2SF6YDW",
                "Authority=Developer ID Application: Anthropic PBC (Q6L2SF6YDW)",
                "Identifier=com.anthropic.claudefordesktop"} <= set(result.stderr.splitlines()):
            raise ValueError("Claude does not have the official Anthropic Developer ID signature")
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)],
                       capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as error:
        raise ValueError(f"Claude official signature verification failed: {error.stderr}") from error


def bundle_identity(app: Path) -> dict[str, Any]:
    with (app / "Contents/Info.plist").open("rb") as source:
        info = plistlib.load(source)
    if not all(isinstance(info.get(key), str) and info[key].strip()
               for key in ("CFBundleShortVersionString", "CFBundleVersion")):
        raise ValueError("Claude version/build metadata is missing")
    archives = {}
    for relative in (repair.APP_ASAR_REL, repair.patch.FRONTEND_ASAR_REL):
        with (app / relative).open("rb") as source:
            digest = hashlib.sha256()
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
            archives[str(relative)] = digest.hexdigest()
    return {"version": info["CFBundleShortVersionString"], "build": info["CFBundleVersion"],
            "archives": archives}


def make_snapshot(app: Path) -> dict[str, Any]:
    if not app.is_dir():
        raise ValueError(f"Claude app not found: {app}")
    version = repair.patch.bundle_version(app)
    if version == "0":
        raise ValueError(f"Cannot read Claude's bundle version: {app}")
    require_official_signature(app)
    identity = bundle_identity(app)
    catalogs = read_source_catalogs(app)
    if bundle_identity(app) != identity:
        raise ValueError("Claude changed during inspection; retry after the official update completes")
    return {
        **identity,
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "catalogs": catalogs,
        "fingerprints": {
            name: catalog_fingerprint(catalog) for name, catalog in catalogs.items()
        },
    }


def translation_alignment(
    source: dict[str, str], translation: dict[str, str]
) -> dict[str, Any]:
    source_ids, translation_ids = set(source), set(translation)
    common = source_ids & translation_ids
    return {
        "source_count": len(source_ids),
        "translated_count": sum(source[key] != translation[key] for key in common),
        "untranslated_count": sum(source[key] == translation[key] for key in common),
        "missing_ids": sorted(source_ids - translation_ids),
        "unused_ids": sorted(translation_ids - source_ids),
    }


def catalog_diff(
    previous: dict[str, str], current: dict[str, str]
) -> dict[str, Any]:
    previous_ids, current_ids = set(previous), set(current)
    shared = previous_ids & current_ids
    return {
        "added": {key: current[key] for key in sorted(current_ids - previous_ids)},
        "removed": {key: previous[key] for key in sorted(previous_ids - current_ids)},
        "changed": {
            key: {"before": previous[key], "after": current[key]}
            for key in sorted(shared)
            if previous[key] != current[key]
        },
    }


def load_baseline(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        baseline = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read update baseline {path}: {error}") from error
    if (
        not isinstance(baseline, dict)
        or baseline.get("format_version") != FORMAT_VERSION
        or not isinstance(baseline.get("snapshot"), dict)
        or not isinstance(baseline["snapshot"].get("catalogs"), dict)
    ):
        raise ValueError(f"Unsupported or incomplete update baseline: {path}")
    return baseline


def save_baseline(path: Path, snapshot: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format_version": FORMAT_VERSION,
        "snapshot": snapshot,
    }
    fd, temporary_name = tempfile.mkstemp(prefix=".baseline-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2)
            output.write("\n")
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def inspect(app: Path, baseline_path: Path) -> dict[str, Any]:
    current = make_snapshot(app)
    baseline = load_baseline(baseline_path)
    translations = {
        name: repair.json_file(resource_path)
        for name, (_, resource_path) in CATALOGS.items()
    }
    alignment = {
        name: translation_alignment(current["catalogs"][name], translations[name])
        for name in CATALOGS
    }
    if baseline is None:
        return {
            "status": "baseline_missing",
            "supported_installer_version": repair.VERSION,
            "installer_compatible": current["version"] == repair.VERSION,
            "current": {
                "version": current["version"],
                "build": current.get("build"),
                "archives": current.get("archives"),
                "fingerprints": current["fingerprints"],
            },
            "translation_alignment": alignment,
            "message": "No baseline exists. Record the current official app before monitoring updates.",
        }

    previous = baseline["snapshot"]
    diffs = {
        name: catalog_diff(previous["catalogs"].get(name, {}), current["catalogs"][name])
        for name in CATALOGS
    }
    changed = (
        previous.get("version") != current["version"]
        or previous.get("fingerprints") != current["fingerprints"]
        or previous.get("build") != current.get("build")
        or previous.get("archives") != current.get("archives")
    )
    return {
        "status": "changed" if changed else "unchanged",
        "supported_installer_version": repair.VERSION,
        "installer_compatible": current["version"] == repair.VERSION,
        "current": {
            "version": current["version"],
            "build": current.get("build"),
            "archives": current.get("archives"),
            "fingerprints": current["fingerprints"],
        },
        "previous": {
            "version": previous.get("version"),
            "build": previous.get("build"),
            "archives": previous.get("archives"),
            "fingerprints": previous.get("fingerprints"),
        },
        "diff": diffs,
        "translation_alignment": alignment,
        "message": (
            "Upstream resources changed. Review the diff; no app files were modified."
            if changed
            else "No upstream localization resource changes detected."
        ),
    }


def format_report(report: dict[str, Any]) -> str:
    lines = [
        f"Status: {report['status']}",
        f"Claude version: {report['current']['version']}",
        f"Claude build: {report['current'].get('build')}",
        f"Legacy installer supports: {report['supported_installer_version']} "
        f"({'yes' if report['installer_compatible'] else 'no'})",
        report["message"],
    ]
    for name, coverage in report["translation_alignment"].items():
        lines.append(
            f"{name}: {coverage['translated_count']} translated, "
            f"{coverage['untranslated_count']} unchanged, "
            f"{len(coverage['missing_ids'])} missing, "
            f"{len(coverage['unused_ids'])} stale IDs"
        )
    for name, diff in report.get("diff", {}).items():
        lines.append(
            f"{name} changes: +{len(diff['added'])} "
            f"-{len(diff['removed'])} ~{len(diff['changed'])}"
        )
        lines.extend(f"  + {key}: {value}" for key, value in diff["added"].items())
        lines.extend(f"  - {key}: {value}" for key, value in diff["removed"].items())
        lines.extend(
            f"  ~ {key}: {value['before']} -> {value['after']}"
            for key, value in diff["changed"].items()
        )
    return "\n".join(lines)


def notify(message: str) -> None:
    escaped = message.replace("\\", "\\\\").replace('"', '\\"')
    subprocess.run(
        [
            "osascript",
            "-e",
            f'display notification "{escaped}" with title "Claude-CN update check"',
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def install_monitor(app: Path, state_dir: Path) -> Path:
    baseline_path = state_dir / "upstream-baseline.json"
    if load_baseline(baseline_path) is None:
        raise ValueError("Record an official-app baseline before installing the monitor")
    launch_agents = Path.home() / "Library/LaunchAgents"
    launch_agents.mkdir(parents=True, exist_ok=True)
    plist_path = launch_agents / f"{MONITOR_LABEL}.plist"
    if plist_path.exists():
        raise ValueError(f"Update monitor already exists; remove it first: {plist_path}")
    script = Path(__file__).resolve()
    user_app = Path(os.path.expanduser(str(app)))
    absolute_state_dir = state_dir.expanduser().resolve()
    absolute_state_dir.mkdir(parents=True, exist_ok=True)
    log_path = absolute_state_dir / "update-monitor.log"
    configuration = {
        "Label": MONITOR_LABEL,
        "ProgramArguments": [
            sys.executable,
            str(script),
            "--app",
            str(user_app),
            "--state-dir",
            str(absolute_state_dir),
            "--notify",
        ],
        "RunAtLoad": True,
        "StartInterval": 3600,
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
    }
    fd, temporary_name = tempfile.mkstemp(prefix=".update-guard-", dir=launch_agents)
    try:
        with os.fdopen(fd, "wb") as output:
            plistlib.dump(configuration, output)
        os.replace(temporary_name, plist_path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)
    uid = os.getuid()
    result = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        plist_path.unlink(missing_ok=True)
        raise RuntimeError(f"Could not start update monitor: {result.stderr.strip()}")
    return plist_path


def remove_monitor() -> None:
    plist_path = Path.home() / "Library/LaunchAgents" / f"{MONITOR_LABEL}.plist"
    subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}", str(plist_path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    plist_path.unlink(missing_ok=True)


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--app", type=Path, default=Path("/Applications/Claude.app"))
    cli.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    action = cli.add_mutually_exclusive_group()
    action.add_argument("--record-baseline", action="store_true")
    action.add_argument("--accept-current", action="store_true")
    action.add_argument("--install-monitor", action="store_true")
    action.add_argument("--remove-monitor", action="store_true")
    cli.add_argument("--notify", action="store_true")
    cli.add_argument("--json", action="store_true")
    cli.add_argument("--check-latest", action="store_true",
                     help="Also query the public official macOS release feed (no app update)")
    args = cli.parse_args()
    app = args.app.expanduser()
    state_dir = args.state_dir.expanduser().resolve()
    baseline_path = state_dir / "upstream-baseline.json"

    if args.remove_monitor:
        remove_monitor()
        print("Claude-CN update monitor removed.")
        return 0
    if args.record_baseline or args.accept_current:
        current = make_snapshot(app)
        existing = load_baseline(baseline_path)
        if existing and not args.accept_current:
            raise SystemExit(
                "A baseline already exists. Inspect the diff, then use --accept-current explicitly."
            )
        save_baseline(baseline_path, current)
        print(f"Recorded official Claude {current['version']} resource baseline: {baseline_path}")
        return 0
    if args.install_monitor:
        print(f"Hourly update monitor installed: {install_monitor(app, state_dir)}")
        return 0

    result = inspect(app, baseline_path)
    if args.check_latest:
        result["official_latest"] = compare_latest(result["current"]["version"], fetch_latest())
    if not result["installer_compatible"]:
        result["message"] += (
            f" The {repair.VERSION} installer cannot be used with this version; no app files were changed."
        )
    print(
        json.dumps(result, ensure_ascii=False, indent=2)
        if args.json
        else format_report(result) + (
            f"\nOfficial latest: {result['official_latest']['version']} "
            f"({result['official_latest']['status']})"
            if "official_latest" in result else ""
        )
    )
    needs_attention = (result["status"] in {"changed", "baseline_missing"}
                       or not result["installer_compatible"]
                       or result.get("official_latest", {}).get("status", "current") != "current")
    if args.notify and needs_attention:
        notify(result["message"])
    return 2 if needs_attention else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Claude-CN update check stopped: {error}", file=sys.stderr)
        if "--notify" in sys.argv:
            notify("Update check stopped; see the monitor log")
        raise SystemExit(1)
