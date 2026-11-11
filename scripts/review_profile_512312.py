"""Fixed .2 review profile only; no install/grant/restore CLI or controller."""
from pathlib import Path
import claude5_candidate as c
import candidate_validation_512312 as validation
import install_51231 as existing

RECEIPTS={'engineering-receipt.json','candidate-validation-record.json','source-resource-manifest.json'}


def _receipt_hashes(app):
    return {name:c.sha(validation._bytes(app.parent/name)) for name in RECEIPTS}


def build_profile(app):
    app=Path(app).absolute()
    current=validation.read_current(app)
    return {'schema':2,'kind':'claude-5.1231.2-install-review-profile','version':'5.1231.2',
        'bundle_id':'com.anthropic.claudefordesktop','tree_algorithm':'claude-cn-install-tree-v1',
        'bundle_sha256':current['final_tree_sha256'],'archives':dict(current['candidate_asar_sha256']),
        'receipts':_receipt_hashes(app),'candidate_status':'awaiting_install_acceptance',
        'install_authorized':False,'business_recovery_verified':False,'native_restore_verified':False}


def verify_profile(app,native,profile):
    app=Path(app).absolute()
    if (not isinstance(profile,dict) or type(profile.get('schema')) is not int or profile['schema']!=2
        or profile.get('kind')!='claude-5.1231.2-install-review-profile' or profile.get('version')!='5.1231.2'
        or c.VERSION!='5.1231.2' or profile.get('bundle_id')!='com.anthropic.claudefordesktop'
        or profile.get('tree_algorithm')!='claude-cn-install-tree-v1'
        or profile.get('candidate_status')!='awaiting_install_acceptance'
        or any(profile.get(k) is not False for k in ('install_authorized','business_recovery_verified','native_restore_verified'))
        or not isinstance(profile.get('archives'),dict) or set(profile['archives'])!=set(c.ARCHIVES)
        or not isinstance(profile.get('receipts'),dict) or set(profile['receipts'])!=RECEIPTS):
        raise ValueError('not the fixed .2 review profile; no install authority')
    profile={**profile,'archives':dict(profile['archives']),'receipts':dict(profile['receipts'])}
    if _receipt_hashes(app)!=profile['receipts']:raise ValueError('review receipt pins changed')
    # Reuse original strict bundle identity, post-sign ASAR and canonical tree
    # verifier. The .0 CLI/defaults/authorization and 4.x guards remain untouched.
    existing.verify_candidate(app,native,profile)
    current=validation.read_current(app)
    if (current['final_tree_sha256']!=profile['bundle_sha256']
        or current['candidate_asar_sha256']!=profile['archives']
        or _receipt_hashes(app)!=profile['receipts']):
        raise ValueError('review profile/candidate changed at native boundary')
    return {'status':'awaiting_install_acceptance','candidate_verified':True,
            'install_authorized':False,'business_recovery_verified':False,'native_restore_verified':False}
