#!/usr/bin/env python3
"""Explicit, fingerprint-pinned 4.4212.0 installer; old preview gates stay intact."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess

import preview_current as preview
import repair_macos_429 as repair
from ui_html import apply_html

RECEIPT = Path('Contents/Resources/claude-cn-install.json')


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def check_report(report: dict, profile: dict) -> None:
    identity = report['installed']
    if any(identity[key] != profile[key] for key in ('version', 'build', 'archives')):
        raise ValueError('Preview identity differs from the reviewed official source')
    if set(report['catalogs']) != set(preview.PATHS) or any(
            row['unreviewed'] for row in report['catalogs'].values()):
        raise ValueError('Preview contains unreviewed message gaps')


def checked_source(root: Path, name: str, spec: dict) -> Path:
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Unsafe UI path')
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('UI symlink refused')
    if digest(path.read_bytes()) != spec['sha256']:
        raise ValueError('Unknown UI fingerprint: ' + name)
    return path


def patch_reviewed_ui(root: Path, bundle: str, manifest: dict, dictionary: dict) -> None:
    # This is a separate explicitly authorized current-version installation path.
    # Never remove preview_only or feed a preview into the legacy installer.
    prepared = []
    js = []
    for name, spec in manifest['files'].items():
        if not name.startswith(bundle + '/'):
            continue
        path = checked_source(root, name[len(bundle) + 1:], spec)
        source = path.read_text()
        if name.endswith('.html'):
            result = apply_html(source, spec['rules'], dictionary, spec.get('inline_scripts'))
            if digest(result.encode()) != spec['patched_sha256']:
                raise ValueError('HTML output fingerprint changed: ' + name)
            prepared.append((path, result))
        else:
            js.append((path, {'file': name, 'source': source, 'spec': spec,
                              'translations': dictionary}))
    if js:
        result = subprocess.run(['node', str(repair.ROOT/'scripts/ui_localization.cjs'),
                                 '--patch-inline'], input=json.dumps([item for _, item in js]),
                                capture_output=True, text=True, check=True)
        prepared.extend((path, result) for (path, _), result
                        in zip(js, json.loads(result.stdout), strict=True))
    for path, content in prepared:
        path.write_text(content)


def verify_stage(app: Path) -> None:
    profile = json.loads(preview.PROFILE.read_text())
    info = plistlib.loads((app/'Contents/Info.plist').read_bytes())
    if (info.get('CFBundleShortVersionString'), info.get('CFBundleVersion')) != (
            profile['version'], profile['build']):
        raise ValueError('Staged version/build differs')
    receipt = json.loads((app/RECEIPT).read_text())
    if receipt['archives'] != preview.archive_hashes(app):
        raise ValueError('Staged archive fingerprint changed')
    if receipt['source_archives'] != profile['archives']:
        raise ValueError('Staged official source differs')
    catalogs = preview.load_catalogs(app)
    for group, (source, _, _) in preview.PATHS.items():
        raw = (repair.asar_json(app, 'resources/i18n/zh-CN.json') if group == 'desktop'
               else json.loads(repair.patch.read_frontend_entry(app,
                   'i18n/' + ('dynamic/' if group == 'dynamic' else '') + 'zh-CN.json')))
        if raw.keys() != catalogs[group]['en'].keys():
            raise ValueError('Staged locale message IDs differ: ' + group)
        if digest(json.dumps(raw, ensure_ascii=False, sort_keys=True).encode()) != receipt['catalog_sha256'][group]:
            raise ValueError('Staged locale content changed: ' + group)
    repair.patch.verify(app, 'zh-CN')


def build(app: Path, stage: Path, review: Path) -> None:
    profile = json.loads(preview.PROFILE.read_text())
    preview.verify_identity(app, profile)
    if stage.exists():
        raise ValueError('Stage already exists')
    # Recompute every resource from the pinned official app; do not trust an
    # arbitrary pre-existing preview directory or caller-supplied manifest.
    report = preview.prepare(app, review, True)
    check_report(report, profile)
    manifest = json.loads((review/'ui-patch-preview.json').read_text())
    pinned = json.loads((repair.RESOURCES/'current-ui-patch-preview.json').read_text())
    if manifest != pinned:
        raise ValueError('Generated UI preview differs from reviewed repository manifest')
    reviewed, _ = preview.load_reviewed()
    dictionary = {**repair.json_file(repair.RESOURCES/'ui-reviewed-zh-CN.json'), **reviewed}
    repair.patch.copy_app(app, stage)
    frontend = repair.patch.prepare_frontend_target(stage)
    desktop = stage/'Contents/Resources/claude-cn-app-work'
    unpacked = set()
    try:
        repair.patch.extract_asar(stage/repair.APP_ASAR_REL, desktop, unpacked)
        patch_reviewed_ui(desktop, 'app.asar', manifest, dictionary)
        patch_reviewed_ui(frontend['work'], 'ion-dist.asar', manifest, dictionary)
        catalogs = {}
        for group in preview.PATHS:
            data = json.loads((review/f'{group}-zh-CN.json').read_text())
            catalogs[group] = digest(json.dumps(data, ensure_ascii=False, sort_keys=True).encode())
            relative = ('resources/i18n/zh-CN.json' if group == 'desktop' else
                        'i18n/' + ('dynamic/' if group == 'dynamic' else '') + 'zh-CN.json')
            path = (desktop if group == 'desktop' else frontend['work']) / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')))
        repair.patch.patch_language_whitelist(stage, 'zh-CN')
        repair.patch.patch_language_display_names(stage)
        for folder in ('zh-CN.lproj', 'zh_CN.lproj'):
            path = stage/'Contents/Resources'/folder
            path.mkdir(exist_ok=True)
            shutil.copy2(repair.RESOURCES/'Localizable.strings', path/'Localizable.strings')
        # Every changed JS file is parsed again after locale whitelist changes.
        scripts = {str(p.relative_to(root)): p.read_text() for root in (desktop, frontend['work'])
                   for p in root.rglob('*.js') if 'node_modules' not in p.parts}
        subprocess.run(['node', str(repair.ROOT/'scripts/audit_messages.cjs')],
                       input=json.dumps({'catalogs': {}, 'scripts': scripts}),
                       text=True, capture_output=True, check=True)
        repair.patch.build_asar(desktop, stage/repair.APP_ASAR_REL, unpacked)
        repair.patch.update_electron_asar_integrity(stage, 'Resources/app.asar', stage/repair.APP_ASAR_REL)
        shutil.rmtree(desktop)
        repair.patch.finalize_frontend_target(stage, frontend)
        (stage/RECEIPT).write_text(json.dumps({'version': profile['version'],
            'source_archives': profile['archives'], 'archives': preview.archive_hashes(stage),
            'catalog_sha256': catalogs}, indent=2))
        repair.patch.resign_app(stage)
        verify_stage(stage)
    finally:
        if desktop.exists():
            shutil.rmtree(desktop)
        repair.patch.cleanup_frontend_target(frontend)


def install(app: Path, stage: Path, home: Path, backups: Path) -> Path:
    verify_stage(stage)
    profile = json.loads(preview.PROFILE.read_text())
    preview.verify_identity(app, profile)
    if app.resolve() == stage.resolve():
        raise ValueError('Stage must differ from installed app')
    backups.mkdir(parents=True, exist_ok=True)
    backup = backups / ('Claude-official-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S') + '.app')
    if backup.exists():
        raise FileExistsError(backup)
    repair.quit_and_wait()
    preview.verify_identity(app, profile)
    originals = {}
    moved = installed = False
    try:
        os.replace(app, backup); moved = True
        os.replace(stage, app); installed = True
        originals = repair.update_locale(home, 'zh-CN')
        verify_stage(app)
    except BaseException:
        if originals:
            repair.restore_locale(originals)
        if installed:
            os.replace(app, backups/(backup.stem + '-failed.app'))
        if moved:
            os.replace(backup, app)
        raise
    return backup


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--app', type=Path, default=Path('/Applications/Claude.app'))
    cli.add_argument('--stage', type=Path, required=True)
    cli.add_argument('--review', type=Path)
    cli.add_argument('--install', action='store_true')
    cli.add_argument('--backup-dir', type=Path, default=repair.ROOT/'work/backups')
    args = cli.parse_args()
    if args.install:
        print('official_backup=' + str(install(args.app, args.stage, Path.home(), args.backup_dir)))
    else:
        if args.review is None:
            cli.error('--review is required when preparing a new candidate')
        build(args.app, args.stage, args.review)
        print('candidate_verified=' + str(args.stage))


if __name__ == '__main__':
    main()
