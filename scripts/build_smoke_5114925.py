#!/usr/bin/env python3
"""Preflight a strictly scoped, never-installable Claude 5.11492.5 app copy.

The default --plan-only path is read-only. A separate --stage operation may
later copy the official app to a fresh external build slot; neither path
starts, replaces, signs, or installs /Applications/Claude.app.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import assemble_5114925_catalogs as catalogs
import claude5_candidate as old
import review_claude_5114925 as current
import review_hardcoded_5114925 as hardcoded
import review_sidebar_5114925 as sidebar
import review_cowork_5114925 as cowork
import review_fallback_5114925 as fallback
from upstream_patch_lib import (read_asar_entry, read_asar_header, LANG_LIST_RE,
                                BASE_LANGUAGE_LIST, extract_asar, build_asar,
                                update_electron_asar_integrity,
                                verify_electron_asar_integrity)
from build_smoke_51231 import archive_entries, preserve_entry_metadata

ROOT = Path(__file__).resolve().parents[1]
APP = Path('/Applications/Claude.app')
EXTERNAL = Path('/Volumes/Mars-私人服务器/内置盘写入统一管理/01-编译与构建/Claude-CN')
MENU_SHA = 'fa28e36f934e96df886265b2ad046af1193aba0798fd16e657b2175c00321385'
ENGLISH_MENU_SHA = 'c94f4aeb68d5a17328988345ddcb7194d08f78a2f998f9398a9ceef8a3772e3a'


def external_mounted() -> bool:
    return Path('/Volumes/Mars-私人服务器').is_mount()


def destination(stage: Path) -> Path:
    target=stage.absolute()
    if (not external_mounted() or '..' in target.parts
            or target.name!='Claude.app' or not target.is_relative_to(EXTERNAL)
            or len(target.relative_to(EXTERNAL).parts)!=2
            or not target.parent.name.startswith('implementation-')
            or target.parent.exists() or target.parent.is_symlink()):
        raise ValueError('isolated app stage requires a fresh external implementation slot')
    current=EXTERNAL
    if current.is_symlink():
        raise ValueError('isolated app stage path is redirected')
    for part in target.relative_to(EXTERNAL).parts:
        current /= part
        if current.is_symlink():
            raise ValueError('isolated app stage path is redirected')
    return target


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def register_edit(edits: dict[tuple[str, str], bytes], key: tuple[str, str],
                  source: bytes | None, updated: bytes,
                  expected_source_sha: str | None) -> None:
    archive, relative = key
    if (archive not in {'app.asar', 'ion-dist.asar'}
            or not relative or any(x in ('', '.', '..') for x in relative.split('/'))
            or '\\' in relative or key in edits):
        raise ValueError('archive edit scope invalid or overlapping')
    if (not isinstance(updated, bytes) or not updated
            or source == updated
            or (source is None and expected_source_sha is not None)
            or (source is not None and digest(source) != expected_source_sha)):
        raise ValueError('archive edit source fingerprint or output invalid')
    edits[key] = updated


def _node_patch(module: str, source: bytes) -> bytes:
    program = '''const fs=require('fs'),m=require(process.argv[1]);
const text=fs.readFileSync(0,'utf8');
process.stdout.write(Buffer.from(m.patchPinned(text),'utf8').toString('base64'));'''
    proc = subprocess.run(['node', '-e', program, str(ROOT / 'scripts' / module)],
                          input=source.decode(), capture_output=True, text=True,
                          check=False, timeout=110)
    if proc.returncode:
        raise ValueError('source-bound current behavior patch refused')
    return base64.b64decode(proc.stdout, validate=True)


def _gated(source: bytes, translated: dict[str, str]) -> bytes:
    proc = subprocess.run(['node', str(ROOT / 'scripts/claude5_compat.cjs')],
        input=json.dumps({'action':'patch-gated','source':source.decode(),
                          'chinese':translated},ensure_ascii=False),
        capture_output=True,text=True,check=False,timeout=100)
    if proc.returncode:raise ValueError('signed gated dictionary refused exact Chinese')
    return json.loads(proc.stdout).encode()


def prepare_plan(app: Path, catalog_dir: Path) -> tuple[dict, dict[tuple[str, str], bytes], bytes]:
    if app != APP or not Path('/Volumes/Mars-私人服务器').is_mount():
        raise ValueError('current official app or external build volume unavailable')
    reports = [current.review(app)[0],hardcoded.review(app),
               sidebar.review(app)[1],cowork.review(app),fallback.review(app)]
    source_archives = reports[0]['source_archives_sha256']
    if any(report['source_archives_sha256'] != source_archives or report['installed']
           for report in reports):
        raise ValueError('independent source reviews disagree or claim installation')
    for report,receipt in zip(reports[1:],(
        hardcoded.TARGET,sidebar.CANDIDATE/'sidebar-reviewed.json',cowork.TARGET,fallback.TARGET),strict=True):
        if report != json.loads(receipt.read_text()):
            raise ValueError('source-bound review receipt changed')
    manifest_path = catalog_dir / 'manifest.json'
    if catalog_dir.is_symlink() or not manifest_path.is_file():
        raise ValueError('current locale assembly unavailable')
    manifest = json.loads(manifest_path.read_text())
    if (manifest.get('version') != current.VERSION or manifest.get('status') != 'candidate_not_installable'
            or manifest.get('installed') is not False
            or manifest.get('source_archives_sha256') != source_archives):
        raise ValueError('locale assembly provenance differs')
    raw_archives = {}
    for archive in ('app.asar','ion-dist.asar'):
        path = app / 'Contents/Resources' / archive
        raw = path.read_bytes()
        if digest(raw) != source_archives['Contents/Resources/'+archive]:
            raise ValueError('current signed archive changed after review')
        size, _, header = read_asar_header(raw, path)
        raw_archives[archive] = (raw,size,header)

    def get(archive: str, name: str) -> bytes | None:
        return read_asar_entry(*raw_archives[archive],name)

    edits: dict[tuple[str,str],bytes] = {}
    for group,(archive,path) in old.PATHS.items():
        english = json.loads(get(archive,path))
        generated = catalog_dir / f'{group}-zh-CN.json'
        data = generated.read_bytes()
        spec = manifest['groups'][group]
        if digest(data) != spec['sha256'] or len(json.loads(data)) != spec['ids']:
            raise ValueError('locale bytes differ from assembled manifest')
        catalogs._validate_catalog(group, english, json.loads(data))
        for locale in ('zh-CN','zh-Hans'):
            localized = path.replace('en-US',locale)
            original = get(archive,localized)
            register_edit(edits,(archive,localized),original,data,
                          digest(original) if original is not None else None)

    translations={**json.loads((ROOT/'resources/ui-reviewed-zh-CN.json').read_text()),
        **json.loads((ROOT/'resources/candidates/claude-desktop-4.23118.1/hardcoded-ui-zh-CN.json').read_text())}
    for filename,spec in reports[1]['files'].items():
        archive,name=filename.split('/',1)
        source=get(archive,name)
        if source is None or digest(source)!=spec['sha256']:
            raise ValueError('current hardcoded UI source changed')
        updated=hardcoded.patch_with_original_rules(filename,source,spec['rules'],translations)
        if digest(updated)!=spec['patched_sha256']:
            raise ValueError('current hardcoded UI output changed')
        register_edit(edits,(archive,name),source,updated,spec['sha256'])

    gates=json.loads((current.DESTINATION/'language-whitelist-review.json').read_text())
    if (gates.get('version')!=current.VERSION or len(gates.get('files',{}))!=3):
        raise ValueError('current locale whitelist review unavailable')
    for name,spec in gates['files'].items():
        original=get('ion-dist.asar',name)
        if original is None or digest(original)!=spec['sha256']:
            raise ValueError('new-version language gate source changed')
        patched,count=LANG_LIST_RE.subn(BASE_LANGUAGE_LIST+',"zh-CN","zh-Hans"]',original.decode())
        updated=patched.encode()
        if count!=spec['expected'] or digest(updated)!=spec['patched_sha256']:
            raise ValueError('new-version language gate output changed')
        register_edit(edits,('ion-dist.asar',name),original,updated,spec['sha256'])

    gated_path='.vite/build/index.chunk-UbmIijBk.js'
    source=get('app.asar',gated_path)
    old_entry=old.read_source(old.BASE/'Claude.app')
    previous=old_entry('app.asar','.vite/build/index.chunk-D3ouEfeo.js')
    def english_gated(raw: bytes):
        proc=subprocess.run(['node',str(ROOT/'scripts/claude5_compat.cjs')],
            input=json.dumps({'action':'gated','source':raw.decode()}),
            capture_output=True,text=True,timeout=90,check=False)
        if proc.returncode:raise ValueError('gated English parser failed')
        return json.loads(proc.stdout)
    if english_gated(source)!=english_gated(previous):
        raise ValueError('gated current English differs from reviewed previous source')
    chinese=json.loads((old.CANDIDATE/'gated-zh-CN.json').read_text())
    if set(chinese)!=set(english_gated(source)):
        raise ValueError('gated current translations incomplete')
    updated=_gated(source,chinese)
    if digest(source)!='97b22fb6d97384ae19ea41352346c332b02bd86b280655cd63ab5f8d670567e5' or digest(updated)!='41dbabe9114e33eaba705015d6c0340893726fbbf35894ca3074ab5aa7b56c8d':
        raise ValueError('current gated source or patch changed')
    register_edit(edits,('app.asar',gated_path),source,updated,digest(source))

    for archive,name,module,spec in [
        ('ion-dist.asar',sidebar.RESOURCE,'sidebar_5114925.cjs',reports[2]),
        ('app.asar',cowork.WORKER,'cowork_5114925.cjs',reports[3]),
        ('ion-dist.asar',fallback.RESOURCE,'fallback_5114925.cjs',reports[4]),
    ]:
        original=get(archive,name)
        patched=_node_patch(module,original)
        expected = (spec['worker']['patched_sha256']
                    if module == 'cowork_5114925.cjs' else spec['patched_sha256'])
        if digest(patched) != expected:
            raise ValueError('current behavior patch differs from review')
        register_edit(edits,(archive,name),original,patched,digest(original))
    policy=ROOT/'scripts/cowork_compaction_policy.cjs'
    if digest(policy.read_bytes())!=reports[3]['policy_module_sha256']:
        raise ValueError('Cowork policy module changed')
    policy_path='.vite/build/claude-cn-compaction-policy.cjs'
    if get('app.asar',policy_path) is not None:
        raise ValueError('current official app already has a policy module')
    register_edit(edits,('app.asar',policy_path),None,policy.read_bytes(),None)

    if len(edits)!=20:
        raise ValueError('current-version intended ASAR edit count changed')
    english_menu=app/'Contents/Resources/en.lproj/Localizable.strings'
    menu=ROOT/'resources/Localizable.strings'
    if digest(english_menu.read_bytes())!=ENGLISH_MENU_SHA or digest(menu.read_bytes())!=MENU_SHA:
        raise ValueError('reviewed native menu source or Chinese translation changed')
    if current.update_guard.bundle_identity(app)['archives']!=source_archives:
        raise ValueError('current signed app changed during build preflight')
    summary={'version':current.VERSION,'status':'plan_only_not_installable',
             'source_archives_sha256':source_archives,'edit_count':len(edits),
             'edits':{f'{archive}/{name}':{'old_sha256':digest(get(archive,name)) if get(archive,name) is not None else None,
                                         'new_sha256':digest(updated)}
                      for (archive,name),updated in sorted(edits.items())},
             'native_menu_sha256':MENU_SHA,'installed':False,'signed':False,
             'visual_ui_verified':False}
    return summary,edits,menu.read_bytes()


def build_stage(app: Path, stage: Path, catalog_dir: Path) -> dict:
    target=destination(stage)
    plan,edits,menu=prepare_plan(app,catalog_dir)
    target.parent.mkdir(mode=0o755)
    receipt=target.parent/'engineering-receipt.json'
    state={'version':current.VERSION,'status':'copy_not_verified',
           'installed':False,'launched':False,'signed':False,'visual_ui_verified':False,
           'source_archives_sha256':plan['source_archives_sha256'],
           'intended_edits':plan['edits'],'stage':str(target)}

    def journal():
        receipt.write_text(json.dumps(state,ensure_ascii=False,sort_keys=True,indent=2)+'\n')

    journal()
    try:
        subprocess.run(['/bin/cp','-pR',str(app),str(target)],check=True,timeout=330,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for archive in ('app.asar','ion-dist.asar'):
            if digest((target/'Contents/Resources'/archive).read_bytes())!=plan['source_archives_sha256']['Contents/Resources/'+archive]:
                raise ValueError('copied official archive differs before patching')
        if current.update_guard.bundle_identity(app)['archives']!=plan['source_archives_sha256']:
            raise ValueError('official app changed while copy was made')
        state['status']='copy_verified_repacking'
        journal()
        before={archive:archive_entries(app/'Contents/Resources'/archive)
                for archive in ('app.asar','ion-dist.asar')}
        work=target.parent/'asar-work'
        roots={};unpacked={};changed={}
        for archive in ('app.asar','ion-dist.asar'):
            roots[archive]=work/archive
            unpacked[archive]=set()
            extract_asar(target/'Contents/Resources'/archive,roots[archive],unpacked[archive])
        for (archive,name),data in edits.items():
            path=roots[archive]/name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(data)
        for locale in ('zh-CN','zh_CN','zh-Hans'):
            folder=target/'Contents/Resources'/f'{locale}.lproj'
            if folder.is_symlink() or (folder/'Localizable.strings').is_symlink():
                raise ValueError('native menu stage path redirected')
            folder.mkdir(exist_ok=True)
            (folder/'Localizable.strings').write_bytes(menu)
        for archive in ('app.asar','ion-dist.asar'):
            dest=target/'Contents/Resources'/archive
            build_asar(roots[archive],dest,unpacked[archive])
            preserve_entry_metadata(app/'Contents/Resources'/archive,dest)
            update_electron_asar_integrity(target,'Resources/'+archive,dest)
            after=archive_entries(dest)
            if not before[archive].keys()<=after.keys():
                raise ValueError('official resource entries disappeared')
            changed[archive]=sorted(name for name,record in after.items()
                                    if record!=before[archive].get(name))
            expected={name for owner,name in edits if owner==archive}
            if set(changed[archive])!=expected:
                raise ValueError('repacked archive changed entries outside approved scope')
            if {name for name,record in after.items() if record[0]}!=unpacked[archive]:
                raise ValueError('official unpacked resource layout changed')
            for name in expected:
                if after[name][1]!=edits[(archive,name)]:
                    raise ValueError('repacked resource bytes differ from reviewed plan')
        verify_electron_asar_integrity(target)
        for locale in ('zh-CN','zh_CN','zh-Hans'):
            if (target/'Contents/Resources'/f'{locale}.lproj/Localizable.strings').read_bytes()!=menu:
                raise ValueError('repacked native menu differs')
        if current.update_guard.bundle_identity(app)['archives']!=plan['source_archives_sha256']:
            raise ValueError('official app changed during isolated repack')
        state.update(status='NONINSTALLABLE_UNSIGNED_CURRENT_CANDIDATE',
                     changed_entries=changed,
                     candidate_asar_sha256={archive:digest((target/'Contents/Resources'/archive).read_bytes())
                        for archive in ('app.asar','ion-dist.asar')},
                     signature='INVALIDATED_BY_RESOURCE_EDIT_NOT_RESIGNED_NOT_A_RELEASE')
        journal()
        return state
    except BaseException as error:
        state.update(status='FAILED_NONINSTALLABLE_ISOLATED_STAGE',
                     error_type=type(error).__name__)
        journal()
        raise


if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--catalog-dir',type=Path,required=True)
    actions=cli.add_mutually_exclusive_group(required=True)
    actions.add_argument('--plan-only',action='store_true')
    actions.add_argument('--stage',type=Path)
    args=cli.parse_args()
    if args.stage:
        summary=build_stage(APP,args.stage.absolute(),args.catalog_dir.absolute())
        print(json.dumps({'version':summary['version'],'status':summary['status'],
                          'changed_entries':{k:len(v) for k,v in summary['changed_entries'].items()},
                          'installed':False,'signed':False}))
        raise SystemExit(0)
    summary,_,_=prepare_plan(APP,args.catalog_dir.absolute())
    print(json.dumps({'version':summary['version'],'status':summary['status'],
                      'edit_count':summary['edit_count'],'installed':False,'signed':False}))
