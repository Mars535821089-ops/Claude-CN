#!/usr/bin/env python3
"""Prepare or patch both 4.23118.0 locale gates in a disposable ASAR stage.

Never call this on a running app. Only the two reviewed JS fingerprints may
change, and a partially migrated pair stops rather than claiming Chinese UI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

from upstream_patch_lib import BASE_LANGUAGE_LIST, LANG_LIST_RE

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "resources/candidates/claude-desktop-4.23118.0/language-whitelist-reviewed.json"
EXPECTED_FILES = {
    "assets/v1/shared-67-CqVL4EFG.js",
    "assets/v1/shared-99-Do2dhk0C.js",
}


def load_reviewed() -> dict[str, dict]:
    document = json.loads(MANIFEST.read_text())
    files = document.get("files")
    if (document.get("version") != "4.23118.0"
            or document.get("source_ion_dist_asar_sha256") !=
            "fe3dab2e327b043878176ed4b354eda6213055b5bc9270e0cfebf0748362f9d3"
            or not isinstance(files, dict) or set(files) != EXPECTED_FILES):
        raise ValueError("reviewed locale gate source version or files changed")
    return files


def _safe_path(root: Path, name: str) -> Path:
    if (not isinstance(name, str) or "\\" in name
            or any(part in {"", ".", ".."} for part in name.split("/"))):
        raise ValueError("unsafe locale gate path")
    path = root
    for part in name.split("/"):
        path = path / part
        if path.is_symlink():
            raise ValueError("locale gate symlink refused")
    return path


def verify_source(raw: bytes, spec: dict) -> bytes | None:
    """Return exact reviewed patch bytes, or None for an already-patched JS."""
    actual = hashlib.sha256(raw).hexdigest()
    if actual == spec["patched_sha256"]:
        return None
    if actual != spec["sha256"]:
        raise ValueError("reviewed locale gate fingerprint changed")
    patched, count = LANG_LIST_RE.subn(BASE_LANGUAGE_LIST + ',"zh-CN"]', raw.decode("utf-8"))
    output = patched.encode()
    if count != spec["expected"] or hashlib.sha256(output).hexdigest() != spec["patched_sha256"]:
        raise ValueError("reviewed locale gate count or output fingerprint changed")
    return output


def prepare(root: Path, targets: dict[str, dict]) -> list[tuple[Path, bytes, bytes]]:
    """Perform all fingerprint and syntax-context preflights before any write."""
    if root.is_symlink() or not root.is_dir() or len(targets) != 2:
        raise ValueError("reviewed locale gate stage is missing or incomplete")
    prepared = []
    states = set()
    for name, spec in sorted(targets.items()):
        path = _safe_path(root, name)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("locale gate source is not a regular file")
        raw = path.read_bytes()
        patched = verify_source(raw, spec)
        if patched is None:
            states.add("already_patched")
            continue
        prepared.append((path, raw, patched))
        states.add("source")
    if len(states) != 1:
        raise ValueError("partial locale gate stage: old and patched arrays are mixed")
    return prepared


def apply_prepared(prepared: list[tuple[Path, bytes, bytes]]) -> None:
    """Apply only prevalidated disposable-stage bytes; preserve on later errors."""
    originals: list[tuple[Path, bytes, bytes, int]] = []
    temporaries: list[Path] = []
    try:
        for path, before, after in prepared:
            if path.read_bytes() != before:
                raise ValueError("locale gate stage changed after preflight")
            fd, name = tempfile.mkstemp(prefix=".locale-reviewed-", dir=path.parent)
            tmp = Path(name)
            temporaries.append(tmp)
            with os.fdopen(fd, "wb") as output:
                output.write(after)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(tmp, path.stat().st_mode & 0o777)
        for (path, before, after), temporary in zip(prepared, temporaries, strict=True):
            if path.read_bytes() != before:
                raise ValueError("locale gate stage changed during replacement")
            mode = path.stat().st_mode & 0o777
            os.replace(temporary, path)
            originals.append((path, before, after, mode))
    except BaseException:
        for path, before, after, mode in reversed(originals):
            if path.read_bytes() == after:
                fd, name = tempfile.mkstemp(prefix=".locale-recovery-", dir=path.parent)
                with os.fdopen(fd, "wb") as output:
                    output.write(before)
                    output.flush()
                    os.fsync(output.fileno())
                os.chmod(name, mode)
                os.replace(name, path)
        raise
    finally:
        for temporary in temporaries:
            temporary.unlink(missing_ok=True)


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--extracted-frontend", type=Path, required=True)
    args = cli.parse_args()
    try:
        files = load_reviewed()
        prepared = prepare(args.extracted_frontend, files)
        print(json.dumps({"version": "4.23118.0", "files": len(files),
                          "replacement_files": len(prepared), "applied_to_disposable_stage": False,
                          "installed_app_unchanged": True}, sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps({"status": "unverified", "reason_type": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
