#!/usr/bin/env python3
"""Independent read-only packaged 5.x audit. Does not launch or install Claude."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import plistlib
import subprocess
import claude5_candidate as c
from upstream_patch_lib import read_asar_header,read_asar_entry,walk_asar_entries,asar_entry,verify_electron_asar_integrity,_asar_integrity

def signature_evidence(app,receipt):
    current=app.parent/'candidate-validation-record.json'
    if current.exists() or current.is_symlink():
        from candidate_validation_512312 import read_current
        validated=read_current(app)
        return {'status':'local_signature_verified','candidate_asar_sha256':validated['candidate_asar_sha256'],
                'evidence_kind':validated['kind'],'candidate_validation_status':validated['status']}
    legacy=app.parent/'local-signature-record.json'
    if legacy.exists() or legacy.is_symlink():
        if legacy.is_symlink():raise ValueError('redirected signature record')
        signed=c.load(legacy)
        if (signed.get('status')!='local_signature_verified' or 'error_type' in signed
                or signed.get('verification',{}).get('exit')!=0
                or not signed.get('targets') or signed['targets'][-1].get('path')!='.'
                or any(type(t.get('exit')) is not int or t['exit']!=0 for t in signed['targets'])):
            raise ValueError('failed/incomplete legacy signing record cannot validate current candidate')
        return {**signed,'evidence_kind':'legacy-complete-signing-record','candidate_validation_status':'absent'}
    # Resource-only preparation can run before publishing current validation;
    # absence of a signature receipt is never reported as readiness acceptance.
    return {'status':'not_signed','candidate_asar_sha256':receipt['candidate_asar_sha256'],
            'evidence_kind':'engineering-resources-only','candidate_validation_status':'absent'}

def audit(app):
    receipt=c.load(app.parent/'engineering-receipt.json')
    if receipt['status']!='FULL_RESOURCE_COMPAT_REVIEW_CANDIDATE_NOT_APPROVED_FOR_INSTALL' or any(receipt['missing_translations'].values()):
        raise ValueError('not a complete review candidate')
    metadata=plistlib.loads((app/'Contents/Info.plist').read_bytes())
    if metadata['CFBundleShortVersionString']!=c.VERSION or metadata['CFBundleIdentifier']!='com.anthropic.claudefordesktop':
        raise ValueError('wrong packaged identity')
    profile_path=app.parent/'source-resource-manifest.json'
    profile_raw=(profile_path if profile_path.exists() else c.CANDIDATE/'manifest.json').read_bytes()
    if c.sha(profile_raw)!=receipt['resource_manifest_sha256']:
        raise ValueError('build resource manifest changed')
    profile=json.loads(profile_raw)
    signed=signature_evidence(app,receipt)
    hashes=signed.get('candidate_asar_sha256',receipt['candidate_asar_sha256'])
    store={}
    for archive,digest in hashes.items():
        path=app/'Contents/Resources'/archive;raw=path.read_bytes()
        if c.sha(raw)!=digest:raise ValueError('packaged archive changed')
        size,_,header=read_asar_header(raw,path);store[archive]=(raw,size,header)
        for name,item in walk_asar_entries(header):
            if not item.get('unpacked'):continue
            native=Path(str(path)+'.unpacked')/name
            if native.is_symlink() or not native.is_file() or item['size']!=native.stat().st_size or item['integrity']!=_asar_integrity(native):
                raise ValueError('signed unpacked entry integrity differs: '+name)
        source_path=c.BASE/'Claude.app/Contents/Resources'/archive
        _,_,old=read_asar_header(source_path.read_bytes(),source_path)
        for name,item in walk_asar_entries(old):
            packaged=asar_entry(header,name)
            if packaged is None:raise ValueError('official entry lost')
            flags=lambda x:{k:v for k,v in x.items() if k not in {'offset','size','integrity'}}
            if flags(item)!=flags(packaged):raise ValueError('official entry flags changed: '+name)
    def entry(archive,name):
        raw,size,header=store[archive];data=read_asar_entry(raw,size,header,name)
        if data is None:raise ValueError('packaged entry missing: '+name)
        return data
    checks={};scripts={}
    for group,(archive,path) in c.PATHS.items():
        english=json.loads(entry(archive,path))
        for alias in ('zh-CN','zh-Hans'):
            raw=entry(archive,path.replace('en-US',alias));chinese=json.loads(raw)
            if chinese.keys()!=english.keys() or c.sha(raw)!=profile['generated_files_sha256'][group+'-zh-CN.json']:
                raise ValueError('catalog/alias hash or full key coverage differs')
        checks[group]={'en':english,'zh':chinese,'strictSelections':True}
    # The embedded publisher and policy module are checked as packaged JS, not
    # by trusting an out-of-bundle dictionary's presence.
    for name,spec in c.load(c.CANDIDATE/'hardcoded-ui-reviewed.json')['files'].items():
        archive,path=name.split('/',1);raw=entry(archive,path)
        if c.sha(raw)!=spec['patched_sha256']:raise ValueError('packaged reviewed UI differs')
        scripts[name]=raw.decode()
    for path,spec in c.load(c.CANDIDATE/'language-whitelist-reviewed.json')['files'].items():
        raw=entry('ion-dist.asar',path)
        if c.sha(raw)!=spec['patched_sha256'] or b'"zh-CN","zh-Hans"' not in raw:
            raise ValueError('packaged dual-alias language gate differs')
        scripts['ion-dist.asar/'+path]=raw.decode()
    compat=c.load(c.CANDIDATE/'compatibility-reviewed.json')
    for action,spec in compat['files'].items():
        raw=entry(spec['archive'],spec['path'])
        if c.sha(raw)!=spec['patched_sha256']:raise ValueError('packaged compatibility source differs')
        scripts[spec['archive']+'/'+spec['path']]=raw.decode()
        if action=='gated':
            from claude5_compat import js
            # Extract original English plus AST-localized Chinese aliases via
            # pinned patched output; exact output hash binds both dictionaries.
            english=js('gated',raw.decode())
            chinese=c.load(c.CANDIDATE/'gated-zh-CN.json')
            checks['gated']={'en':english,'zh':chinese,'strictSelections':True}
    policy=entry('app.asar',compat['policy_entry'])
    if c.sha(policy)!=compat['policy_sha256']:raise ValueError('packaged session policy differs')
    scripts['app.asar/'+compat['policy_entry']]=policy.decode()
    for alias in ('zh-CN','zh_CN','zh-Hans'):
        raw=(app/'Contents/Resources'/f'{alias}.lproj/Localizable.strings').read_bytes()
        if c.sha(raw)!=receipt['native_menu_sha256']:raise ValueError('packaged native menu differs')
    if c.sha((app/'Contents/Resources/ion-dist/index.html').read_bytes())!=receipt['loose_fallback_sha256']:
        raise ValueError('packaged loose fallback differs')
    verify_electron_asar_integrity(app)
    validation=subprocess.run(['node',str(c.ROOT/'scripts/audit_messages.cjs')],
        input=json.dumps({'catalogs':checks,'scripts':scripts}),text=True,capture_output=True,check=True,timeout=180)
    # Read original only. No strict signature repetition or extraction.
    c.read_source(c.BASE/'Claude.app')
    return {'version':c.VERSION,'status':'packaged_resources_verified_NOT_GUI_ACCEPTANCE',
        'catalogs_and_js':json.loads(validation.stdout),'missing':0,'locale_gates':3,
        'aliases':['zh-CN','zh-Hans'],'signature_status':signed['status'],
        'signature_evidence_kind':signed['evidence_kind'],'candidate_validation_status':signed['candidate_validation_status'],
        'candidate_asar_sha256':hashes,'signed_unpacked_integrity_verified':True,
        'source_original_unchanged':True,'installed':False,'launched':False,'gui_verified':False}

if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__);cli.add_argument('--candidate',type=Path,required=True)
    print(json.dumps(audit(cli.parse_args().candidate),ensure_ascii=False,indent=2))
