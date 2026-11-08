#!/usr/bin/env python3
"""Build a NONINSTALLABLE Claude 4.23118 Chinese/Cowork compatibility smoke app.

The only allowed input is the exact signed official source. The output uses a
synthetic Cowork session, and must never replace the user's localized app.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess

import patch_cowork_compaction as cowork
import patch_whitelist_423118 as whitelist
import preview_current as preview
import repair_macos_429 as repair
import verify_combined_423118 as combined
import verify_ui_423118 as ui
from upstream_patch_lib import (
    build_asar, extract_asar, read_asar_entry, read_asar_header,
    update_electron_asar_integrity, verify_electron_asar_integrity,
)

ROOT = Path(__file__).resolve().parents[1]
BUILD_ROOT = Path('/Volumes/Mars-私人服务器/内置盘写入统一管理/01-编译与构建/Codex-provider-switch')
CANDIDATE = ROOT / 'resources/candidates/claude-desktop-4.23118.0'
VENDOR_LOOSE_FALLBACK_SHA256 = 'd05caf6e92c2e5bf5152bf6f53520b688a409d85ec5cd1a11f5594940737960f'


def checked_destination(stage: Path, build_root: Path = BUILD_ROOT) -> Path:
    if not os.path.ismount('/Volumes/Mars-私人服务器' if build_root == BUILD_ROOT else build_root):
        raise ValueError('unmounted external smoke build volume')
    raw_base = build_root.expanduser().absolute()
    raw_target = stage.expanduser().absolute()
    if not raw_target.is_relative_to(raw_base):
        raise ValueError('Claude smoke destination is outside the reviewed build area')
    current = raw_base
    for part in raw_target.relative_to(raw_base).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('Claude smoke build path is redirected')
    base = raw_base.resolve()
    target = raw_target.resolve(strict=False)
    if target.name != 'Claude.app' or not target.is_relative_to(base) or target == base:
        raise ValueError('Claude smoke destination is outside the reviewed build area')
    if target.exists() or target.is_symlink():
        raise ValueError('Claude smoke destination already exists')
    return target


def prepare_ui_edits(roots: dict[str, Path], manifest: dict,
                     translations: dict[str, str]) -> list[tuple[Path, str]]:
    if manifest.get('version') != '4.23118.0' or not isinstance(manifest.get('files'), dict):
        raise ValueError('unknown reviewed UI version or file map')
    originals = []
    for name, spec in manifest['files'].items():
        bundle, sep, relative = name.partition('/')
        if not sep or bundle not in roots or any(part in ('', '.', '..') for part in relative.split('/')):
            raise ValueError('reviewed UI path is unsafe')
        base = roots[bundle]
        path = base / relative
        if (path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(base.resolve())
                or not stat.S_ISREG(path.lstat().st_mode)):
            raise ValueError('reviewed UI entry is missing or redirected')
        source = path.read_text(encoding='utf-8')
        if hashlib.sha256(source.encode()).hexdigest() != spec['sha256']:
            raise ValueError('reviewed UI source fingerprint changed')
        originals.append((path, name, source, spec))
    payload = [{'file': name, 'source': source, 'spec': spec,
                'translations': translations} for _path, name, source, spec in originals]
    result = subprocess.run(
        ['node', str(ROOT / 'scripts/ui_localization.cjs'), '--patch-inline'],
        input=json.dumps(payload, ensure_ascii=False), text=True,
        capture_output=True, timeout=90, check=False,
    )
    if result.returncode:
        raise ValueError('reviewed UI AST or source fingerprint changed')
    patched = json.loads(result.stdout)
    if not isinstance(patched, list) or len(patched) != len(originals):
        raise ValueError('reviewed UI patched entry count changed')
    prepared = []
    for (path, _name, _source, spec), output in zip(originals, patched, strict=True):
        if (not isinstance(output, str) or
                hashlib.sha256(output.encode()).hexdigest() != spec['patched_sha256']):
            raise ValueError('reviewed UI patched fingerprint changed')
        prepared.append((path, output))
    return prepared


def _vendor_fallback(app: Path, expected: str) -> Path:
    vendor = app / 'Contents/Resources/ion-dist'
    index = vendor / 'index.html'
    if (vendor.is_symlink() or index.is_symlink() or not vendor.is_dir()
            or not index.is_file() or set(vendor.iterdir()) != {index}
            or hashlib.sha256(index.read_bytes()).hexdigest() != expected):
        raise ValueError('official loose fallback HTML differs; refusing ASAR repack')
    return index


def prepare_frontend_stage(
    app: Path, *, expected_fallback_sha256: str = VENDOR_LOOSE_FALLBACK_SHA256,
) -> dict:
    """Official 4.23118 ships an ASAR AND a legacy-window fallback index.html."""
    index = _vendor_fallback(app, expected_fallback_sha256)
    resources = app / 'Contents/Resources'
    asar = resources / 'ion-dist.asar'
    work = resources / '.claude-cn-423118-ion-work'
    if not asar.is_file() or asar.is_symlink() or work.exists() or work.is_symlink():
        raise ValueError('reviewed frontend ASAR/work path changed')
    unpacked: set[str] = set()
    extract_asar(asar, work, unpacked)
    return {'work': work, 'asar': asar, 'unpacked': unpacked,
            'fallback': index, 'fallback_sha256': expected_fallback_sha256}


def finalize_frontend_stage(app: Path, state: dict) -> None:
    _vendor_fallback(app, state['fallback_sha256'])
    work, asar = state['work'], state['asar']
    if not work.is_dir() or asar.is_symlink():
        raise ValueError('reviewed frontend stage changed during repack')
    temporary = asar.with_name('ion-dist.asar.smoke-new')
    if temporary.exists() or temporary.is_symlink():
        raise ValueError('frontend repack slot is occupied')
    try:
        build_asar(work, temporary, state['unpacked'])
        raw = temporary.read_bytes()
        read_asar_header(raw, temporary)
        _vendor_fallback(app, state['fallback_sha256'])
        os.replace(temporary, asar)
    finally:
        temporary.unlink(missing_ok=True)
    shutil.rmtree(work)
    update_electron_asar_integrity(app, 'Resources/ion-dist.asar', asar)


def _catalogs() -> dict[str, bytes]:
    manifest = json.loads((CANDIDATE / 'manifest.json').read_text())
    return {group: (CANDIDATE / f'{group}-zh-CN.json').read_bytes()
            for group in preview.PATHS if manifest[group]['candidate_sha256'] ==
            hashlib.sha256((CANDIDATE / f'{group}-zh-CN.json').read_bytes()).hexdigest()}


def build(source: Path, stage: Path, build_root: Path = BUILD_ROOT) -> dict:
    target = checked_destination(stage, build_root)
    report = combined.verify(source)
    if report['version'] != '4.23118.0' or report['signed_official_source'] is not True:
        raise ValueError('signed 4.23118.0 source is required')
    catalogs = _catalogs()
    if set(catalogs) != set(preview.PATHS):
        raise ValueError('one or more reviewed locale catalogs changed')
    manifest = json.loads(ui.MANIFEST.read_text())
    reviewed = {**json.loads((ROOT / 'resources/ui-reviewed-zh-CN.json').read_text()),
                **json.loads((CANDIDATE / 'hardcoded-ui-zh-CN.json').read_text())}
    source_hashes = {relative: hashlib.sha256((source / 'Contents/Resources' / relative).read_bytes()).hexdigest()
                     for relative in ('app.asar', 'ion-dist.asar')}
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['/bin/cp', '-cR', str(source), str(target)], check=True, timeout=210)
    for relative, expected in source_hashes.items():
        if hashlib.sha256((target / 'Contents/Resources' / relative).read_bytes()).hexdigest() != expected:
            raise ValueError('copied source archives changed before isolated smoke patching')
    frontend = None
    desktop = target / 'Contents/Resources/claude-cn-smoke-app-work'
    desktop_unpacked: set[str] = set()
    try:
        frontend = prepare_frontend_stage(target)
        app_asar = target / repair.APP_ASAR_REL
        extract_asar(app_asar, desktop, desktop_unpacked)
        roots = {'app.asar': desktop, 'ion-dist.asar': frontend['work']}
        edits = prepare_ui_edits(roots, manifest, reviewed)
        gate_edits = whitelist.prepare(frontend['work'], whitelist.load_reviewed())
        hostloop = desktop / cowork.NEW_ENTRY
        original_hostloop = hostloop.read_text(encoding='utf-8')
        patched_hostloop = cowork.patch_host_loop_source(
            original_hostloop, '/dev/null', '/dev/null', 'local-SYNTHETIC-REVIEW-ONLY',
            version='4.23118.0',
        )
        if patched_hostloop.count(cowork.MARKER) != 1:
            raise ValueError('synthetic Cowork hook source is incomplete')
        for path, output in edits:
            path.write_text(output, encoding='utf-8')
        whitelist.apply_prepared(gate_edits)
        hostloop.write_text(patched_hostloop, encoding='utf-8')
        for group, data in catalogs.items():
            relative = ('resources/i18n/zh-CN.json' if group == 'desktop' else
                        'i18n/' + ('dynamic/' if group == 'dynamic' else '') + 'zh-CN.json')
            root = desktop if group == 'desktop' else frontend['work']
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        staged_asar = app_asar.with_name('app.asar.smoke-new')
        build_asar(desktop, staged_asar, desktop_unpacked)
        os.replace(staged_asar, app_asar)
        update_electron_asar_integrity(target, 'Resources/app.asar', app_asar)
        shutil.rmtree(desktop)
        desktop = None
        finalize_frontend_stage(target, frontend)
        frontend = None
        verify_electron_asar_integrity(target)
        repair.patch.resign_app(target)
        subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(target)],
                       check=True, capture_output=True, timeout=45)
        _verify_smoke_content(target, manifest, catalogs)
        result = {'version': report['version'], 'smoke_only': True,
                  'synthetic_cowork_session': True, 'installed': False,
                  'signed_local_candidate': True, 'catalog_messages': report['catalog_messages'],
                  'reviewed_ui_strings': report['reviewed_ui_strings'], 'locale_gates': report['locale_gates'],
                  'source_asar_sha256': source_hashes,
                  'candidate_asar_sha256': {relative: hashlib.sha256(
                      (target / 'Contents/Resources' / relative).read_bytes()).hexdigest()
                      for relative in source_hashes}}
        receipt = target.parent / 'smoke-receipt.json'
        if receipt.exists() or receipt.is_symlink():
            raise ValueError('smoke receipt already exists')
        receipt.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
        return result
    finally:
        if desktop is not None and desktop.exists():
            shutil.rmtree(desktop)
        if frontend is not None and frontend['work'].exists():
            shutil.rmtree(frontend['work'])


def _verify_smoke_content(stage: Path, manifest: dict, catalogs: dict[str, bytes]) -> None:
    for bundle in ('app.asar', 'ion-dist.asar'):
        path = stage / 'Contents/Resources' / bundle
        raw = path.read_bytes()
        size, _, header = read_asar_header(raw, path)
        for name, spec in manifest['files'].items():
            if name.startswith(bundle + '/'):
                actual = read_asar_entry(raw, size, header, name[len(bundle) + 1:])
                if actual is None or hashlib.sha256(actual).hexdigest() != spec['patched_sha256']:
                    raise ValueError('packaged UI entry failed hash verification')
        if bundle == 'app.asar':
            host = read_asar_entry(raw, size, header, cowork.NEW_ENTRY)
            if host is None or host.decode().count(cowork.MARKER) != 1:
                raise ValueError('packaged Cowork hook missing')
        else:
            for name, spec in whitelist.load_reviewed().items():
                actual = read_asar_entry(raw, size, header, name)
                if actual is None or hashlib.sha256(actual).hexdigest() != spec['patched_sha256']:
                    raise ValueError('packaged locale gate differs')
        for group, expected in catalogs.items():
            if (bundle == 'app.asar') != (group == 'desktop'):
                continue
            relative = ('resources/i18n/zh-CN.json' if group == 'desktop' else
                        'i18n/' + ('dynamic/' if group == 'dynamic' else '') + 'zh-CN.json')
            actual = read_asar_entry(raw, size, header, relative)
            if actual is None or json.loads(actual) != json.loads(expected):
                raise ValueError('packaged Chinese catalog differs')


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--official-app', type=Path, required=True)
    cli.add_argument('--stage', type=Path, required=True)
    args = cli.parse_args()
    try:
        print(json.dumps(build(args.official_app, args.stage), sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps({'status': 'unverified', 'reason_type': type(error).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
