import functools
import importlib.util
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import prepare_512312 as prepare
import install_51231 as installer
import test_candidate_validation_512312 as fixtures

path=ROOT/'scripts/review_profile_512312.py';profile_api=ModuleType('missing_review_profile')
if path.exists():
    spec=importlib.util.spec_from_file_location('review_profile_test',path)
    profile_api=importlib.util.module_from_spec(spec);spec.loader.exec_module(profile_api)


class NativeBoundary:
    def verify(self,app,version):
        if plistlib.loads((app/'Contents/Info.plist').read_bytes())['CFBundleShortVersionString']!=version:
            raise ValueError('version mismatch at native boundary')


class SourceInputCopyTests(unittest.TestCase):
    def test_new_slot_excludes_only_old_derived_install_profile(self):
        helper=getattr(prepare,'copy_source_inputs',None)
        self.assertTrue(callable(helper),'source input copy policy missing')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();old=root/'old';old.mkdir();new=root/'new'
            before=b'{"version":"5.1231.0","old-derived":true}'
            (old/'install-profile.json').write_bytes(before)
            (old/'authored.json').write_bytes(b'authored source bytes')
            helper(old,new)
            self.assertFalse((new/'install-profile.json').exists())
            self.assertEqual((new/'authored.json').read_bytes(),b'authored source bytes')
            self.assertEqual((old/'install-profile.json').read_bytes(),before)

    def test_existing_destination_is_never_rewritten_or_cleaned(self):
        helper=getattr(prepare,'copy_source_inputs',None)
        self.assertTrue(callable(helper),'source input copy policy missing')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();old=root/'old';old.mkdir();new=root/'new';new.mkdir()
            (new/'install-profile.json').write_bytes(b'keep existing derived profile')
            with self.assertRaises(FileExistsError):helper(old,new)
            self.assertEqual((new/'install-profile.json').read_bytes(),b'keep existing derived profile')


class ReviewProfileTests(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.ValidationTests();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.case.fixture();self.app=self.case.app

    def api(self):
        self.assertTrue(callable(getattr(profile_api,'build_profile',None)),'schema2 profile builder missing')
        self.assertTrue(callable(getattr(profile_api,'verify_profile',None)),'schema2 profile verifier missing')

    def context(self):
        return patch('candidate_validation_512312.read_current',functools.partial(fixtures.v.read_current,**self.case.pins))

    def build(self):
        self.api()
        with self.context(),patch.object(fixtures.v.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'','')):
            return profile_api.build_profile(self.app)

    def verify(self,profile):
        with self.context(),patch.object(fixtures.v.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'','')):
            return profile_api.verify_profile(self.app,NativeBoundary(),profile)

    def test_schema2_binds_current_receipts_and_existing_installer_tree(self):
        profile=self.build()
        self.assertEqual(profile['schema'],2);self.assertEqual(profile['version'],'5.1231.2')
        self.assertEqual(profile['bundle_sha256'],installer.tree_hash(self.app))
        self.assertEqual(set(profile['receipts']),{'engineering-receipt.json','candidate-validation-record.json','source-resource-manifest.json'})
        self.assertNotIn('local-signature-record.json',profile['receipts'])
        self.assertFalse(profile['install_authorized']);self.assertFalse(profile['business_recovery_verified'])
        self.verify(profile)

    def test_old_profile_refused_for_point_two_by_both_new_and_existing_verifier(self):
        self.api()
        old=json.loads((ROOT/'resources/candidates/claude-desktop-5.1231.0/install-profile.json').read_text())
        with self.assertRaises(ValueError):self.verify(old)
        with self.assertRaises(ValueError):installer.verify_candidate(self.app,NativeBoundary(),old)

    def test_wrong_tree_asar_receipt_or_authority_refused(self):
        profile=self.build()
        for field,value in (('bundle_sha256','0'*64),('version','5.1231.0'),('schema',True),
                            ('install_authorized',True),('native_restore_verified',True)):
            with self.subTest(field=field),self.assertRaises(ValueError):self.verify({**profile,field:value})
        changed={**profile,'archives':{**profile['archives'],'app.asar':'0'*64}}
        with self.assertRaises(ValueError):self.verify(changed)
        changed={**profile,'receipts':{**profile['receipts'],'candidate-validation-record.json':'0'*64}}
        with self.assertRaises(ValueError):self.verify(changed)

    def test_receipt_tamper_after_profile_creation_is_refused(self):
        profile=self.build();p=self.app.parent/'candidate-validation-record.json'
        p.write_bytes(p.read_bytes()+b' ')
        with self.assertRaises(ValueError):self.verify(profile)


if __name__=='__main__':unittest.main()
