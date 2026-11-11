import sys
import unittest
import tempfile
import json
import os
import plistlib
import subprocess
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import sign_review_51231 as s

class SigningReviewTests(unittest.TestCase):
    def framework_fixture(self, root):
        app=root/'implementation-test/full-03/Claude.app'
        framework=app/'Contents/Frameworks/Electron Framework.framework'
        version=framework/'Versions/A';(version/'Resources').mkdir(parents=True)
        main=version/'Electron Framework';main.write_bytes(b'synthetic framework');main.chmod(0o755)
        (version/'Resources/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleExecutable':'Electron Framework','CFBundleIdentifier':'com.github.Electron.framework',
            'CFBundlePackageType':'FMWK'}))
        (framework/'Versions/Current').symlink_to('A')
        (framework/'Electron Framework').symlink_to('Versions/Current/Electron Framework')
        outer=app/'Contents/MacOS/Claude';outer.parent.mkdir();outer.write_bytes(b'synthetic app');outer.chmod(0o755)
        native=app/'Contents/Resources/native.node';native.parent.mkdir();native.write_bytes(b'synthetic addon')
        return app,framework,main,outer,native

    def target_plan(self, app):
        helper=getattr(s,'reviewed_sign_targets',None)
        self.assertTrue(callable(helper),'reviewed framework target-role planner missing')
        return helper(app)

    def test_electron_main_is_omitted_once_but_framework_and_other_files_remain(self):
        with tempfile.TemporaryDirectory() as tmp:
            app,framework,main,outer,native=self.framework_fixture(Path(tmp).resolve())
            files,bundles,omitted=self.target_plan(app)
            self.assertEqual(set(files),{outer,native})
            self.assertEqual(bundles,[framework])
            self.assertEqual(omitted,{'file':str(main.relative_to(app)),
                                      'bundle':str(framework.relative_to(app))})

    def test_missing_or_duplicate_known_main_or_bundle_refuses_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            app,framework,main,outer,native=self.framework_fixture(Path(tmp).resolve())
            for files,bundles in (([outer,native],[framework]),([main,main,outer,native],[framework]),
                                  ([main,outer,native],[]),([main,outer,native],[framework,framework])):
                with self.subTest(files=len(files),bundles=len(bundles)), patch.object(
                        s,'_collect_sign_targets',return_value=(files,bundles)):
                    with self.assertRaises(ValueError):self.target_plan(app)

    def test_wrong_framework_executable_identifier_or_package_type_refuses_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            app,framework,main,outer,native=self.framework_fixture(Path(tmp).resolve())
            path=framework/'Versions/A/Resources/Info.plist';original=plistlib.loads(path.read_bytes())
            for field,value in (('CFBundleExecutable','Other'),('CFBundleIdentifier','other.framework'),('CFBundlePackageType','APPL')):
                with self.subTest(field=field):
                    path.write_bytes(plistlib.dumps({**original,field:value}))
                    with self.assertRaises(ValueError):self.target_plan(app)

    def test_framework_alias_must_resolve_to_the_unique_known_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            app,framework,main,outer,native=self.framework_fixture(Path(tmp).resolve())
            alias=framework/'Electron Framework';alias.unlink();alias.symlink_to(outer)
            with self.assertRaises(ValueError):self.target_plan(app)

    def test_second_path_to_same_framework_main_inode_is_not_silently_omitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            app,framework,main,outer,native=self.framework_fixture(Path(tmp).resolve())
            os.link(main,main.with_name('duplicate-native'))
            with self.assertRaises(ValueError):self.target_plan(app)

    def sign_fixture(self, root, timeout=False):
        app,framework,main,outer,native=self.framework_fixture(root)
        (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleShortVersionString':s.c.VERSION}))
        hashes={}
        for archive in s.c.ARCHIVES:
            p=app/'Contents/Resources'/archive;p.write_bytes(b'synthetic archive')
            hashes[archive]=s.c.sha(p.read_bytes())
        (app.parent/'engineering-receipt.json').write_text(json.dumps({'version':s.c.VERSION,
            'status':'FULL_RESOURCE_COMPAT_REVIEW_CANDIDATE_NOT_APPROVED_FOR_INSTALL',
            'candidate_asar_sha256':hashes}))
        events=[]
        def command(args,**kwargs):
            if '--sign' in args:
                self.assertEqual(kwargs['timeout'],30)
                events.append(('sign',args[-1]))
                if timeout:raise subprocess.TimeoutExpired(args,30)
                return subprocess.CompletedProcess(args,0,'','')
            if '--verify' in args:
                self.assertEqual(kwargs['timeout'],45)
                self.assertIn('--strict',args);self.assertIn('--deep',args)
                events.append(('verify',args[-1]))
                return subprocess.CompletedProcess(args,0,'','')
            self.assertIn('--entitlements',args);self.assertEqual(kwargs['timeout'],20)
            return subprocess.CompletedProcess(args,0,b'',b'')
        with patch.object(s.c,'BASE',root),patch.object(s.os.path,'ismount',return_value=True),patch.object(
                s.subprocess,'run',side_effect=command),patch.object(s,'refresh_unpacked_integrity',
                side_effect=lambda p:events.append(('refresh',p.name)) or []),patch.object(
                s,'update_electron_asar_integrity',side_effect=lambda *args:events.append(('digest',args[1]))),patch.object(
                s,'verify_electron_asar_integrity',side_effect=lambda p:events.append(('electron-verify',str(p)))):
            if timeout:
                with self.assertRaises(subprocess.TimeoutExpired):s.sign(app)
                record=json.loads((app.parent/'local-signature-record.json').read_text())
            else:record=s.sign(app)
        return app,framework,main,outer,native,events,record

    def test_sign_refreshes_slots_before_framework_bundle_then_outer_and_strict_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            app,framework,main,outer,native,events,record=self.sign_fixture(Path(tmp).resolve())
            signed=[path for role,path in events if role=='sign']
            self.assertNotIn(str(main),signed)
            self.assertEqual(signed.count(str(framework)),1)
            self.assertEqual(set(signed),{str(native),str(outer),str(framework),str(app)})
            self.assertEqual(signed[-1],str(app))
            for archive in s.c.ARCHIVES:
                refresh=events.index(('refresh',archive));digest=events.index(('digest','Resources/'+archive))
                self.assertLess(events.index(('sign',str(native))),refresh)
                self.assertLess(refresh,digest)
                self.assertLess(digest,events.index(('sign',str(framework))))
            self.assertEqual(record['status'],'local_signature_verified')
            self.assertEqual(events[-1],('verify',str(app)))

    def test_sign_timeout_keeps_failed_receipt_without_a_second_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            app,framework,main,outer,native,events,record=self.sign_fixture(Path(tmp).resolve(),timeout=True)
            self.assertEqual(record['status'],'signing_failed')
            self.assertEqual(record['error_type'],'TimeoutExpired')
            self.assertEqual(len([e for e in events if e[0]=='sign']),1)
            self.assertFalse(any(e[0]=='verify' for e in events))
    def test_never_sign_original_or_installed_app(self):
        with patch.object(s.subprocess,'run') as command:
            for p in (Path('/Applications/Claude.app'),s.c.BASE/'Claude.app',
                      s.c.BASE/'implementation-test/../Claude.app'):
                with self.assertRaises(ValueError):s.sign(p)
            command.assert_not_called()

    def test_ad_hoc_entitlements_remove_team_keys_but_keep_virtualization(self):
        before={'com.apple.security.virtualization':True,'keychain-access-groups':['synthetic'],
                'com.apple.application-identifier':'synthetic','com.apple.developer.team-identifier':'synthetic'}
        after=s.local_entitlements(before)
        self.assertTrue(after['com.apple.security.virtualization'])
        self.assertTrue(after['com.apple.security.cs.disable-library-validation'])
        self.assertNotIn('keychain-access-groups',after)
        self.assertIn('keychain-access-groups',before)

    def test_signing_changed_unpacked_bytes_refresh_header_without_packed_edits(self):
        from upstream_patch_lib import build_asar,read_asar_header,read_asar_entry,asar_entry
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);work=root/'work';work.mkdir()
            (work/'keep.js').write_bytes(b'const unchanged = true;')
            (work/'native.node').write_bytes(b'native fixture')
            archive=root/'app.asar';build_asar(work,archive,{'native.node'})
            external=Path(str(archive)+'.unpacked')/'native.node'
            external.write_bytes(b'native fixture with synthetic signature')
            s.refresh_unpacked_integrity(archive)
            raw=archive.read_bytes();size,_,header=read_asar_header(raw,archive)
            self.assertEqual(read_asar_entry(raw,size,header,'keep.js'),b'const unchanged = true;')
            entry=asar_entry(header,'native.node')
            self.assertEqual(entry['size'],external.stat().st_size)
            self.assertEqual(entry['integrity']['hash'],s.c.sha(external.read_bytes()))
