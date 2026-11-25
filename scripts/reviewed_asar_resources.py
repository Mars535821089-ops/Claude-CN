"""Package reviewed resource bytes using the existing ASAR format helpers.

No app copying, signing, launching, installation or settings operations. The
result is an engineering resource archive, never an installable application.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

# These helpers inspect/preserve ASAR data only; no 5.x build entry is invoked.
from build_smoke_51231 import archive_entries, preserve_entry_metadata
from upstream_patch_lib import extract_asar, build_asar

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative_name(name):
    return (isinstance(name, str) and not name.startswith('/')
            and all(part not in ('', '.', '..') and '\\' not in part
                    for part in name.split('/')))


def unpacked_snapshot(archive, entries):
    result = {}
    base = Path(str(archive) + '.unpacked')
    for name, entry in entries.items():
        if entry[0]:
            path = base / name
            if (path.is_symlink() or not path.is_file()
                    or not path.resolve().is_relative_to(base.resolve())):
                raise ValueError('Redirected or missing unpacked resource')
            result[name] = (digest(path), path.stat().st_mode & 0o777)
    return result


def repack(source, slot, changes, expected_source_sha256):
    source, slot = Path(source).absolute(), Path(slot).absolute()
    parent = slot.parent.resolve(strict=True)
    if (slot.exists() or slot.is_symlink() or slot.resolve().is_relative_to(ROOT.resolve())
            or any(p.suffix.lower() == '.app' for p in (parent, *parent.parents))
            or '..' in slot.parts or source.is_symlink() or not source.is_file()):
        raise ValueError('Resource output must be a fresh slot outside applications and source')
    if digest(source) != expected_source_sha256:
        raise ValueError('Official resource source fingerprint changed')
    before = archive_entries(source)
    if not all(relative_name(name) for name in before):
        raise ValueError('Unsafe source archive entry')
    if (not isinstance(changes, dict) or not changes
            or any(not relative_name(name) or not isinstance(raw, bytes)
                   or before.get(name, (False,))[0] for name, raw in changes.items())):
        raise ValueError('Unsafe or unpacked replacement resource')
    if any(name in before and before[name][1] == raw for name, raw in changes.items()):
        raise ValueError('Declared resource change is a no-op')
    original_unpacked = unpacked_snapshot(source, before)
    # Exclusive reservation never replaces another worker's output or data.
    slot.mkdir()
    target = slot / source.name
    with tempfile.TemporaryDirectory(prefix='resource-work-', dir=slot) as directory:
        work = Path(directory)
        unpacked = set()
        extract_asar(source, work, unpacked)
        for name, raw in changes.items():
            destination = work / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        build_asar(work, target, unpacked)
        preserve_entry_metadata(source, target)
    after = archive_entries(target)
    if after.keys() != before.keys() | changes.keys():
        raise ValueError('Packaged vendor entry coverage changed')
    changed = []
    for name, entry in after.items():
        if name in changes:
            if entry[0] or entry[1] != changes[name]:
                raise ValueError('Packaged replacement bytes differ')
            if name in before and entry[2] != before[name][2]:
                raise ValueError('Reviewed entry metadata flags changed')
            changed.append(name)
        elif entry != before[name]:
            raise ValueError('Unreviewed vendor entry changed: ' + name)
    if (unpacked_snapshot(target, after) != original_unpacked
            or digest(source) != expected_source_sha256):
        raise ValueError('Unpacked bytes, modes or original source changed')
    receipt = {'status': 'resource_archive_only_not_installable', 'installed': False,
        'launched': False, 'signed': False, 'source_sha256': expected_source_sha256,
        'output_sha256': digest(target), 'changed_entries': sorted(changed),
        'original_entries': len(before), 'packaged_entries': len(after),
        'preserved_unpacked_files': len(original_unpacked),
        'source_unchanged': True, 'unreviewed_entries_unchanged': True}
    with (slot / 'resource-archive-receipt.json').open('x') as stream:
        stream.write(json.dumps(receipt, indent=2) + '\n')
    return receipt
