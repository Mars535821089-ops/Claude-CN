"""Fresh public candidate copy and verification only. Contains NO signing action."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import claude5_candidate as c
import candidate_validation_512312 as v
from build_smoke_51231 import destination

SOURCE=c.BASE/'diagnostic-outer-once-1b2f01b70a0041da8095da25fb30e28d/Claude.app'
TARGET=c.BASE/'implementation-02/install-review-01/Claude.app'


def main():
    target=destination(TARGET);slot=target.parent
    original=(SOURCE.parent/'diagnostic.json').read_bytes()
    failed=(SOURCE.parent/'local-signature-record.json').read_bytes()
    if c.sha(original)!=v.ORIGIN_SHA256 or c.sha(failed)!=v.FAILURE_SHA256:
        raise ValueError('approved origin changed; refuse copy')
    diag=c.load(SOURCE.parent/'diagnostic.json')
    ent=(SOURCE.parent/'original-outer-entitlements.plist').read_bytes()
    v._origin(diag,json.loads(failed),c.sha(ent))
    slot.mkdir(parents=True,exist_ok=False)
    report={'source':str(SOURCE),'target':str(target),'commands':[],'status':'in_progress',
            'operation':'copy_previously_signed_app_without_resigning','installed':False,'launched':False}
    owned=[]

    def cleanup(proc):
        if proc.poll() is None:
            try:os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            proc.wait(timeout=2)

    def run(role,args,deadline):
        if '--sign' in args:raise ValueError('this validation generation cannot sign')
        item={'role':role,'command':[str(x) for x in args],'deadline_seconds':deadline}
        report['commands'].append(item);start=time.monotonic()
        proc=subprocess.Popen(item['command'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,
            env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
        owned.append(proc);item['pid']=proc.pid
        print(json.dumps({'role':role,'pid':proc.pid,'deadline_seconds':deadline}),flush=True)
        try:
            stdout,stderr=proc.communicate(timeout=deadline)
            item.update(exit=proc.returncode,timed_out=False,stdout=stdout.decode(errors='replace'),stderr=stderr.decode(errors='replace'))
        except subprocess.TimeoutExpired as error:
            item.update(timed_out=True,timeout_stdout=(error.stdout or b'').decode(errors='replace'),
                        timeout_stderr=(error.stderr or b'').decode(errors='replace'))
            cleanup(proc);stdout,stderr=proc.communicate(timeout=2)
            item.update(exit=proc.returncode,stdout=stdout.decode(errors='replace'),stderr=stderr.decode(errors='replace'))
        finally:
            cleanup(proc);item['elapsed_seconds']=round(time.monotonic()-start,3);item['reaped']=proc.poll() is not None
            (slot/(role+'.stdout.log')).write_text(item.get('stdout',''))
            (slot/(role+'.stderr.log')).write_text(item.get('stderr',''))
            (slot/'assembly-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:item[k] for k in ('role','exit','timed_out','elapsed_seconds')}),flush=True)
        if item['timed_out'] or item['exit']!=0:raise RuntimeError(role+' failed; no ready receipt')
        return item

    def tree(role,app):
        item=run(role,[sys.executable,'-B',str(Path(__file__).resolve()),'--tree',str(app)],45)
        return json.loads(item['stdout'])

    try:
        source_tree=tree('source-tree-before',SOURCE)
        if source_tree['diagnostic_tree_sha256']!=diag['scratch_after']['tree_sha256']:
            raise ValueError('approved origin app tree changed')
        run('copy-already-signed-app',['/bin/cp','-cR',str(SOURCE),str(target)],30)
        copied_tree=tree('copied-tree-before',target)
        if copied_tree!=source_tree:raise ValueError('copy differs from signed origin')
        origin=slot/'origin';origin.mkdir()
        origins={'outer-diagnostic.json':original,'full03-failed-signature-record.json':failed,
                 'outer-entitlements.plist':ent,
                 'engineering-receipt.json':(SOURCE.parent/'engineering-receipt.json').read_bytes(),
                 'source-resource-manifest.json':(SOURCE.parent/'source-resource-manifest.json').read_bytes()}
        for name,raw in origins.items():
            with (origin/name).open('xb') as out:out.write(raw)
        with (slot/'source-resource-manifest.json').open('xb') as out:out.write(origins['source-resource-manifest.json'])
        engineering=json.loads(origins['engineering-receipt.json'])
        engineering.update(candidate_asar_sha256=copied_tree['candidate_asar_sha256'],
            signature='COPIED_ALREADY_LOCALLY_SIGNED_VALIDATION_PENDING_NO_NEW_SIGNATURE',
            origin_engineering_sha256=c.sha(origins['engineering-receipt.json']),
            copy_validation_kind='copied-signed-candidate-validation')
        with (slot/'engineering-receipt.json').open('x') as out:out.write(json.dumps(engineering,indent=2)+'\n')
        official=str(c.BASE/'Claude.app')
        displayed=run('source-official-display',['/usr/bin/codesign','-dv','--verbose=4',official],20)
        verified_source=run('source-official-verify',['/usr/bin/codesign','--verify','--deep','--strict',official],45)
        verified=run('local-deep-strict',['/usr/bin/codesign','--verify','--deep','--strict','--verbose=2',str(target)],45)
        auditor=str(c.ROOT/'scripts/audit_51231.py')
        audited=run('resource-audit',[sys.executable,'-B',auditor,'--candidate',str(target)],240)
        text=audited['stdout'];audited['result']=json.loads(text[text.index('{'):])
        final_tree=tree('copied-tree-final',target);source_after=tree('source-tree-after',SOURCE)
        if final_tree!=source_tree or source_after!=source_tree:raise ValueError('source or copy changed during validation')
        record={'schema_version':1,'kind':'copied-signed-candidate-validation','version':c.VERSION,
            'status':'awaiting_install_acceptance','installed':False,'launched':False,'gui_verified':False,
            'install_authorized':False,'business_recovery_verified':False,
            'tree_algorithm':'claude-cn-install-tree-v1','source_tree_sha256':source_tree['tree_sha256'],
            'final_tree_sha256':final_tree['tree_sha256'],
            'source_diagnostic_tree_sha256':source_tree['diagnostic_tree_sha256'],
            'candidate_asar_sha256':final_tree['candidate_asar_sha256'],
            'local_signature_identity':'ad-hoc (-), not Anthropic',
            'origin_files_sha256':{n:c.sha(raw) for n,raw in origins.items()},
            'current_files_sha256':{n:c.sha((slot/n).read_bytes()) for n in v.CURRENT_NAMES},
            'verification':{'source_official_display':displayed,'source_official_verify':verified_source,
                            'local_deep_strict':verified,'resource_audit':audited}}
        # Publish once, after all actual proofs, without inventing signing targets.
        pending=slot/'.candidate-validation.pending'
        with pending.open('xb') as out:
            out.write((json.dumps(record,ensure_ascii=False,indent=2)+'\n').encode());out.flush();os.fsync(out.fileno())
        os.link(pending,slot/'candidate-validation-record.json');pending.unlink()
        fd=os.open(slot,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
        # End-to-end exercise the NEW strict reader, not only an in-memory template.
        run('current-receipt-packaged-audit',[sys.executable,'-B',auditor,'--candidate',str(target)],240)
        report.update(status='awaiting_install_acceptance',source_unchanged=True,app_copied_without_resigning=True,
            current_receipt_sha256=c.sha((slot/'candidate-validation-record.json').read_bytes()),
            source_tree=source_tree,final_tree=final_tree)
    except BaseException as error:
        report.update(status='validation_failed_not_ready',error_type=type(error).__name__,error=str(error))
        # If a post-publication check fails, do not leave a current ready receipt.
        receipt=slot/'candidate-validation-record.json'
        if receipt.exists():receipt.rename(slot/'rejected-candidate-validation-record.json')
        raise
    finally:
        for proc in owned:cleanup(proc)
        report['all_owned_processes_reaped']=all(p.poll() is not None for p in owned)
        (slot/'assembly-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'status':report['status'],'slot':str(slot),'all_owned_processes_reaped':report['all_owned_processes_reaped']}),flush=True)


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--tree':print(json.dumps(v.tree_evidence(Path(sys.argv[2]))))
    elif len(sys.argv)==1:main()
    else:raise SystemExit('unsupported copy-validation arguments')
