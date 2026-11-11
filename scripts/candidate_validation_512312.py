"""Copied signed candidate evidence, NOT an installer/permission/recovery API.

Origin pins come from the independently observed outer-only control. No legacy
failed signing record is promoted to a successful signing run. Receipts are
unsigned local evidence: native verification and pinned bytes remain mandatory.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

import claude5_candidate as c
from install_51231 import safe, file_hash

ORIGIN_SHA256='a9f6e2246df28ffc2ed2ce94bc0f09df32d4158b17ac0d13fa8bd9e50bedbbc9'
FAILURE_SHA256='d929e12b02c1bee24f3d0ad075bc1837597390184e30d5837449205786e3dc8d'
ORIGIN_NAMES={'outer-diagnostic.json','full03-failed-signature-record.json',
              'engineering-receipt.json','source-resource-manifest.json','outer-entitlements.plist'}
CURRENT_NAMES={'engineering-receipt.json','source-resource-manifest.json'}


def _key(s):
    return s.st_dev,s.st_ino,s.st_size,s.st_mode,s.st_mtime_ns,s.st_ctime_ns


def _bytes(path):
    safe(path)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        before=os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_size>1<<20 or before.st_nlink!=1
                or before.st_uid!=os.getuid() or before.st_mode&0o022):
            raise ValueError('unsafe candidate receipt')
        raw=stream.read((1<<20)+1)
        if len(raw)>1<<20 or _key(before)!=_key(os.fstat(stream.fileno())) or _key(before)!=_key(path.lstat()):
            raise ValueError('candidate receipt changed')
    return raw


def _json(raw):
    def unique(pairs):
        out={}
        for k,v in pairs:
            if k in out:raise ValueError('duplicate receipt field')
            out[k]=v
        return out
    return json.loads(raw,object_pairs_hook=unique)


def tree_evidence(app):
    """One-pass bytes/modes/links; canonical tree matches existing installer."""
    app=Path(app).absolute();safe(app)
    if not app.is_dir():raise ValueError('candidate missing')
    rows=[]
    for p in sorted(app.rglob('*')):
        before=p.lstat();name=p.relative_to(app).as_posix()
        if stat.S_ISLNK(before.st_mode):
            if not p.resolve().is_relative_to(app.resolve()):raise ValueError('candidate link escapes')
            rows.append([name,'link',os.readlink(p)])
        elif stat.S_ISDIR(before.st_mode):rows.append([name,'dir',stat.S_IMODE(before.st_mode)])
        elif stat.S_ISREG(before.st_mode):
            digest=file_hash(p)
            if _key(before)!=_key(p.lstat()):raise ValueError('candidate changed during hash')
            rows.append([name,'file',stat.S_IMODE(before.st_mode),before.st_size,digest])
        else:raise ValueError('special candidate node')
    by_name={r[0]:r for r in rows}
    return {'tree_sha256':c.sha(json.dumps(sorted(rows),ensure_ascii=False,separators=(',',':')).encode()),
            'diagnostic_tree_sha256':c.sha(json.dumps(rows,separators=(',',':')).encode()),
            'regular_files':sum(r[1]=='file' for r in rows),
            'logical_bytes':sum(r[3] for r in rows if r[1]=='file'),
            'candidate_asar_sha256':{a:by_name['Contents/Resources/'+a][4] for a in c.ARCHIVES}}


def _success(step,deadline,command=None):
    if (not isinstance(step,dict) or type(step.get('exit')) is not int or step['exit']!=0
            or step.get('timed_out') is not False or step.get('deadline_seconds')!=deadline
            or (command is not None and step.get('command')!=command)):
        raise ValueError('missing or unsuccessful command proof')


def _origin(diag,failed,ent_hash):
    if (diag.get('status')!='sign_verify_audit_pass' or any(diag.get(k) is not True for k in
            ('sign_completed_successfully','artifact_signature_verified','resource_audit_passed',
             'source_unchanged','source_entitlements_unchanged'))):
        raise ValueError('origin is not the successful outer-only control')
    if failed.get('status')!='signing_failed' or failed.get('error_type')!='TimeoutExpired' or len(failed.get('targets',[]))!=37:
        raise ValueError('historical failed signing record was rewritten')
    commands=diag['commands'];signs=[s for s in commands if '--sign' in s.get('command',[])]
    if len(signs)!=1:raise ValueError('origin is not one outer-only sign')
    ent=diag['entitlements']
    if ent['sha256']!=ent_hash:raise ValueError('origin entitlements differ')
    app=diag['scratch']
    expected=['/usr/bin/codesign','--force','--sign','-','--timestamp=none','--options','runtime',
        '--preserve-metadata=identifier,flags','--entitlements',ent['copy_path'],'-r',
        '=designated => identifier "com.anthropic.claudefordesktop"',app]
    _success(signs[0],30,expected)
    verify=[s for s in commands if s.get('role')=='outer-deep-strict-verify']
    audit=[s for s in commands if s.get('role')=='postsign-resource-audit']
    if len(verify)!=1 or len(audit)!=1:raise ValueError('origin validation roles ambiguous')
    _success(verify[0],45,['/usr/bin/codesign','--verify','--deep','--strict','--verbose=2',app])
    _success(audit[0],240)
    if audit[0]['command'][-2:]!=['--candidate',app]:raise ValueError('origin audit target mismatch')


def read_current(app,*,expected_origin_sha256=ORIGIN_SHA256,expected_failure_sha256=FAILURE_SHA256):
    """Strict current reader. Pin overrides are trusted caller/test inputs, not CLI/JSON options."""
    try:
        app=Path(app).absolute();safe(app);slot=app.parent
        record_raw=_bytes(slot/'candidate-validation-record.json')
        r=_json(record_raw)
        if (type(r.get('schema_version')) is not int or r['schema_version']!=1
                or r.get('kind')!='copied-signed-candidate-validation' or r.get('version')!=c.VERSION
                or r.get('status')!='awaiting_install_acceptance' or 'targets' in r
                or r.get('tree_algorithm')!='claude-cn-install-tree-v1'
                or r.get('local_signature_identity')!='ad-hoc (-), not Anthropic'
                or any(r.get(k) is not False for k in ('installed','launched','gui_verified',
                                                      'install_authorized','business_recovery_verified'))):
            raise ValueError('not a current copy-validation receipt; no authority granted')
        if set(r['origin_files_sha256'])!=ORIGIN_NAMES or set(r['current_files_sha256'])!=CURRENT_NAMES:
            raise ValueError('receipt file scope differs')
        origin={name:_bytes(slot/'origin'/name) for name in ORIGIN_NAMES}
        current={name:_bytes(slot/name) for name in CURRENT_NAMES}
        if any(c.sha(raw)!=r['origin_files_sha256'][n] for n,raw in origin.items()) or any(
                c.sha(raw)!=r['current_files_sha256'][n] for n,raw in current.items()):
            raise ValueError('origin/current evidence tampered')
        if (c.sha(origin['outer-diagnostic.json'])!=expected_origin_sha256
                or c.sha(origin['full03-failed-signature-record.json'])!=expected_failure_sha256):
            raise ValueError('origin approval pin mismatch')
        diag=_json(origin['outer-diagnostic.json']);failed=_json(origin['full03-failed-signature-record.json'])
        _origin(diag,failed,c.sha(origin['outer-entitlements.plist']))
        if current['source-resource-manifest.json']!=origin['source-resource-manifest.json']:
            raise ValueError('source resource profile changed')
        tree=tree_evidence(app)
        if (r['source_tree_sha256']!=r['final_tree_sha256'] or r['final_tree_sha256']!=tree['tree_sha256']
                or r['source_diagnostic_tree_sha256']!=tree['diagnostic_tree_sha256']
                or tree['diagnostic_tree_sha256']!=diag['scratch_after']['tree_sha256']
                or r['candidate_asar_sha256']!=tree['candidate_asar_sha256']):
            raise ValueError('candidate/source/final tree binding mismatch')
        eng=_json(current['engineering-receipt.json']);profile=_json(current['source-resource-manifest.json'])
        if (eng['candidate_asar_sha256']!=r['candidate_asar_sha256']
                or eng['resource_manifest_sha256']!=c.sha(current['source-resource-manifest.json'])):
            raise ValueError('current engineering profile binding mismatch')
        proofs=r['verification'];source=str(c.BASE/'Claude.app')
        display=proofs['source_official_display']
        _success(display,20,['/usr/bin/codesign','-dv','--verbose=4',source])
        if not {'TeamIdentifier=Q6L2SF6YDW','Authority=Developer ID Application: Anthropic PBC (Q6L2SF6YDW)',
                'Identifier=com.anthropic.claudefordesktop'}<=set(display.get('stderr','').splitlines()):
            raise ValueError('official source identity proof missing')
        _success(proofs['source_official_verify'],45,['/usr/bin/codesign','--verify','--deep','--strict',source])
        verify_args=['/usr/bin/codesign','--verify','--deep','--strict','--verbose=2',str(app)]
        _success(proofs['local_deep_strict'],45,verify_args)
        audited=proofs['resource_audit'];_success(audited,240)
        if audited['command'][-2:]!=['--candidate',str(app)]:raise ValueError('current audit target mismatch')
        audit=audited['result']
        if (audit['version']!=c.VERSION or type(audit['missing']) is not int or audit['missing']!=0
                or audit['catalogs_and_js']['errors']!=[]
                or audit['catalogs_and_js']['catalogs']!={g:d['translated'] for g,d in profile['catalogs'].items()}
                or audit['signed_unpacked_integrity_verified'] is not True or audit['source_original_unchanged'] is not True):
            raise ValueError('resource validation incomplete')
        # The local receipt alone never substitutes for current native verification.
        native=subprocess.run(verify_args,capture_output=True,text=True,timeout=45,check=False)
        if native.returncode:raise ValueError('current native signature invalid')
        # Native success describes that boundary, not permanent approval of an
        # earlier hash. Rebind both artifact and exact evidence bytes afterward.
        if tree_evidence(app)!=tree:
            raise ValueError('candidate changed across native verification')
        if (_bytes(slot/'candidate-validation-record.json')!=record_raw
                or any(_bytes(slot/'origin'/n)!=raw for n,raw in origin.items())
                or any(_bytes(slot/n)!=raw for n,raw in current.items())):
            raise ValueError('candidate evidence changed across native verification')
        return r
    except Exception as error:
        raise ValueError('current candidate validation refused') from error
