import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import sign_review_51231 as s

class SigningReviewTests(unittest.TestCase):
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
