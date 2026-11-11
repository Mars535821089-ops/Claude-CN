"""Real synthetic files/receipts; only native signature verification is fake."""
import copy
import importlib.util
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from types import ModuleType
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import claude5_candidate as c
from install_51231 import tree_hash
path=ROOT/'scripts/candidate_validation_512312.py'
v=ModuleType('missing_candidate_validation')
if path.exists():
    spec=importlib.util.spec_from_file_location('candidate_validation_fixture',path)
    v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.slot=Path(self.tmp.name).resolve()/'fresh';self.slot.mkdir()
        self.app=self.slot/'Claude.app';(self.app/'Contents/MacOS').mkdir(parents=True)
        (self.app/'Contents/MacOS/Claude').write_bytes(b'synthetic signed-code boundary')
        (self.app/'Contents/Resources').mkdir()
        (self.app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleShortVersionString':c.VERSION,
            'CFBundleIdentifier':'com.anthropic.claudefordesktop'}))
        for name in c.ARCHIVES:(self.app/'Contents/Resources'/name).write_bytes(b'opaque synthetic archive')
        self.record_path=self.slot/'candidate-validation-record.json'

    def api(self):
        self.assertTrue(callable(getattr(v,'tree_evidence',None)),'candidate validation library missing')
        self.assertTrue(callable(getattr(v,'read_current',None)),'strict current receipt reader missing')

    def dump(self,path,obj):
        path.write_text(json.dumps(obj,sort_keys=True)+'\n')
        return c.sha(path.read_bytes())

    def fixture(self):
        self.api();tree=v.tree_evidence(self.app)
        origin=self.slot/'origin';origin.mkdir()
        failed={'status':'signing_failed','error_type':'TimeoutExpired',
                'targets':[{'path':'synthetic-'+str(i),'exit':0} for i in range(37)]}
        failed_hash=self.dump(origin/'full03-failed-signature-record.json',failed)
        ent=origin/'outer-entitlements.plist';ent.write_bytes(b'synthetic original entitlements')
        origin_app='/synthetic/origin/Claude.app';ent_path='/synthetic/origin/original-outer-entitlements.plist'
        command=['/usr/bin/codesign','--force','--sign','-','--timestamp=none','--options','runtime',
                 '--preserve-metadata=identifier,flags','--entitlements',ent_path,'-r',
                 '=designated => identifier "com.anthropic.claudefordesktop"',origin_app]
        def step(role,argv,deadline):return {'role':role,'command':argv,'deadline_seconds':deadline,
            'exit':0,'timed_out':False,'elapsed_seconds':0.1,'stdout':'','stderr':''}
        diag={'status':'sign_verify_audit_pass','scratch':origin_app,
              'sign_completed_successfully':True,'artifact_signature_verified':True,'resource_audit_passed':True,
              'source_unchanged':True,'source_entitlements_unchanged':True,
              'scratch_after':{'tree_sha256':tree['diagnostic_tree_sha256']},
              'entitlements':{'copy_path':ent_path,'sha256':c.sha(ent.read_bytes())},
              'commands':[step('outer-only-sign',command,30),
                step('outer-deep-strict-verify',['/usr/bin/codesign','--verify','--deep','--strict','--verbose=2',origin_app],45),
                step('postsign-resource-audit',['python','audit_51231.py','--candidate',origin_app],240)]}
        diag_hash=self.dump(origin/'outer-diagnostic.json',diag)
        profile={'catalogs':{g:{'translated':1} for g in ('desktop','frontend','dynamic','gated')}}
        profile_hash=self.dump(origin/'source-resource-manifest.json',profile)
        (self.slot/'source-resource-manifest.json').write_bytes((origin/'source-resource-manifest.json').read_bytes())
        eng={'version':c.VERSION,'candidate_asar_sha256':tree['candidate_asar_sha256'],
             'resource_manifest_sha256':profile_hash}
        self.dump(origin/'engineering-receipt.json',eng);self.dump(self.slot/'engineering-receipt.json',eng)
        audit={'version':c.VERSION,'missing':0,'catalogs_and_js':{'catalogs':{g:1 for g in profile['catalogs']},'errors':[]},
               'signed_unpacked_integrity_verified':True,'source_original_unchanged':True}
        source_display=step('source-official-display',['/usr/bin/codesign','-dv','--verbose=4',str(c.BASE/'Claude.app')],20)
        source_display['stderr']='TeamIdentifier=Q6L2SF6YDW\nAuthority=Developer ID Application: Anthropic PBC (Q6L2SF6YDW)\nIdentifier=com.anthropic.claudefordesktop\n'
        record={'schema_version':1,'kind':'copied-signed-candidate-validation','version':c.VERSION,
            'status':'awaiting_install_acceptance','installed':False,'launched':False,'gui_verified':False,
            'install_authorized':False,'business_recovery_verified':False,
            'tree_algorithm':'claude-cn-install-tree-v1','source_tree_sha256':tree['tree_sha256'],
            'final_tree_sha256':tree['tree_sha256'],'source_diagnostic_tree_sha256':tree['diagnostic_tree_sha256'],
            'candidate_asar_sha256':tree['candidate_asar_sha256'],
            'local_signature_identity':'ad-hoc (-), not Anthropic',
            'origin_files_sha256':{p.name:c.sha(p.read_bytes()) for p in origin.iterdir()},
            'current_files_sha256':{n:c.sha((self.slot/n).read_bytes()) for n in ('engineering-receipt.json','source-resource-manifest.json')},
            'verification':{'source_official_display':source_display,
              'source_official_verify':step('source-official-verify',['/usr/bin/codesign','--verify','--deep','--strict',str(c.BASE/'Claude.app')],45),
              'local_deep_strict':step('local-deep-strict',['/usr/bin/codesign','--verify','--deep','--strict','--verbose=2',str(self.app)],45),
              'resource_audit':{**step('resource-audit',['python','audit_51231.py','--candidate',str(self.app)],240),'result':audit}}}
        self.dump(self.record_path,record)
        self.pins={'expected_origin_sha256':diag_hash,'expected_failure_sha256':failed_hash}
        return record

    def read(self):
        with patch.object(v.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'','')) as native:
            result=v.read_current(self.app,**self.pins)
            self.assertEqual(native.call_count,1)
            self.assertEqual(native.call_args.kwargs['timeout'],45)
            self.assertEqual(native.call_args.args[0],['/usr/bin/codesign','--verify','--deep','--strict','--verbose=2',str(self.app)])
            return result

    def test_tree_reuses_installer_canonical_format(self):
        self.api()
        self.assertEqual(v.tree_evidence(self.app)['tree_sha256'],tree_hash(self.app))

    def test_current_copy_receipt_is_not_a_fabricated_signing_run(self):
        self.fixture();record=self.read()
        self.assertEqual(record['status'],'awaiting_install_acceptance')
        self.assertNotIn('targets',record)
        self.assertFalse(record['install_authorized']);self.assertFalse(record['business_recovery_verified'])
        self.assertEqual(json.loads((self.slot/'origin/full03-failed-signature-record.json').read_text())['status'],'signing_failed')

    def test_payload_tamper_rejected_even_with_faked_current_tree_fields(self):
        record=self.fixture()
        (self.app/'Contents/MacOS/Claude').write_bytes(b'tampered')
        current=v.tree_evidence(self.app)
        record['source_tree_sha256']=record['final_tree_sha256']=current['tree_sha256']
        record['source_diagnostic_tree_sha256']=current['diagnostic_tree_sha256']
        self.dump(self.record_path,record)
        with self.assertRaises(ValueError):self.read()

    def test_origin_tamper_cannot_be_reapproved_by_editing_its_untrusted_receipt_hash(self):
        record=self.fixture();p=self.slot/'origin/outer-diagnostic.json'
        diag=json.loads(p.read_text());diag['status']='signing_failed'
        record['origin_files_sha256'][p.name]=self.dump(p,diag);self.dump(self.record_path,record)
        with self.assertRaises(ValueError):self.read()

    def test_old_failed_sign_record_alone_is_never_a_current_validation(self):
        self.api()
        self.dump(self.slot/'local-signature-record.json',{'status':'signing_failed','targets':[{'exit':0}]*37})
        with self.assertRaises(ValueError):v.read_current(self.app)

    def test_failed_origin_cannot_masquerade_as_new_current_schema(self):
        self.fixture();self.dump(self.record_path,{'status':'local_signature_verified','targets':[{'exit':0}]*38})
        with self.assertRaises(ValueError):self.read()

    def test_unknown_schema_or_elevated_authority_is_rejected(self):
        record=self.fixture()
        for field,value in (('schema_version',2),('schema_version',True),('status','approved'),
                            ('install_authorized',True),('business_recovery_verified',True),('targets',[])):
            with self.subTest(field=field,value=value):
                self.dump(self.record_path,{**record,field:value})
                with self.assertRaises(ValueError):self.read()

    def test_current_proof_failure_or_fake_audit_is_rejected(self):
        record=self.fixture()
        for role in ('source_official_verify','local_deep_strict','resource_audit'):
            modified=copy.deepcopy(record);modified['verification'][role]['exit']=1
            self.dump(self.record_path,modified)
            with self.subTest(role=role),self.assertRaises(ValueError):self.read()
        modified=copy.deepcopy(record);modified['verification']['resource_audit']['result']['missing']=1
        self.dump(self.record_path,modified)
        with self.assertRaises(ValueError):self.read()

    def test_real_native_verification_failure_cannot_be_replaced_by_saved_success(self):
        self.fixture()
        with patch.object(v.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','bad signature')):
            with self.assertRaises(ValueError):v.read_current(self.app,**self.pins)

    def test_native_success_does_not_vouch_for_an_app_changed_during_verification(self):
        self.fixture();changed=self.app/'Contents/MacOS/Claude'
        def native(args,**kwargs):
            changed.write_bytes(b'changed during native boundary')
            return subprocess.CompletedProcess(args,0,'','')
        with patch.object(v.subprocess,'run',side_effect=native):
            with self.assertRaises(ValueError):v.read_current(self.app,**self.pins)
        self.assertEqual(changed.read_bytes(),b'changed during native boundary')

    def test_native_success_does_not_vouch_for_changed_current_receipt_bytes(self):
        self.fixture();raw=self.record_path.read_bytes()
        def native(args,**kwargs):
            self.record_path.write_bytes(raw+b' ')
            return subprocess.CompletedProcess(args,0,'','')
        with patch.object(v.subprocess,'run',side_effect=native):
            with self.assertRaises(ValueError):v.read_current(self.app,**self.pins)
        self.assertEqual(self.record_path.read_bytes(),raw+b' ')

    def test_native_success_does_not_vouch_for_changed_origin_or_metadata_bytes(self):
        self.fixture()
        paths=[self.slot/'origin'/n for n in v.ORIGIN_NAMES]+[self.slot/n for n in v.CURRENT_NAMES]
        for path in paths:
            raw=path.read_bytes()
            def native(args,**kwargs):
                path.write_bytes(raw+b' ')
                return subprocess.CompletedProcess(args,0,'','')
            try:
                with self.subTest(path=path.relative_to(self.slot)),patch.object(v.subprocess,'run',side_effect=native):
                    with self.assertRaises(ValueError):v.read_current(self.app,**self.pins)
                self.assertEqual(path.read_bytes(),raw+b' ')
            finally:
                path.write_bytes(raw)  # Only restore this test's synthetic fixture.

    def test_metadata_tamper_missing_or_redirected_receipt_rejected(self):
        record=self.fixture();p=self.slot/'engineering-receipt.json'
        p.write_bytes(p.read_bytes()+b' ')
        with self.assertRaises(ValueError):self.read()
        p.unlink()
        with self.assertRaises(ValueError):self.read()
        retained=self.slot/'saved-current.json';self.record_path.rename(retained);self.record_path.symlink_to(retained)
        with self.assertRaises(ValueError):self.read()

    def test_packaged_audit_refuses_failed_legacy_and_status_only_relabel(self):
        import audit_51231 as a
        helper=getattr(a,'signature_evidence',None)
        self.assertTrue(callable(helper),'packaged audit current-receipt adapter missing')
        path=self.slot/'local-signature-record.json'
        for status in ('signing_failed','local_signature_verified'):
            self.dump(path,{'status':status,'error_type':'TimeoutExpired','targets':[{'path':'x','exit':0}]*37})
            with self.assertRaises(ValueError):helper(self.app,{'candidate_asar_sha256':{}})

    def test_packaged_audit_reads_the_strict_current_receipt(self):
        import audit_51231 as a
        import functools
        helper=getattr(a,'signature_evidence',None)
        self.assertTrue(callable(helper),'packaged audit current-receipt adapter missing')
        record=self.fixture()
        with patch('candidate_validation_512312.read_current',functools.partial(v.read_current,**self.pins)),patch.object(
                v.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'','')):
            evidence=helper(self.app,{'candidate_asar_sha256':{}})
        self.assertEqual(evidence['candidate_asar_sha256'],record['candidate_asar_sha256'])
        self.assertEqual(evidence['candidate_validation_status'],'awaiting_install_acceptance')
        self.assertEqual(evidence['evidence_kind'],'copied-signed-candidate-validation')


if __name__=='__main__':unittest.main()
