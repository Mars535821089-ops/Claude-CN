#!/usr/bin/env python3
"""Build an explicitly noninstallable 5.x localization engineering sample.

No installer, no app launch, no user settings, no credentials. This deliberately
does NOT sign incomplete resources into something resembling a release.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import struct
import claude5_candidate as c
import claude5_compat as compat
from upstream_patch_lib import (extract_asar, build_asar, update_electron_asar_integrity,
                                verify_electron_asar_integrity, walk_asar_entries,
                                read_asar_header, read_asar_entry, asar_entry, align4)

def preserve_entry_metadata(source, target):
    """Keep official executable/other non-layout flags without changing data."""
    original=source.read_bytes();_,_,old=read_asar_header(original,source)
    raw=target.read_bytes();size,_,header=read_asar_header(raw,target)
    ignored={'offset','size','integrity','unpacked'}
    changed=False
    for name,item in walk_asar_entries(old):
        flags={k:v for k,v in item.items() if k not in ignored}
        current=asar_entry(header,name)
        if current is None:raise ValueError('official entry lost before metadata restore')
        for key,value in flags.items():
            if current.get(key)!=value:current[key]=value;changed=True
    if changed:
        encoded=json.dumps(header,separators=(',',':'),ensure_ascii=False).encode()
        payload=align4(4+len(encoded));target.write_bytes(struct.pack('<IIII',4,4+payload,payload,len(encoded))+
            encoded+b'\0'*(payload-4-len(encoded))+raw[8+size:])

def destination(stage):
    if not os.path.ismount('/Volumes/Mars-私人服务器'):
        raise ValueError('external volume unmounted')
    raw = stage.absolute()
    if '..' in raw.parts:
        raise ValueError('parent traversal in derived destination')
    if not raw.is_relative_to(c.BASE) or raw == c.BASE / 'Claude.app' or raw.name != 'Claude.app':
        raise ValueError('destination must be a new derived subdirectory')
    relative = raw.relative_to(c.BASE)
    if len(relative.parts) < 3 or not relative.parts[0].startswith('implementation-'):
        raise ValueError('destination overlaps official source or assessment')
    current = Path(raw.anchor)
    for part in raw.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ValueError('redirected destination')
    if raw.exists() or raw.parent.exists():
        raise ValueError('derived output slot already occupied')
    return raw

def check_coverage(partial_smoke):
    gaps = {g: c.load(c.CANDIDATE / f'{g}-missing.json') for g in c.PATHS}
    if not partial_smoke:
        c.require_complete(gaps)
    return {g: len(v) for g, v in gaps.items()}

def archive_entries(path):
    raw = path.read_bytes()
    size, _, header = read_asar_header(raw, path)
    return {name: (item.get('unpacked', False),
                   read_asar_entry(raw, size, header, name),
                   {k:v for k,v in item.items() if k not in {'offset','size','integrity','unpacked'}})
            for name, item in walk_asar_entries(header)}

def build(source, stage, *, partial_smoke=False):
    target = destination(stage)
    missing = check_coverage(partial_smoke)
    entry = c.read_source(source)
    manifest = c.load(c.CANDIDATE / 'manifest.json')
    if manifest['target_version'] != c.VERSION or manifest['source_archives'] != c.ARCHIVES:
        raise ValueError('candidate/source mismatch')
    for name, digest in {**manifest['generated_files_sha256'], **manifest['authored_inputs_sha256']}.items():
        if '/' in name or '\\' in name or c.sha((c.CANDIDATE / name).read_bytes()) != digest:
            raise ValueError('resource changed since audit: ' + name)
    # Coverage declarations cannot be forged by clearing a missing.json file.
    catalogs = {}
    checks = {}
    for group, (archive, path) in c.PATHS.items():
        english = json.loads(entry(archive, path))
        chinese = c.load(c.CANDIDATE / f'{group}-zh-CN.json')
        gaps = c.load(c.CANDIDATE / f'{group}-missing.json')
        actual_gaps = {k: v for k, v in english.items() if k not in chinese}
        if (not chinese.keys() <= english.keys() or actual_gaps != gaps
                or len(gaps) != manifest['catalogs'][group]['missing']):
            raise ValueError('catalog coverage declaration differs from real source')
        catalogs[group] = (c.CANDIDATE / f'{group}-zh-CN.json').read_bytes()
        checks[group] = {'en': {k: english[k] for k in chinese}, 'zh': chinese,
                         'strictSelections': True}
    validation = subprocess.run(['node', str(c.ROOT / 'scripts/audit_messages.cjs')],
        input=json.dumps({'catalogs': checks}), text=True, capture_output=True, check=True, timeout=180)
    if json.loads(validation.stdout)['errors']:
        raise ValueError('invalid packaged catalog')
    ui = c.load(c.CANDIDATE / 'hardcoded-ui-reviewed.json')['files']
    translations = {**c.load(c.ROOT / 'resources/ui-reviewed-zh-CN.json'),
                    **c.load(c.ROOT / 'resources/candidates/claude-desktop-4.23118.1/hardcoded-ui-zh-CN.json')}
    payload = [{'file': name, 'source': entry(*name.split('/', 1)).decode(),
                'spec': spec, 'translations': translations} for name, spec in ui.items()]
    checked = subprocess.run(['node', str(c.ROOT / 'scripts/ui_localization.cjs'), '--patch-inline'],
        input=json.dumps(payload), text=True, capture_output=True, check=True, timeout=90)
    patched = json.loads(checked.stdout)
    if len(patched) != len(payload):
        raise ValueError('UI count mismatch')
    compat_edits = compat.apply(entry, c.CANDIDATE)
    # All reviewed JS replacements are preflighted before copying anything.
    before = {a: archive_entries(source / 'Contents/Resources' / a) for a in c.ARCHIVES}
    fallback = source / 'Contents/Resources/ion-dist/index.html'
    fallback_sha = c.sha(fallback.read_bytes())
    target.parent.mkdir(parents=True, exist_ok=False)
    snapshot=(c.CANDIDATE/'manifest.json').read_bytes()
    (target.parent/'source-resource-manifest.json').write_bytes(snapshot)
    subprocess.run(['/bin/cp', '-cR', str(source), str(target)], check=True, timeout=210)
    c.read_source(target)
    work = target.parent / 'asar-work'
    roots, unpacked = {}, {}
    for archive in c.ARCHIVES:
        roots[archive] = work / archive
        unpacked[archive] = set()
        extract_asar(target / 'Contents/Resources' / archive, roots[archive], unpacked[archive])
    gates = c.load(c.CANDIDATE / 'language-whitelist-reviewed.json')['files']
    gate_edits = c.prepare_gates(roots['ion-dist.asar'], gates)
    allowed = {a: set() for a in c.ARCHIVES}
    for item, text in zip(payload, patched, strict=True):
        archive, name = item['file'].split('/', 1)
        (roots[archive] / name).write_text(text)
        allowed[archive].add(name)
    c.apply_prepared(gate_edits)
    allowed['ion-dist.asar'].update(gates)
    for (archive, name), data in compat_edits.items():
        if name in allowed[archive]:
            raise ValueError('overlapping compatibility edits')
        path = roots[archive] / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        allowed[archive].add(name)
    for group, (archive, path) in c.PATHS.items():
        raw = catalogs[group]
        for alias in ('zh-CN','zh-Hans'):
            localized = path.replace('en-US',alias)
            out = roots[archive] / localized
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(raw)
            allowed[archive].add(localized)
    menu = (c.ROOT / 'resources/Localizable.strings').read_bytes()
    if c.sha(menu) != 'fa28e36f934e96df886265b2ad046af1193aba0798fd16e657b2175c00321385':
        raise ValueError('reviewed macOS menu resource changed')
    for locale in ('zh-CN','zh_CN','zh-Hans'):
        folder = target / 'Contents/Resources' / (locale+'.lproj')
        if folder.is_symlink() or (folder/'Localizable.strings').is_symlink():
            raise ValueError('redirected native menu resource')
        folder.mkdir(exist_ok=True)
        (folder / 'Localizable.strings').write_bytes(menu)
    changed = {}
    for archive in c.ARCHIVES:
        path = target / 'Contents/Resources' / archive
        build_asar(roots[archive], path, unpacked[archive])
        preserve_entry_metadata(source/'Contents/Resources'/archive,path)
        update_electron_asar_integrity(target, 'Resources/' + archive, path)
        after = archive_entries(path)
        if not before[archive].keys() <= after.keys():
            raise ValueError('vendor entries lost')
        changed[archive] = sorted(k for k in after if after[k] != before[archive].get(k))
        if set(changed[archive]) != allowed[archive]:
            raise ValueError('archive changes exceeded intended scope')
        if {k for k,v in after.items() if v[0]} != unpacked[archive]:
            raise ValueError('unpacked flags changed')
    if c.sha((target / 'Contents/Resources/ion-dist/index.html').read_bytes()) != fallback_sha:
        raise ValueError('loose fallback changed')
    verify_electron_asar_integrity(target)
    # Recheck packaged catalog bytes, not just the source resource files.
    for archive in c.ARCHIVES:
        after = archive_entries(target / 'Contents/Resources' / archive)
        for (owner, name), data in compat_edits.items():
            if owner == archive and after[name][1] != data:
                raise ValueError('packaged compatibility module differs')
        for group, (owner, path) in c.PATHS.items():
            for alias in ('zh-CN','zh-Hans'):
                if owner == archive and after[path.replace('en-US',alias)][1] != catalogs[group]:
                    raise ValueError('packaged locale differs')
    for locale in ('zh-CN','zh_CN','zh-Hans'):
        if (target/'Contents/Resources'/f'{locale}.lproj/Localizable.strings').read_bytes()!=menu:
            raise ValueError('packaged native menu differs')
    c.read_source(source)  # Official source must still be byte-identical.
    receipt = {'version': c.VERSION, 'status': ('INCOMPLETE_NONINSTALLABLE_ENGINEERING_SAMPLE' if any(missing.values()) else
        'FULL_RESOURCE_COMPAT_REVIEW_CANDIDATE_NOT_APPROVED_FOR_INSTALL'),
        'installed': False, 'launched': False, 'visual_ui_verified': False,
        'missing_translations': missing, 'ui_replacements': sum(
            r['expected'] for spec in ui.values() for r in spec['rules']),
        'locale_gates': len(gates), 'changed_entries': changed,
        'gated_messages': len(c.load(c.CANDIDATE / 'gated-zh-CN.json')),
        'locale_aliases':['zh-CN','zh-Hans'], 'native_menu_sha256':c.sha(menu),
        'compatibility_patches': c.load(c.CANDIDATE / 'compatibility-reviewed.json'),
        'source_asar_sha256': c.ARCHIVES,
        'resource_manifest_sha256': c.sha(snapshot),
        'candidate_asar_sha256': {a: c.sha((target / 'Contents/Resources' / a).read_bytes()) for a in c.ARCHIVES},
        'loose_fallback_sha256': fallback_sha,
        'signature': 'INVALIDATED_BY_RESOURCE_EDIT_NOT_RESIGNED_NOT_A_RELEASE',
        'source_signature': 'main-agent initial Anthropic strict verification passed; later bounded timeout preserved in assessment; not rerun',
        'cowork_hook': 'optional_session_allowlist_policy_unconfigured_native_fallback_preserved',
        'sidebar': 'both_framework_lifecycle_source_patch_GUI_not_accepted'}
    (target.parent / 'engineering-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    shutil.rmtree(work)
    return receipt

if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--official-app', type=Path, default=c.BASE / 'Claude.app')
    cli.add_argument('--stage', type=Path, required=True)
    cli.add_argument('--partial-smoke', action='store_true', help='explicitly allow NONINSTALLABLE incomplete engineering output')
    args = cli.parse_args()
    print(json.dumps(build(args.official_app, args.stage, partial_smoke=args.partial_smoke)))
