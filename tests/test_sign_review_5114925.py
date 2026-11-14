"""A local signature must never be mislabeled as credential-compatible."""

import hashlib
import json
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import sign_review_5114925 as signer


class SignedCurrentClaudeTests(unittest.TestCase):
    def test_explicitly_reports_original_keychain_groups_cannot_be_retained(self):
        original={'com.apple.application-identifier':'TEAM.bundle',
                  'com.apple.developer.team-identifier':'TEAM',
                  'keychain-access-groups':['TEAM.group1','TEAM.group2'],
                  'com.apple.security.device.camera':True}
        result=signer.local_signing_impact(original)
        self.assertEqual(result['lost_keychain_group_count'],2)
        self.assertEqual(result['original_team_entitlement_present'],True)
        self.assertFalse(result['credential_continuity_proven'])
        self.assertEqual(result['local_entitlements']['com.apple.security.device.camera'],True)
        self.assertNotIn('keychain-access-groups',result['local_entitlements'])

    def test_signing_only_accepts_exact_unsigned_isolated_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            app=root/'implementation-fixture/Claude.app'
            resources=app/'Contents/Resources';resources.mkdir(parents=True)
            info={'CFBundleShortVersionString':'5.11492.5',
                  'CFBundleIdentifier':'com.anthropic.claudefordesktop'}
            (app/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
            shas={}
            for archive in ('app.asar','ion-dist.asar'):
                raw=archive.encode()
                (resources/archive).write_bytes(raw)
                shas[archive]=hashlib.sha256(raw).hexdigest()
            receipt={'version':'5.11492.5','status':'NONINSTALLABLE_UNSIGNED_CURRENT_CANDIDATE',
                     'installed':False,'signed':False,'candidate_asar_sha256':shas}
            path=app.parent/'engineering-receipt.json'
            path.write_text(json.dumps(receipt))
            with mock.patch.object(signer,'EXTERNAL',root), \
                 mock.patch.object(signer,'external_mounted',return_value=True):
                self.assertEqual(signer.preflight(app),receipt)
                with self.assertRaises(ValueError):
                    signer.preflight(Path('/Applications/Claude.app'))
                path.write_text(json.dumps({**receipt,'status':'ready_for_install'}))
                with self.assertRaises(ValueError):
                    signer.preflight(app)
                path.write_text(json.dumps(receipt))
                (resources/'app.asar').write_bytes(b'drift')
                with self.assertRaises(ValueError):
                    signer.preflight(app)


if __name__=='__main__':
    unittest.main()
