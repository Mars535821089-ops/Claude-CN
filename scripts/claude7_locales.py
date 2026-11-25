#!/usr/bin/env python3
"""Reproduce reviewed Claude 7 locale resources; never modify or launch apps.

This is a source-candidate assembler, not a signing or installation entry point.
Keep the earlier version-specific installers and their gates unchanged.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess

import audit_migration as migration
import claude5_candidate as legacy
from official_release import fetch_latest
from upstream_patch_lib import (read_asar_header, read_asar_entry,
                                verify_electron_asar_integrity)
from update_guard import catalog_fingerprint, require_official_signature

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / 'resources/candidates/claude-desktop-7.37095.0'
VERSION = '7.37095.0'
VOLUME = Path('/Volumes/Mars-私人服务器')
OUTPUT_ROOT = VOLUME / '内置盘写入统一管理/01-编译与构建/Claude-CN'
ARCHIVES = {
    'app.asar': '8c10482076f72213de7127d261fb1467041bccb4df058259fd82ca799cf0209a',
    'ion-dist.asar': 'dfa83d8118169adfd3d7932b015b841f570a4229948607886592520f95cc1098',
}
ENGLISH = {
    'desktop': 'f4e538d5105c12125ac8b8fe124001eabec44ef329c5c798797146637cc75125',
    'frontend': 'c282210cd828837f89ae690f8f885c4e09605e0b12dd136cafd648d1bd9d6c7f',
    'dynamic': '2ccedb4cff2d5b9d2de99ec662d18eba99bdad00b5ee3963a3882399aae85654',
}
LEGACY_MANIFEST = 'ac5b76a0734f7cd69ffc512dd66775c4d61fb30b538f1f2e5371c41f24701315'
REVIEWS = ('desktop-reviewed-additions.json', 'frontend-reviewed-phrase-reuse.json',
           'frontend-reviewed-additions.json')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read_review_snapshot(path):
    raw = path.read_bytes()
    return migration.parse_review(raw), sha(raw)


def compose(catalogs, prior, reviews):
    """Source matching plus explicit new additions; no unreviewed fallback."""
    if set(catalogs) != set(ENGLISH) or set(prior) != {'frontend', 'dynamic'}:
        raise ValueError('Unexpected locale group scope')
    result = {}
    for group, catalog in catalogs.items():
        previous = prior.get(group, {'en': {}, 'zh': {}})
        result[group] = migration.merge_exact(previous['en'], catalog['en'],
            previous['zh'], official=catalog['official'])
    for review in reviews:
        # Native desktop plural normalization has a separate hash-bound review.
        # Ordinary additions in EVERY group may only fill unresolved IDs.
        for group, pack in review.get('catalogs', {}).items():
            if group not in result:
                raise ValueError('Unknown reviewed locale group')
            if (not pack.get('translations', {}).keys() <= result[group]['missing'].keys()
                    or group != 'desktop' and pack.get('plural_shapes')):
                raise ValueError('New additions would overwrite verified translations')
        migration.apply_review(catalogs, result, review, VERSION)
    gaps = {group: len(pack['missing']) for group, pack in result.items() if pack['missing']}
    if gaps:
        raise ValueError(f'Locale candidate has unreviewed source messages: {gaps}')
    checked = subprocess.run(['node', str(ROOT / 'scripts/audit_messages.cjs')],
        input=json.dumps({'catalogs': {group: {'en': catalogs[group]['en'],
            'zh': pack['translations'], 'strictSelections': True}
            for group, pack in result.items()}}, ensure_ascii=False),
        text=True, capture_output=True, timeout=180, check=False)
    validation = json.loads(checked.stdout)
    if checked.returncode or validation['errors']:
        raise ValueError('ICU validation failed: ' + checked.stdout)
    return result


def read_official(app):
    require_official_signature(app)
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    if (app.is_symlink() or info.get('CFBundleShortVersionString') != VERSION
            or info.get('CFBundleIdentifier') != 'com.anthropic.claudefordesktop'):
        raise ValueError('Claude 7 source identity changed')
    archives = {}
    for name, digest in ARCHIVES.items():
        path = app / 'Contents/Resources' / name
        raw = path.read_bytes()
        if path.is_symlink() or sha(raw) != digest:
            raise ValueError('Claude 7 official archive changed: ' + name)
        size, _, header = read_asar_header(raw, path)
        archives[name] = raw, size, header
    verify_electron_asar_integrity(app)
    catalogs = {}
    for group, (archive, name) in legacy.PATHS.items():
        english = json.loads(read_asar_entry(*archives[archive], name))
        if catalog_fingerprint(english) != ENGLISH[group]:
            raise ValueError('Claude 7 English catalog fingerprint changed: ' + group)
        native = name.replace('en-US.json', 'zh-Hans.json')
        raw = read_asar_entry(*archives[archive], native)
        catalogs[group] = {'en': english, 'official': json.loads(raw) if raw else {}}
    return catalogs


def read_legacy(app):
    pack = legacy.CANDIDATE
    manifest_raw = (pack / 'manifest.json').read_bytes()
    if sha(manifest_raw) != LEGACY_MANIFEST:
        raise ValueError('Reviewed legacy source manifest changed')
    manifest = json.loads(manifest_raw)
    for name, digest in {**manifest['generated_files_sha256'],
                         **manifest['authored_inputs_sha256']}.items():
        path = pack / name
        if Path(name).name != name or path.is_symlink() or sha(path.read_bytes()) != digest:
            raise ValueError('Reviewed legacy resource changed: ' + name)
    source = legacy.read_source(app)
    prior = {}
    for group in ('frontend', 'dynamic'):
        english = json.loads(source(*legacy.PATHS[group]))
        chinese = migration.load_review(pack / f'{group}-zh-CN.json')
        prior[group] = {'en': english, 'zh': chinese}
    return prior


def assemble(app, previous_app, output):
    """A fresh external slot contains only reproducible source resources."""
    if not VOLUME.is_mount():
        raise ValueError('External build volume unavailable')
    parent = output.parent.resolve(strict=True)
    if (output.exists() or output.is_symlink() or not parent.is_relative_to(OUTPUT_ROOT.resolve())
            or any(p.suffix.lower() == '.app' for p in (parent, *parent.parents))
            or any(output.resolve().is_relative_to(p.resolve())
                   for p in (ROOT, app, previous_app))):
        raise ValueError('Choose a fresh external locale output outside apps and source')
    latest = fetch_latest()
    if latest['version'] != VERSION:
        raise ValueError('New official release requires source migration before assembly')
    checked_at = dt.datetime.now(dt.timezone.utc).isoformat()
    catalogs = read_official(app)
    prior = read_legacy(previous_app)
    snapshots = {name: read_review_snapshot(PACK / name) for name in REVIEWS}
    review_hashes = {name: pair[1] for name, pair in snapshots.items()}
    reviews = [snapshots[name][0] for name in REVIEWS]
    result = compose(catalogs, prior, reviews)
    # A changed input must never be published under the earlier snapshot.
    if (read_official(app) != catalogs or read_legacy(previous_app) != prior
            or review_hashes != {name: sha((PACK / name).read_bytes()) for name in REVIEWS}):
        raise ValueError('Source changed during locale assembly')
    outputs = {}
    for group, pack in result.items():
        outputs[f'{group}-zh-CN.json'] = pack['translations']
        outputs[f'{group}-provenance.json'] = pack['provenance']
    manifest = {'version': VERSION, 'status': 'reviewed_locales_only_not_installable',
        'source_archives': ARCHIVES, 'source_english_fingerprints': ENGLISH,
        'legacy_manifest_sha256': LEGACY_MANIFEST,
        'review_inputs_sha256': review_hashes,
        'official_release': latest, 'official_checked_utc': checked_at,
        'catalog_counts': {group: len(pack['translations']) for group, pack in result.items()},
        'strict_icu_errors': 0, 'installed': False, 'launched': False,
        'customizations_ported': False, 'gui_verified': False,
        'remaining': ['hardcoded UI', 'sidebar and title fallback', 'role mappings',
                      'Cowork', 'Keychain-safe signing', 'install and native UI acceptance']}
    # mkdir is exclusive: a concurrent producer cannot have its output replaced.
    output.mkdir()
    for name, value in outputs.items():
        raw = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()
        with (output / name).open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    manifest['generated_files_sha256'] = {name: sha((output / name).read_bytes()) for name in outputs}
    with (output / 'manifest.json').open('x') as stream:
        stream.write(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--official-app', type=Path, required=True)
    parser.add_argument('--previous-official-app', type=Path, default=legacy.BASE / 'Claude.app')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(assemble(args.official_app, args.previous_official_app, args.output),
                     ensure_ascii=False))
