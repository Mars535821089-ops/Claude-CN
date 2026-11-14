#!/usr/bin/env python3
"""Ad-hoc sign ONLY a fresh isolated sample, never an installable Claude.

The official app's Team ID and Keychain access groups cannot be retained by
this local identity. Success proves code integrity, NOT login continuity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import time

from sign_review_51231 import (local_entitlements, reviewed_sign_targets,
                               refresh_unpacked_integrity)
from upstream_patch_lib import update_electron_asar_integrity, verify_electron_asar_integrity

EXTERNAL=Path('/Volumes/Mars-私人服务器/内置盘写入统一管理/01-编译与构建/Claude-CN')
VERSION='5.11492.5'


def external_mounted() -> bool:
    return Path('/Volumes/Mars-私人服务器').is_mount()


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def local_signing_impact(original: dict) -> dict:
    groups=original.get('keychain-access-groups',[])
    if not isinstance(groups,list):
        raise ValueError('original Keychain groups are not a list')
    return {'original_team_entitlement_present':bool(original.get('com.apple.developer.team-identifier')),
            'lost_keychain_group_count':len(groups),
            'credential_continuity_proven':False,
            'local_entitlements':local_entitlements(original)}


def preflight(app: Path) -> dict:
    app=app.absolute()
    if (not external_mounted() or '..' in app.parts
            or app.name!='Claude.app' or not app.is_relative_to(EXTERNAL)
            or len(app.relative_to(EXTERNAL).parts)!=2
            or not app.parent.name.startswith('implementation-')
            or any(p.is_symlink() for p in (app,app.parent,EXTERNAL))):
        raise ValueError('signing target is not a verified external sample')
    receipt_path=app.parent/'engineering-receipt.json'
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise ValueError('isolated engineering receipt unavailable')
    receipt=json.loads(receipt_path.read_text())
    if (receipt.get('version')!=VERSION
            or receipt.get('status')!='NONINSTALLABLE_UNSIGNED_CURRENT_CANDIDATE'
            or receipt.get('installed') is not False or receipt.get('signed') is not False
            or set(receipt.get('candidate_asar_sha256',{}))!={'app.asar','ion-dist.asar'}):
        raise ValueError('only a complete unsigned sample can be signed')
    info=plistlib.loads((app/'Contents/Info.plist').read_bytes())
    if (info.get('CFBundleShortVersionString')!=VERSION
            or info.get('CFBundleIdentifier')!='com.anthropic.claudefordesktop'):
        raise ValueError('candidate identity changed')
    for archive,digest in receipt['candidate_asar_sha256'].items():
        file=app/'Contents/Resources'/archive
        if file.is_symlink() or sha(file.read_bytes())!=digest:
            raise ValueError('candidate archive changed since isolated build')
    return receipt


def entitlements(path: Path) -> dict:
    result=subprocess.run(['/usr/bin/codesign','-d','--entitlements',':-',str(path)],
                          capture_output=True,timeout=20,check=False)
    if result.returncode:
        raise ValueError('signed entitlement source unavailable')
    raw=result.stdout.rstrip(b'\0')
    return plistlib.loads(raw) if raw.strip() else {}


def sign(app: Path) -> dict:
    app=app.absolute()
    baseline=preflight(app)
    source_app=Path('/Applications/Claude.app')
    original_entitlements=entitlements(source_app)
    impact=local_signing_impact(original_entitlements)
    if (not impact['original_team_entitlement_present']
            or impact['lost_keychain_group_count']<1):
        raise ValueError('official team/Keychain entitlement contract changed')
    record_path=app.parent/'local-signature-record.json'
    ent_dir=app.parent/'signing-entitlements'
    if record_path.exists() or record_path.is_symlink() or ent_dir.exists() or ent_dir.is_symlink():
        raise ValueError('isolated signing generation already exists')
    ent_dir.mkdir(mode=0o700)
    record={'version':VERSION,'status':'SIGNING_IN_PROGRESS_NONINSTALLABLE',
            'installed':False,'launched':False,'gui_verified':False,
            'credential_continuity_proven':False,
            'lost_keychain_group_count':impact['lost_keychain_group_count'],
            'official_team_entitlement_present':True,'signature_identity':'ad-hoc (-), not Anthropic',
            'input_receipt_sha256':sha((app.parent/'engineering-receipt.json').read_bytes()),
            'targets':[]}
    try:
        files,bundles,omitted=reviewed_sign_targets(app)
        targets=sorted(files,key=lambda p:len(p.parts),reverse=True)+\
            sorted(bundles,key=lambda p:len(p.parts),reverse=True)+[app]
        record['target_plan']={'native_files':len(files),'bundles':len(bundles),
                               'outer':1,'omitted_duplicate_framework_main':omitted}
        for index,target in enumerate(targets):
            if index==len(files):
                record['unpacked_metadata_refreshed']={}
                for archive in ('app.asar','ion-dist.asar'):
                    path=app/'Contents/Resources'/archive
                    record['unpacked_metadata_refreshed'][archive]=refresh_unpacked_integrity(path)
                    update_electron_asar_integrity(app,'Resources/'+archive,path)
                record['post_unpacked_asar_sha256']={name:sha((app/'Contents/Resources'/name).read_bytes())
                    for name in ('app.asar','ion-dist.asar')}
            before=time.monotonic()
            try:original=entitlements(target)
            except ValueError:
                if target==app:raise
                original={}
            allowed=local_entitlements(original)
            args=['/usr/bin/codesign','--force','--sign','-','--timestamp=none',
                  '--options','runtime','--preserve-metadata=identifier,flags']
            if allowed:
                ent=ent_dir/f'{index:03d}.plist';ent.write_bytes(plistlib.dumps(allowed))
                args+=['--entitlements',str(ent)]
            if target==app:
                args+=['-r','=designated => identifier "com.anthropic.claudefordesktop"']
            args.append(str(target))
            done=subprocess.run(args,capture_output=True,timeout=40,check=False)
            record['targets'].append({'path':str(target.relative_to(app)) if target!=app else '.',
                'exit':done.returncode,'elapsed_seconds':round(time.monotonic()-before,2)})
            print(f'signed isolated target {index+1}/{len(targets)} exit={done.returncode}',flush=True)
            if done.returncode:raise ValueError('isolated signature target failed')
        verify_electron_asar_integrity(app)
        result=subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(app)],
                              capture_output=True,timeout=90,check=False)
        record['verification_exit']=result.returncode
        if result.returncode:raise ValueError('locally signed candidate is not deep/strict valid')
        final_entitlements=entitlements(app)
        if any(k in final_entitlements for k in ('com.apple.application-identifier',
                        'com.apple.developer.team-identifier','keychain-access-groups')):
            raise ValueError('local signature unexpectedly advertises official access')
        if sha((source_app/'Contents/Resources/app.asar').read_bytes())!=baseline['source_archives_sha256']['Contents/Resources/app.asar']:
            raise ValueError('official source changed during isolated signing')
        record['status']='LOCALLY_SIGNED_NOT_INSTALLABLE_CREDENTIAL_ACCESS_UNPROVEN'
        return record
    except BaseException as error:
        record['status']='LOCAL_SIGNING_FAILED_NOT_INSTALLABLE'
        record['error_type']=type(error).__name__
        raise
    finally:
        record_path.write_text(json.dumps(record,ensure_ascii=False,sort_keys=True,indent=2)+'\n')


if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--candidate',type=Path,required=True)
    result=sign(cli.parse_args().candidate)
    print(json.dumps({'status':result['status'],'verification_exit':result['verification_exit'],
                      'lost_keychain_group_count':result['lost_keychain_group_count'],
                      'credential_continuity_proven':False,'installed':False}))
