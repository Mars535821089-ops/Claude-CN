"""Real private files/receipts; mock only native OS and app-process boundaries."""
import importlib
from dataclasses import replace
import json
import os
from pathlib import Path
import plistlib
import shutil
import socket
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))

class Install5Tests(unittest.TestCase):
    def setUp(self):
        if importlib.util.find_spec('install_51231') is None:
            self.fail('version-pinned controlled installer has not been implemented')
        self.m=importlib.import_module('install_51231')
        fixture_root=Path('/Volumes/Mars-私人服务器/内置盘写入统一管理/01-编译与构建/Codex-provider-switch/claude5-compat-20261111/implementation-01/test-fixtures')
        self.tmp=tempfile.TemporaryDirectory(dir=fixture_root);self.addCleanup(self.tmp.cleanup)
        r=Path(self.tmp.name).resolve();self.root=r
        self.paths=self.m.Paths(candidate=r/'external/full-06/Claude.app',installed=r/'Applications/Claude.app',
            private=r/'PrivateArchive',external=r/'external',recovery=r/'PrivateArchive'/('a'*32),home=r/'home')
        for p in [self.paths.external,self.paths.installed.parent,self.paths.private,self.paths.recovery]:p.mkdir(parents=True,exist_ok=True)
        self.paths.private.chmod(0o700);self.paths.recovery.chmod(0o700)
        self.app(self.paths.candidate,'5.1231.0',b'new');self.app(self.paths.installed,'4.23118.1',b'old')
        self.config=self.paths.home/'Library/Application Support/Claude/config.json'
        self.config.parent.mkdir(parents=True);self.config.write_text('{"locale":"en-US","other":"keep"}');self.config.chmod(0o600)
        self.messages=self.config.parent/'sessions';self.messages.mkdir();(self.messages/'draft').write_text('keep draft')
        self.backup=self.paths.recovery/'previous.app'
        self.profile={'version':'5.1231.0','bundle_id':'com.anthropic.claudefordesktop','bundle_sha256':self.m.tree_hash(self.paths.candidate),
            'archives':{n:self.m.file_hash(self.paths.candidate/'Contents/Resources'/n) for n in ('app.asar','ion-dist.asar')},'receipts':{}}
        owner=self
        class OS:
            def __init__(s):s.events=[];s.connected=True;s.running=True;s.new_data=False;s.start_error=False;s.mutate_copy=False;s.cross_device=False
            def mounts(s,p):
                if not s.connected:raise ValueError('unmounted')
                return {'external_uuid':'external-fixture','private_uuid':'private-fixture','private_image_verified':True}
            def verify(s,p,version=None):
                info=plistlib.loads((p/'Contents/Info.plist').read_bytes())
                if info['CFBundleIdentifier']!='com.anthropic.claudefordesktop' or version and info['CFBundleShortVersionString']!=version:raise ValueError('bundle identity')
            def copy(s,a,b):
                s.events.append('copy');owner.m.check_writes();parent=owner.m.bound_parent(b.parent)
                with parent.child(b.name,create=True) as target:
                    target.private=False;cwd=os.open('.',os.O_RDONLY|os.O_DIRECTORY)
                    try:os.fchdir(target.fd);shutil.copytree(a,'.',symlinks=True,dirs_exist_ok=True)
                    finally:os.fchdir(cwd);os.close(cwd)
                    parent.check();target.check();owner.m.check_writes()
                if s.mutate_copy:(b/'Contents/Resources/app.asar').write_bytes(b'corrupt')
            def same_volume(s,a,b):return not s.cross_device
            def quit(s,p):s.events.append('quit');s.running=False
            def stopped(s,p):
                if s.running:raise ValueError('running')
            def exchange(s,a,b):
                # Native renameatx_np is the ONLY mocked filesystem primitive.
                # All conflicting paths, resulting files and receipts are real.
                s.events.append('exchange');hold=a.with_name('os-atomic-swap-fixture');os.rename(a,hold);os.rename(b,a);os.rename(hold,b)
            def start(s,p):
                s.events.append('start');s.running=True
                if s.new_data:(owner.messages/'new-session').write_text('new user data')
                if s.start_error:raise OSError('uncertain launch')
            def observe(s,p):return {'pid':12345,'executable_inode':(p/'Contents/MacOS/Claude').stat().st_ino}
        self.native=OS()
        def swap_at(afd,a,bfd,b):
            hold='.swap-fixture-'+uuid.uuid4().hex
            os.rename(a,hold,src_dir_fd=afd,dst_dir_fd=afd)
            os.rename(b,a,src_dir_fd=bfd,dst_dir_fd=afd)
            os.rename(hold,b,src_dir_fd=afd,dst_dir_fd=bfd)
        self.swap_at=swap_at
        stub=patch.object(self.m,'_swap_at',side_effect=swap_at,create=True);stub.start();self.addCleanup(stub.stop)
        prepare=self.m.prepare_recovery(self.paths,self.native,self.profile)
        grant=self.m.grant(self.paths,self.native,self.profile,operation='prepare',expected_plan=prepare['plan_sha256'],apply=True)
        self.m.prepare_recovery(self.paths,self.native,self.profile,apply=True,authorization=Path(grant['authorization']))
        self.native.events.clear()

    def app(self,p,version,data):
        (p/'Contents/Resources').mkdir(parents=True);(p/'Contents/MacOS').mkdir()
        (p/'Contents/MacOS/Claude').write_bytes(data)
        (p/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'com.anthropic.claudefordesktop','CFBundleShortVersionString':version,'CFBundleVersion':version,'CFBundleExecutable':'Claude'}))
        for n in ['app.asar','ion-dist.asar']:(p/'Contents/Resources'/n).write_bytes(data+n.encode())

    def write(self,p,data):p.write_text(json.dumps(data));p.chmod(0o600)
    def token(self,op='install'):
        plan=self.m.preflight(self.paths,self.native,self.profile,operation=op)
        p=self.paths.recovery/('authorize-'+op+'.json')
        self.write(p,{'operation':op,'plan_sha256':self.m.plan_hash(plan),'expires_at':int(time.time())+300,
            'allow_quit':True,'allow_exchange':True,'allow_launch':op=='install'})
        return p
    def run_install(self,**kwargs):return self.m.install(self.paths,self.native,self.profile,**kwargs)

    def test_default_is_readonly_even_without_authorization(self):
        before=sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*'))
        self.run_install()
        self.assertEqual(sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*')),before)
        self.assertEqual(self.native.events,[])
        self.assertEqual(self.config.read_text(),'{"locale":"en-US","other":"keep"}')

    def test_apply_without_token_never_copies_quits_or_changes_app(self):
        with self.assertRaises(ValueError):self.run_install(apply=True)
        self.assertEqual(self.native.events,[])
        self.assertEqual((self.paths.installed/'Contents/MacOS/Claude').read_bytes(),b'old')

    def test_verified_same_volume_copy_precedes_quit_atomic_exchange_and_single_start(self):
        result=self.run_install(apply=True,authorization=self.token())
        self.assertEqual(self.native.events,['copy','quit','exchange','start'])
        self.assertEqual(result['phase'],'started_pending_acceptance')
        self.assertEqual((self.paths.installed/'Contents/MacOS/Claude').read_bytes(),b'new')
        self.assertEqual(json.loads(self.config.read_text()),{'locale':'zh-CN','other':'keep'})
        receipt=json.loads((self.paths.recovery/'install-receipt.json').read_text())
        self.assertTrue(receipt['launch_submitted']);self.assertEqual(receipt['launch_count'],1)
        with self.assertRaises(ValueError):self.run_install(apply=True,authorization=self.token())
        self.assertEqual(self.native.events.count('start'),1)
        self.assertEqual((self.messages/'draft').read_text(),'keep draft')

    def test_bad_candidate_or_backup_is_rejected_before_quit(self):
        token=self.token();(self.paths.candidate/'Contents/Resources/app.asar').write_bytes(b'changed')
        with self.assertRaises(ValueError):self.run_install(apply=True,authorization=token)
        self.assertEqual(self.native.events,[])
        (self.backup/'Contents/MacOS/Claude').write_bytes(b'changed')
        with self.assertRaises(ValueError):self.run_install()

    def test_unmounted_wrong_private_mount_and_bad_material_fail_closed(self):
        self.native.connected=False
        with self.assertRaises(ValueError):self.run_install()
        self.native.connected=True
        (self.paths.recovery/'protected-state.json').write_bytes(b'changed')
        with self.assertRaises(ValueError):self.run_install()
        self.assertEqual(self.native.events,[])

    def test_copy_corruption_and_cross_volume_staging_never_quit(self):
        self.native.mutate_copy=True
        with self.assertRaises(ValueError):self.run_install(apply=True,authorization=self.token())
        self.assertNotIn('quit',self.native.events)

    def test_cross_device_check_and_occupied_slot_never_quit(self):
        self.native.cross_device=True
        with self.assertRaises(ValueError):self.run_install(apply=True,authorization=self.token())
        self.assertNotIn('quit',self.native.events)

    def test_launch_unknown_has_durable_receipt_and_is_not_replayed(self):
        self.native.start_error=True
        with self.assertRaises(OSError):self.run_install(apply=True,authorization=self.token())
        state=json.loads((self.paths.recovery/'install-receipt.json').read_text())
        self.assertTrue(state['launch_submitted']);self.assertEqual(state['launch_count'],1)
        self.assertEqual(state['phase'],'manual_recovery_required')
        self.assertEqual(self.native.events.count('start'),1)

    def test_new_user_data_refuses_rollback_before_quit_or_exchange(self):
        self.native.new_data=True;self.run_install(apply=True,authorization=self.token())
        events=list(self.native.events)
        with self.assertRaises(ValueError):self.token('rollback')
        self.assertEqual(self.native.events,events)
        self.assertEqual((self.messages/'new-session').read_text(),'new user data')

    def test_no_new_data_rollback_restores_app_locale_conditionally_without_second_launch(self):
        self.run_install(apply=True,authorization=self.token())
        result=self.m.rollback(self.paths,self.native,self.profile,apply=True,authorization=self.token('rollback'))
        self.assertEqual(result['phase'],'rolled_back_stopped')
        self.assertEqual((self.paths.installed/'Contents/MacOS/Claude').read_bytes(),b'old')
        self.assertEqual(json.loads(self.config.read_text()),{'locale':'en-US','other':'keep'})
        self.assertEqual(self.native.events.count('start'),1)

    def test_user_locale_change_blocks_stale_rollback_and_is_never_overwritten(self):
        self.run_install(apply=True,authorization=self.token());self.config.write_text('{"locale":"zh-Hans","other":"user-new"}')
        with self.assertRaises(ValueError):self.token('rollback')
        self.assertEqual(json.loads(self.config.read_text())['other'],'user-new')

    def test_token_expiry_tamper_and_permission_are_rejected_before_mutation(self):
        p=self.token();data=json.loads(p.read_text());data['expires_at']=1;self.write(p,data)
        with self.assertRaises(ValueError):self.run_install(apply=True,authorization=p)
        p=self.token();p.chmod(0o644)
        with self.assertRaises(ValueError):self.run_install(apply=True,authorization=p)
        self.assertEqual(self.native.events,[])

    def test_private_receipt_and_material_symlinks_are_rejected(self):
        p=self.paths.recovery/'protected-state.json';p.unlink();p.symlink_to(self.config)
        with self.assertRaises(ValueError):self.run_install()
        self.assertEqual(self.native.events,[])

    def test_invalid_locale_json_is_a_prequit_preflight_failure(self):
        self.config.write_text('not JSON')
        with self.assertRaises(ValueError):self.run_install()
        self.assertEqual(self.native.events,[])

    def test_malformed_runtime_evidence_is_not_marked_started(self):
        self.native.observe=lambda p:True
        with self.assertRaises(ValueError):self.run_install(apply=True,authorization=self.token())
        state=json.loads((self.paths.recovery/'install-receipt.json').read_text())
        self.assertEqual(state['phase'],'manual_recovery_required')
        self.assertEqual(self.native.events.count('start'),1)

    def test_new_data_on_quit_blocks_rollback_with_persistent_manual_receipt(self):
        self.run_install(apply=True,authorization=self.token());token=self.token('rollback')
        quit=self.native.quit
        def writing_quit(p):quit(p);(self.messages/'shutdown-save').write_text('newest')
        self.native.quit=writing_quit
        with self.assertRaises(ValueError):self.m.rollback(self.paths,self.native,self.profile,apply=True,authorization=token)
        self.assertEqual(self.native.events.count('exchange'),1)
        self.assertEqual((self.messages/'shutdown-save').read_text(),'newest')
        self.assertEqual(json.loads((self.paths.recovery/'install-receipt.json').read_text())['phase'],'manual_recovery_required')

    def test_real_lock_conflict_refuses_second_writer_before_copy(self):
        token=self.token()
        with self.m.locked(self.paths):
            with self.assertRaises(BlockingIOError):self.run_install(apply=True,authorization=token)
        self.assertEqual(self.native.events,[])

    def test_real_staging_conflict_is_not_deleted(self):
        stage=self.paths.installed.with_name('.Claude-5.1231.0-'+('a'*32)+'.app');stage.mkdir()
        (stage/'unique').write_text('preserve')
        with self.assertRaises(ValueError):self.run_install()
        self.assertEqual((stage/'unique').read_text(),'preserve')
        self.assertEqual(self.native.events,[])

    def test_source_or_settings_mutation_during_copy_never_quits(self):
        token=self.token();copy=self.native.copy
        def changing_copy(a,b):copy(a,b);self.config.write_text('{"locale":"zh-Hans","other":"new user choice"}')
        self.native.copy=changing_copy
        with self.assertRaises(ValueError):self.run_install(apply=True,authorization=token)
        self.assertNotIn('quit',self.native.events)
        self.assertEqual(json.loads(self.config.read_text())['other'],'new user choice')

    def test_chinese_locale_and_keychain_contents_are_preserved(self):
        self.config.write_text('{"locale":"zh-Hans","other":"keep"}');original=self.config.read_bytes()
        keys=self.paths.home/'Library/Keychains';keys.mkdir(parents=True);key=keys/'fixture.keychain-db';key.write_bytes(b'synthetic not-a-real-keychain')
        real_open=Path.open
        def no_credential_read(path,*args,**kw):
            if path.is_relative_to(keys):raise AssertionError('credential content opened')
            return real_open(path,*args,**kw)
        with patch.object(Path,'open',no_credential_read):
            self.run_install(apply=True,authorization=self.token())
        self.assertEqual(self.config.read_bytes(),original)
        self.assertEqual(key.read_bytes(),b'synthetic not-a-real-keychain')

    def test_policy_change_after_start_prevents_rollback(self):
        policy=self.paths.home/'.claude/cowork-policy.json';policy.parent.mkdir();policy.write_text('{"synthetic":"initial"}')
        self.run_install(apply=True,authorization=self.token());policy.write_text('{"synthetic":"new selection"}')
        with self.assertRaises(ValueError):self.token('rollback')
        self.assertEqual(policy.read_text(),'{"synthetic":"new selection"}')

    def test_receipt_pin_and_wrong_mount_uuid_fail_before_quit(self):
        p=self.paths.candidate.parent/'source-resource-manifest.json';p.write_bytes(b'fixture receipt')
        self.profile['receipts']={p.name:self.m.file_hash(p)};token=self.token();p.write_bytes(b'tampered')
        with self.assertRaises(ValueError):self.run_install(apply=True,authorization=token)
        self.assertEqual(self.native.events,[])
        self.native.mounts=lambda p:{'external_uuid':'external-fixture','private_uuid':'wrong','private_image_verified':True}
        with self.assertRaises(ValueError):self.run_install()

    def test_current_state_is_never_restored_from_encrypted_snapshot(self):
        self.run_install(apply=True,authorization=self.token())
        (self.messages/'current').write_text('latest data')
        with self.assertRaises(ValueError):self.m.rollback(self.paths,self.native,self.profile,apply=True,authorization=None)
        self.assertEqual((self.messages/'current').read_text(),'latest data')

    def test_expiry_during_staging_is_rechecked_before_quit(self):
        token=self.token();copy=self.native.copy
        def expire(a,b):
            copy(a,b);data=json.loads(token.read_text());data['expires_at']=1;self.write(token,data)
        self.native.copy=expire
        with self.assertRaises(ValueError):self.run_install(apply=True,authorization=token)
        self.assertNotIn('quit',self.native.events)

    def test_restore_material_changes_after_quit_prevent_exchange(self):
        token=self.token();quit=self.native.quit
        def changed(p):quit(p);(self.paths.recovery/'protected-state.json').write_bytes(b'changed while quitting')
        self.native.quit=changed
        with self.assertRaises(ValueError):self.run_install(apply=True,authorization=token)
        self.assertNotIn('exchange',self.native.events)
        self.assertEqual((self.paths.installed/'Contents/MacOS/Claude').read_bytes(),b'old')

    def test_atomic_exchange_failure_retains_both_apps_and_durable_manual_receipt(self):
        token=self.token()
        def failed(a,b):raise OSError('synthetic atomic failure')
        self.native.exchange=failed
        with self.assertRaises(OSError):self.run_install(apply=True,authorization=token)
        self.assertEqual((self.paths.installed/'Contents/MacOS/Claude').read_bytes(),b'old')
        self.assertEqual((self.paths.installed.with_name('.Claude-5.1231.0-'+('a'*32)+'.app')/'Contents/MacOS/Claude').read_bytes(),b'new')
        self.assertEqual(json.loads((self.paths.recovery/'install-receipt.json').read_text())['phase'],'manual_recovery_required')
        self.assertNotIn('start',self.native.events)

    def test_conditional_locale_restore_does_not_overwrite_intervening_edit(self):
        self.run_install(apply=True,authorization=self.token())
        journal=json.loads((self.paths.recovery/'install-receipt.json').read_text())['locale_journal']
        self.config.write_text('{"locale":"fr-FR","other":"new"}')
        with self.assertRaises(ValueError):self.m.restore_locale(self.paths,journal)
        self.assertEqual(json.loads(self.config.read_text()),{'locale':'fr-FR','other':'new'})

    def test_wrong_inode_runtime_evidence_and_repeated_apply_never_relaunch(self):
        token=self.token();self.native.observe=lambda p:{'pid':12345,'executable_inode':-1}
        with self.assertRaises(ValueError):self.run_install(apply=True,authorization=token)
        with self.assertRaises(ValueError):self.run_install(apply=True,authorization=token)
        self.assertEqual(self.native.events.count('start'),1)

    def test_os_mount_attestation_requires_real_mount_expected_backing_and_encryption(self):
        native=self.m.Native()
        def output(args,**kw):
            if 'diskutil' in args[0]:
                root=Path(args[-1]);value={'MountPoint':str(root),'VolumeUUID':'external-fixture' if root==self.paths.external else 'private-fixture'}
            elif args[1]=='info':
                value={'images':[{'image-path':str(self.m.IMAGE),'image-encrypted':True,'system-entities':[{'mount-point':str(self.paths.private)}]}]}
            else:value={'Properties':{'Encrypted':True}}
            return SimpleNamespace(stdout=plistlib.dumps(value))
        with patch.object(self.m.os.path,'ismount',return_value=True),patch.object(native,'run',side_effect=output):
            mounts=native.mounts(self.paths)
            self.assertEqual(mounts,{'external_uuid':'external-fixture','private_uuid':'private-fixture','private_image_verified':True})
        with patch.object(self.m.os.path,'ismount',return_value=False):
            with self.assertRaises(ValueError):native.mounts(self.paths)
        def unencrypted(args,**kw):
            if args[1]=='info' and 'hdiutil' in args[0]:
                return SimpleNamespace(stdout=plistlib.dumps({'images':[{'image-path':str(self.m.IMAGE),'image-encrypted':False,'system-entities':[{'mount-point':str(self.paths.private)}]}]}))
            return output(args,**kw)
        with patch.object(self.m.os.path,'ismount',return_value=True),patch.object(native,'run',side_effect=unencrypted):
            with self.assertRaises(ValueError):native.mounts(self.paths)

    def test_mounted_image_encryption_is_attested_without_imageinfo_or_unlock(self):
        native=self.m.Native()
        def output(args,**kw):
            if args[0].endswith('diskutil'):
                root=Path(args[-1]);return SimpleNamespace(stdout=plistlib.dumps({'MountPoint':str(root),'VolumeUUID':'fixture'}))
            if args[1]!='info':raise AssertionError('must not scan imageinfo or unlock an already mounted image')
            return SimpleNamespace(stdout=plistlib.dumps({'images':[{'image-path':str(self.m.IMAGE),'image-encrypted':True,'system-entities':[{'mount-point':str(self.paths.private)}]}]}))
        with patch.object(self.m.os.path,'ismount',return_value=True),patch.object(native,'run',side_effect=output):
            self.assertTrue(native.mounts(self.paths)['private_image_verified'])

    def test_valid_internal_bundle_links_survive_verified_copy(self):
        framework=self.paths.candidate/'Contents/Frameworks/Test.framework/Versions';(framework/'A').mkdir(parents=True)
        (framework/'A/data').write_bytes(b'linked content');(framework/'Current').symlink_to('A',target_is_directory=True)
        self.profile['bundle_sha256']=self.m.tree_hash(self.paths.candidate)
        self.run_install(apply=True,authorization=self.token())
        self.assertEqual(os.readlink(self.paths.installed/'Contents/Frameworks/Test.framework/Versions/Current'),'A')

    def test_unknown_native_process_status_is_not_treated_as_stopped(self):
        for result in [SimpleNamespace(returncode=0,stdout=b'',stderr=b''),
                       SimpleNamespace(returncode=2,stdout=b'',stderr=b'uncertain'),
                       SimpleNamespace(returncode=0,stdout=b'0\n',stderr=b'')]:
            with patch.object(self.m.subprocess,'run',return_value=result):
                with self.assertRaises(ValueError):self.m.Native().stopped(self.paths.installed)

    def test_known_npm_link_is_preserved_and_target_edit_changes_state(self):
        root=self.paths.home/'.claude/plugins/claude-hud/node_modules'
        (root/'.bin').mkdir(parents=True);(root/'semver/bin').mkdir(parents=True)
        target=root/'semver/bin/semver.js';target.write_text('initial')
        link=root/'.bin/semver';link.symlink_to('../semver/bin/semver.js')
        before=self.m.state_hash(self.paths);target.write_text('new plugin edit')
        self.assertNotEqual(self.m.state_hash(self.paths),before)
        self.assertTrue(link.is_symlink())
        link.unlink();link.symlink_to('/unknown/target')
        with self.assertRaises(ValueError):self.m.state_hash(self.paths)

    def test_known_external_relay_target_is_tracked_not_ignored(self):
        target=self.paths.home/'Library/Mobile Documents/com~apple~CloudDocs/ClaudeRelay/relay-cards';target.mkdir(parents=True)
        (self.paths.home/'.claude').mkdir();link=self.paths.home/'.claude/relay-cards';link.symlink_to(target)
        before=self.m.state_hash(self.paths);(target/'new-card.md').write_text('new user card')
        self.assertNotEqual(self.m.state_hash(self.paths),before)
        link.unlink();link.symlink_to(self.messages)
        with self.assertRaises(ValueError):self.m.state_hash(self.paths)

    def test_known_vm_link_requires_mounted_archive_and_tracks_vm_writes(self):
        target=self.paths.private/'ClaudeCowork/VM基础镜像候选/rootfs.img';target.parent.mkdir(parents=True);target.write_bytes(b'synthetic VM')
        link=self.paths.home/'Library/Application Support/Claude-3p/vm_bundles/claudevm.bundle/rootfs.img';link.parent.mkdir(parents=True);link.symlink_to(target)
        with patch.object(self.m.os.path,'ismount',return_value=True):
            before=self.m.state_hash(self.paths);target.write_bytes(b'new synthetic VM state')
            self.assertNotEqual(self.m.state_hash(self.paths),before)
        with patch.object(self.m.os.path,'ismount',return_value=False):
            with self.assertRaises(ValueError):self.m.state_hash(self.paths)

    def test_only_exact_browser_cache_roots_and_diagnostic_alias_are_excluded(self):
        base=self.config.parent;cache=base/'GPUCache';cache.mkdir();(cache/'data').write_bytes(b'cache')
        third=self.paths.home/'Library/Application Support/Claude-3p';third.mkdir()
        (third/'latest').symlink_to(third/'native-compaction-diagnostic.log')
        before=self.m.state_hash(self.paths)
        (cache/'new-cache').write_text('regenerated');(third/'native-compaction-diagnostic.log').write_text('diagnostic')
        self.assertEqual(self.m.state_hash(self.paths),before)
        (self.messages/'GPUCache').mkdir();before=self.m.state_hash(self.paths)
        (self.messages/'GPUCache/user-note').write_text('unique user text')
        self.assertNotEqual(self.m.state_hash(self.paths),before)

    def test_unknown_links_special_nodes_and_new_schema_are_not_exempted(self):
        link=self.messages/'unknown';link.symlink_to(self.config)
        with self.assertRaises(ValueError):self.m.state_hash(self.paths)
        link.unlink();fifo=self.messages/'unknown-fifo';os.mkfifo(fifo)
        with self.assertRaises(ValueError):self.m.state_hash(self.paths)
        fifo.unlink();before=self.m.state_hash(self.paths);(self.messages/'new-schema.sqlite').write_bytes(b'new schema')
        self.assertNotEqual(self.m.state_hash(self.paths),before)

    def test_keychain_db_and_temporary_looking_files_remain_protected(self):
        keys=self.paths.home/'Library/Keychains';keys.mkdir(parents=True)
        for name in ['login.keychain-db','login.keychain-db-wal','.flSynthetic','login.keychain-db.sb-synthetic']:
            before=self.m.state_hash(self.paths);(keys/name).write_bytes(b'synthetic opaque bytes')
            self.assertNotEqual(self.m.state_hash(self.paths),before)

    def test_composed_gstack_alias_is_exact_and_retargeting_is_rejected(self):
        root=self.paths.home/'.claude/skills';target=root/'gstack/open-gstack-browser/SKILL.md';target.parent.mkdir(parents=True);target.write_text('skill source')
        alias=root/'gstack/connect-chrome';alias.symlink_to('open-gstack-browser',target_is_directory=True)
        link=root/'connect-chrome/SKILL.md';link.parent.mkdir();link.symlink_to(root/'gstack/connect-chrome/SKILL.md')
        before=self.m.state_hash(self.paths);target.write_text('new skill source')
        self.assertNotEqual(self.m.state_hash(self.paths),before)
        alias.unlink();alias.symlink_to('/unknown/new-target')
        with self.assertRaises(ValueError):self.m.state_hash(self.paths)

    def test_observed_dangling_legacy_skill_link_is_preserved_and_materialization_is_new_state(self):
        root=self.paths.home/'.claude/skills';root.mkdir(parents=True)
        link=root/'using-coze-cli';link.symlink_to('../../.agents/skills/using-coze-cli')
        before=self.m.state_hash(self.paths)
        target=self.paths.home/'.agents/skills/using-coze-cli';target.mkdir(parents=True);(target/'SKILL.md').write_text('newly installed skill')
        self.assertNotEqual(self.m.state_hash(self.paths),before)
        self.assertTrue(link.is_symlink())

    def test_valid_singleton_ipc_churn_is_not_user_state_but_persistent_retarget_is_rejected(self):
        temporary=self.root/'darwin-temp';endpoint=temporary/'.org.chromium.Chromium.synthetic/SingletonSocket';endpoint.parent.mkdir(parents=True)
        sock=socket.socket(socket.AF_UNIX);self.addCleanup(sock.close)
        cwd=os.getcwd()
        try:os.chdir(endpoint.parent);sock.bind('SingletonSocket')
        finally:os.chdir(cwd)
        base=self.config.parent;(base/'SingletonSocket').symlink_to(endpoint)
        (base/'SingletonCookie').symlink_to('synthetic-nonce-not-a-real-cookie')
        (base/'SingletonLock').symlink_to('synthetic-host-123')
        with patch.object(self.m.os,'confstr',return_value=str(temporary)):
            before=self.m.state_hash(self.paths)
            (base/'SingletonCookie').unlink();(base/'SingletonCookie').symlink_to('synthetic-next-nonce')
            self.assertEqual(self.m.state_hash(self.paths),before)
            (base/'SingletonCookie').unlink();(base/'SingletonCookie').symlink_to(self.config)
            with self.assertRaises(ValueError):self.m.state_hash(self.paths)

    def test_cache_exemption_cannot_hide_unknown_links(self):
        cache=self.config.parent/'Code Cache';cache.mkdir();(cache/'unexpected').symlink_to(self.config)
        with self.assertRaises(ValueError):self.m.state_hash(self.paths)

    def test_preparation_generates_standard_material_and_permission_without_handmade_ciphertext(self):
        p=replace(self.paths,recovery=self.paths.private/'fresh-recovery')
        plan=self.m.prepare_recovery(p,self.native,self.profile)
        self.assertFalse(p.recovery.exists())
        grant=self.m.grant(p,self.native,self.profile,operation='prepare',expected_plan=plan['plan_sha256'],apply=True)
        result=self.m.prepare_recovery(p,self.native,self.profile,apply=True,authorization=Path(grant['authorization']))
        self.assertEqual(result['phase'],'recovery_prepared')
        manifest=json.loads((p.recovery/'recovery.json').read_text())
        self.assertEqual(manifest['schema'],2)
        self.assertEqual(manifest['kind'],'app-and-metadata-only-no-state-restore')
        self.assertTrue((p.recovery/'previous.app/Contents/Info.plist').is_file())
        self.assertEqual(json.loads((p.recovery/'protected-state.json').read_text())['kind'],'metadata-only-no-credential-content')
        self.assertFalse((p.recovery/'state.enc').exists())
        self.assertNotIn('quit',self.native.events);self.assertNotIn('start',self.native.events)

    def test_prepare_refuses_state_race_and_does_not_claim_recovery_ready(self):
        p=replace(self.paths,recovery=self.paths.private/'racing-recovery')
        plan=self.m.prepare_recovery(p,self.native,self.profile)
        grant=self.m.grant(p,self.native,self.profile,operation='prepare',expected_plan=plan['plan_sha256'],apply=True)
        copy=self.native.copy
        def changing(a,b):copy(a,b);(self.messages/'new-during-prepare').write_text('newest')
        self.native.copy=changing
        with self.assertRaises(ValueError):self.m.prepare_recovery(p,self.native,self.profile,apply=True,authorization=Path(grant['authorization']))
        self.assertFalse((p.recovery/'recovery.json').exists())
        self.assertEqual((self.messages/'new-during-prepare').read_text(),'newest')

    def test_grant_is_explicit_and_does_not_self_authorize_on_plain_apply(self):
        p=replace(self.paths,recovery=self.paths.private/'ungranted')
        with self.assertRaises(ValueError):self.m.grant(p,self.native,self.profile,operation='prepare',expected_plan='0'*64,apply=True)
        self.assertFalse(p.recovery.exists())
        with self.assertRaises(ValueError):self.m.prepare_recovery(p,self.native,self.profile,apply=True)
        self.assertFalse(p.recovery.exists())

    def boundary_locale_edit(self):
        real_replace=self.m.os.replace
        def edit():self.config.write_text('{"locale":"fr-FR","other":"new-user-choice"}')
        def replaced(a,b,*args,**kw):
            if Path(b)==self.config and Path(a).name.startswith('.locale-'):edit()
            return real_replace(a,b,*args,**kw)
        def swapped(afd,a,bfd,b):
            if b==self.config.name:edit()
            return self.swap_at(afd,a,bfd,b)
        return patch.object(self.m.os,'replace',side_effect=replaced),patch.object(self.m,'_swap_at',side_effect=swapped,create=True)

    def retained_configs(self):
        candidates=[self.config,*self.config.parent.glob('.locale-*/*')]
        result=[]
        for p in candidates:
            if p.is_file():
                try:result.append(json.loads(p.read_bytes()))
                except (ValueError,UnicodeError):pass
        return result

    def test_locale_syscall_race_retains_both_versions_manual_and_no_launch(self):
        token=self.token();replace_patch,swap_patch=self.boundary_locale_edit()
        with replace_patch,swap_patch:
            with self.assertRaises(ValueError):self.run_install(apply=True,authorization=token)
        versions=self.retained_configs()
        self.assertIn({'locale':'fr-FR','other':'new-user-choice'},versions)
        self.assertIn({'locale':'zh-CN','other':'keep'},versions)
        state=json.loads((self.paths.recovery/'install-receipt.json').read_text())
        self.assertEqual(state['phase'],'manual_recovery_required')
        self.assertNotIn('start',self.native.events)

    def test_rollback_locale_syscall_race_preserves_new_edit_manual_without_relaunch(self):
        self.run_install(apply=True,authorization=self.token());token=self.token('rollback')
        replace_patch,swap_patch=self.boundary_locale_edit()
        with replace_patch,swap_patch:
            with self.assertRaises(ValueError):self.m.rollback(self.paths,self.native,self.profile,apply=True,authorization=token)
        self.assertIn({'locale':'fr-FR','other':'new-user-choice'},self.retained_configs())
        self.assertEqual(json.loads((self.paths.recovery/'install-receipt.json').read_text())['phase'],'manual_recovery_required')
        self.assertEqual(self.native.events.count('start'),1)

    def test_copy_boundary_detach_never_recreates_private_path_and_records_on_bound_volume(self):
        p=replace(self.paths,recovery=self.paths.private/'disconnect-slot')
        plan=self.m.prepare_recovery(p,self.native,self.profile)
        grant=self.m.grant(p,self.native,self.profile,operation='prepare',expected_plan=plan['plan_sha256'],apply=True)
        copy=self.native.copy;parked=self.root/'detached-private'
        def detach(a,b):
            os.rename(self.paths.private,parked);self.paths.private.mkdir(mode=0o700);self.native.connected=False
            copy(a,b)
        self.native.copy=detach
        with self.assertRaises(ValueError):self.m.prepare_recovery(p,self.native,self.profile,apply=True,authorization=Path(grant['authorization']))
        self.assertFalse(p.recovery.exists())
        self.assertEqual(list(self.paths.private.iterdir()),[])
        failure=json.loads((parked/'disconnect-slot/prepare-failure.json').read_text())
        self.assertEqual(failure['phase'],'manual_recovery_required')
        self.assertNotIn('quit',self.native.events)

    def test_native_copy_never_creates_missing_destination_parent(self):
        target=self.root/'missing/recovery/previous.app'
        with self.assertRaises((ValueError,FileNotFoundError)):
            self.m.Native().copy(self.paths.installed,target)
        self.assertFalse(target.parent.exists())
