#!/usr/bin/env python3
"""Ad-hoc sign a complete, newly derived 5.x review candidate; never install it.

No signing identity/Keychain lookup: every signing invocation uses '-' and
--timestamp=none. Bounded subprocesses preserve timeout vs signature failure.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import struct
import time
import claude5_candidate as c
from upstream_patch_lib import (_collect_sign_targets,verify_electron_asar_integrity,
    read_asar_header,walk_asar_entries,_asar_integrity,align4,update_electron_asar_integrity)

def refresh_unpacked_integrity(archive):
    raw=archive.read_bytes();old_size,_,header=read_asar_header(raw,archive);changed=[]
    for name,item in walk_asar_entries(header):
        if not item.get('unpacked'):continue
        path=Path(str(archive)+'.unpacked')/name
        if path.is_symlink() or not path.is_file():raise ValueError('unsafe unpacked signing entry')
        integrity=_asar_integrity(path);size=path.stat().st_size
        if item.get('integrity')!=integrity or item['size']!=size:
            item['integrity']=integrity;item['size']=size;changed.append(name)
    if changed:
        encoded=json.dumps(header,separators=(',',':'),ensure_ascii=False).encode()
        payload=align4(4+len(encoded));header_size=4+payload
        archive.write_bytes(struct.pack('<IIII',4,header_size,payload,len(encoded))+
            encoded+b'\0'*(payload-4-len(encoded))+raw[8+old_size:])
    return changed

def local_entitlements(original):
    result=dict(original)
    for name in ('com.apple.application-identifier','com.apple.developer.team-identifier','keychain-access-groups'):
        result.pop(name,None)
    if original:
        result['com.apple.security.cs.disable-library-validation']=True
    return result

def sign(app):
    app=app.absolute()
    if ('..' in app.parts or not app.is_relative_to(c.BASE) or app==c.BASE/'Claude.app'
            or not app.relative_to(c.BASE).parts[0].startswith('implementation-')
            or app.name!='Claude.app'):
        raise ValueError('not an isolated derived candidate')
    if not os.path.ismount('/Volumes/Mars-私人服务器'):
        raise ValueError('external volume unmounted')
    if any(p.is_symlink() for p in (app,*app.parents)):
        raise ValueError('redirected signing target')
    receipt=c.load(app.parent/'engineering-receipt.json')
    if receipt['version']!=c.VERSION or receipt['status']!='FULL_RESOURCE_COMPAT_REVIEW_CANDIDATE_NOT_APPROVED_FOR_INSTALL':
        raise ValueError('only complete review candidates may be signed')
    for archive,digest in receipt['candidate_asar_sha256'].items():
        if archive not in c.ARCHIVES or c.sha((app/'Contents/Resources'/archive).read_bytes())!=digest:
            raise ValueError('candidate archive changed')
    if plistlib.loads((app/'Contents/Info.plist').read_bytes())['CFBundleShortVersionString']!=c.VERSION:
        raise ValueError('signing version changed')
    record_path=app.parent/'local-signature-record.json'
    ent_dir=app.parent/'signing-entitlements'
    if record_path.exists() or ent_dir.exists():
        raise ValueError('signing slot already occupied')
    ent_dir.mkdir()
    record={'version':c.VERSION,'identity':'ad-hoc (-), not Anthropic','installed':False,
            'launched':False,'gui_verified':False,'status':'in_progress','targets':[]}
    try:
        files,bundles=_collect_sign_targets(app)
        targets=sorted(files,key=lambda p:len(p.parts),reverse=True)+sorted(bundles,key=lambda p:len(p.parts),reverse=True)+[app]
        for i,target in enumerate(targets):
            # Sign native .unpacked files FIRST, then bind their new signed
            # bytes in ASAR headers. Updating Electron's digest invalidates its
            # old code signature; containing bundles are signed afterwards.
            if i==len(files):
                record['unpacked_metadata_refreshed']={}
                for archive in c.ARCHIVES:
                    path=app/'Contents/Resources'/archive
                    record['unpacked_metadata_refreshed'][archive]=refresh_unpacked_integrity(path)
                    update_electron_asar_integrity(app,'Resources/'+archive,path)
                record['candidate_asar_sha256']={a:c.sha((app/'Contents/Resources'/a).read_bytes()) for a in c.ARCHIVES}
            start=time.monotonic()
            metadata=subprocess.run(['/usr/bin/codesign','-d','--entitlements',':-',str(target)],
                capture_output=True,timeout=20,check=False)
            raw=metadata.stdout.rstrip(b'\0')
            if metadata.returncode and target==app:
                raise ValueError('cannot read main candidate entitlements')
            entitlements=local_entitlements(plistlib.loads(raw) if raw.strip() else {})
            args=['/usr/bin/codesign','--force','--sign','-','--timestamp=none',
                  '--options','runtime','--preserve-metadata=identifier,flags']
            if entitlements:
                path=ent_dir/f'{i:03d}.plist';path.write_bytes(plistlib.dumps(entitlements))
                args+=['--entitlements',str(path)]
            if target==app:
                args+=['-r','=designated => identifier "com.anthropic.claudefordesktop"']
            args.append(str(target))
            result=subprocess.run(args,capture_output=True,text=True,timeout=30,check=False)
            record['targets'].append({'path':str(target.relative_to(app)) if target!=app else '.',
                'exit':result.returncode,'elapsed_seconds':round(time.monotonic()-start,2),
                'stderr':result.stderr[-1500:]})
            print(f'signed target {i+1}/{len(targets)}: exit={result.returncode}',flush=True)
            if result.returncode:raise ValueError('local target signing failed')
        verify_electron_asar_integrity(app)
        for archive,digest in record['candidate_asar_sha256'].items():
            if c.sha((app/'Contents/Resources'/archive).read_bytes())!=digest:
                raise ValueError('ASAR changed during signing')
        start=time.monotonic()
        try:
            result=subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict','--verbose=2',str(app)],
                capture_output=True,text=True,timeout=45,check=False)
            record['verification']={'exit':result.returncode,'stdout':result.stdout,'stderr':result.stderr,
                'elapsed_seconds':round(time.monotonic()-start,2)}
            record['status']='local_signature_verified' if result.returncode==0 else 'local_signature_verification_failed'
        except subprocess.TimeoutExpired as error:
            record['status']='local_signature_verification_timeout'
            record['verification']={'timeout_seconds':45,'elapsed_seconds':round(time.monotonic()-start,2),
                'stdout':(error.stdout or b'').decode(errors='replace'),'stderr':(error.stderr or b'').decode(errors='replace')}
    except BaseException as error:
        record['status']='signing_failed';record['error_type']=type(error).__name__
        raise
    finally:
        record_path.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
    return record

if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__);cli.add_argument('--candidate',type=Path,required=True)
    result=sign(cli.parse_args().candidate);print(json.dumps(result,ensure_ascii=False))
    raise SystemExit(0 if result['status']=='local_signature_verified' else 1)
