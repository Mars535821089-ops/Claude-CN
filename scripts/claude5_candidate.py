#!/usr/bin/env python3
"""Offline Claude 5.1231.2 source preparation. Never installs or starts an app.

Keep the 4.x installers and their trust boundaries unchanged. Incomplete source
packs are useful review artifacts, NOT installable releases.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import stat
import subprocess

from patch_whitelist_423118 import _safe_path, verify_source, apply_prepared
from upstream_patch_lib import (BASE_LANGUAGE_LIST, LANG_LIST_RE, read_asar_header,
                                read_asar_entry, verify_electron_asar_integrity)

ROOT = Path(__file__).resolve().parents[1]
VERSION = '5.1231.2'
CANDIDATE = ROOT / 'resources/candidates/claude-desktop-5.1231.2'
BASE = Path('/Volumes/Mars-私人服务器/内置盘写入统一管理/01-编译与构建/Codex-provider-switch/claude5-1231-2-20261111')
ARCHIVES = {
    'app.asar': '5e71c28e6e8e21f028811212144feebe45e6346b60645a25e2f1b6a72186999b',
    'ion-dist.asar': 'd7957c54e70073b6a143e09421d456e268a4fc7cb600bf9bfec791c99c7cdaf4',
}
GATES = {
    'assets/v1/c6fce776d-CpCXDKwF.js': 'c594fe011b8866c551ca4408d5b2e3a9534d34314788166f0157220dde878c73',
    'assets/v1/shared-130-EowXggL2.js': '8449e57046fd4d9d89646b8ed3333ef87cd888bf641f63853e2c57e08b09b87c',
    'assets/v1/shared-25-R__OiE7w.js': '64454e6fe9fd23a1f6781d5be977e9cf097822a18141a2a5addf6ec48b152d9d',
}
PATHS = {'desktop': ('app.asar', 'resources/i18n/en-US.json'),
         'frontend': ('ion-dist.asar', 'i18n/en-US.json'),
         'dynamic': ('ion-dist.asar', 'i18n/dynamic/en-US.json')}
ASSESSMENT_HASHES = {
    'ui-rule-discovery.json': '19a0795cc6c0dfcfda2da433020437ce402908c06609f011a5a3ef9eb80d5314',
    'migration-preview/report.json': '31eab553db516a439f827bf723379a486eb97383aceda860bde301ffdb4bba88',
    'migration-preview/desktop-translations.json': '0850b1727b7e19d1130870a173c78000e7e6b9acd9e26af08fcbe4148391bd05',
    'migration-preview/frontend-translations.json': '40b787e5ccee385658de7eaade15ee10871ba782786802a7079b080231492177',
    'migration-preview/dynamic-translations.json': '34708a083e6a535314c85b254018b954f6cb764c600eaf3df823de179f426c35',
}

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def load(path):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError('duplicate JSON key: ' + key)
            out[key] = value
        return out
    return json.loads(path.read_text(), object_pairs_hook=unique)

def prepare_gates(root, specs):
    if root.is_symlink() or not root.is_dir() or set(specs) != set(GATES):
        raise ValueError('all three reviewed gates required')
    edits, states = [], set()
    for name, spec in sorted(specs.items()):
        path = _safe_path(root, name)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('gate must be an independent regular file')
        raw = path.read_bytes()
        if spec.get('locales') == ['zh-CN', 'zh-Hans']:
            digest = sha(raw)
            if digest == spec['patched_sha256']:
                after = None
            else:
                if digest != spec['sha256']:
                    raise ValueError('reviewed gate fingerprint changed')
                text, count = LANG_LIST_RE.subn(BASE_LANGUAGE_LIST + ',"zh-CN","zh-Hans"]',raw.decode())
                after = text.encode()
                if count != 1 or sha(after) != spec['patched_sha256']:
                    raise ValueError('dual alias count or fingerprint changed')
        else:
            after = verify_source(raw, spec)
        states.add(after is None)
        if after is not None:
            edits.append((path, raw, after))
    if len(states) != 1:
        raise ValueError('partial gate migration')
    return edits

def merge_authored(source, base, authored):
    if not base.keys() <= source.keys():
        raise ValueError('migration source IDs changed')
    gaps = {key: value for key, value in source.items() if key not in base}
    if not authored.keys() <= set(gaps.values()):
        raise ValueError('authored text is stale or overwrites resolved/official Chinese')
    result = dict(base)
    for key, english in list(gaps.items()):
        if english not in authored:
            continue
        chinese = authored[english]
        if not isinstance(chinese, str) or not chinese or chinese == english:
            raise ValueError('English fallback is not a translation')
        result[key] = chinese
        del gaps[key]
    return result, gaps

def require_complete(missing):
    if any(missing.values()):
        raise ValueError('missing Chinese translations; refusing release packaging')

def read_source(app):
    if app.is_symlink():
        raise ValueError('redirected source app')
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    if (info.get('CFBundleShortVersionString') != VERSION or
            info.get('CFBundleIdentifier') != 'com.anthropic.claudefordesktop'):
        raise ValueError('unknown source identity')
    archives = {}
    for name, digest in ARCHIVES.items():
        path = app / 'Contents/Resources' / name
        raw = path.read_bytes()
        if path.is_symlink() or sha(raw) != digest:
            raise ValueError('unknown official source archive: ' + name)
        size, _, header = read_asar_header(raw, path)
        archives[name] = (raw, size, header)
    verify_electron_asar_integrity(app)
    def entry(archive, path):
        raw, size, header = archives[archive]
        data = read_asar_entry(raw, size, header, path)
        if data is None:
            raise ValueError('missing source entry: ' + path)
        return data
    return entry

def prepare(app=BASE / 'Claude.app', assessment=BASE, candidate=CANDIDATE):
    if VERSION == '5.1231.2':
        from prepare_512312 import prepare_from_full06
        return prepare_from_full06(app, candidate)
    entry = read_source(app)
    for name, expected in ASSESSMENT_HASHES.items():
        if sha((assessment / name).read_bytes()) != expected:
            raise ValueError('assessment fingerprint changed: ' + name)
    report = load(assessment / 'migration-preview/report.json')
    discovery = load(assessment / 'ui-rule-discovery.json')
    if report['version'] != VERSION:
        raise ValueError('wrong assessment version')
    authored = load(candidate / 'authored-zh-CN.json')
    if authored['version'] != VERSION:
        raise ValueError('wrong authored version')
    checks, gaps, manifests = {}, {}, {}
    outputs = {}
    for group, (archive, path) in PATHS.items():
        raw = entry(archive, path)
        english = json.loads(raw)
        basepath = assessment / f'migration-preview/{group}-translations.json'
        base = load(basepath)
        expected_gaps = load(assessment / f'migration-preview/{group}-missing.json')
        if expected_gaps != {k: v for k, v in english.items() if k not in base}:
            raise ValueError('assessment source text mismatch')
        if group == 'desktop':
            official = json.loads(entry(archive, 'resources/i18n/zh-Hans.json'))
            if any(base.get(k) != v for k, v in official.items()):
                raise ValueError('official Chinese was overwritten')
        additions = dict(authored['catalogs'].get(group, {}))
        if group == 'frontend':
            for batch in sorted(candidate.glob('frontend-authored*.json')):
                by_id = load(batch)
                if by_id['source_sha256'] != sha(raw):
                    raise ValueError('ID translation source fingerprint changed')
                for key, text in by_id['translations'].items():
                    if key not in expected_gaps:
                        raise ValueError('ID addition overwrites resolved Chinese')
                    source_text = english[key]
                    if source_text in additions and additions[source_text] != text:
                        raise ValueError('conflicting exact-English additions')
                    additions[source_text] = text
        translated, missing = merge_authored(english, base, additions)
        provenance = load(assessment / f'migration-preview/{group}-provenance.json')
        provenance.update({k: 'authored_exact_english' for k in translated if k not in base})
        if group == 'desktop':
            normalized = load(candidate / 'official-icu-normalized.json')
            if len(normalized) != 23 or not normalized.keys() <= official.keys():
                raise ValueError('official ICU normalization scope changed')
            translated.update(normalized)
            provenance.update({k: 'official_wording_icu_structure_normalized' for k in normalized})
            outputs['desktop-official-zh-Hans.json'] = official
        checks[group] = {'en': {k: english[k] for k in translated}, 'zh': translated,
                         'strictSelections': True}
        gaps[group] = missing
        outputs[f'{group}-zh-CN.json'] = translated
        outputs[f'{group}-missing.json'] = missing
        outputs[f'{group}-provenance.json'] = provenance
        manifests[group] = {'source_messages': len(english), 'translated': len(translated),
                            'missing': len(missing), 'source_sha256': sha(raw),
                            'assessment_translations_sha256': sha(basepath.read_bytes())}
    validation = subprocess.run(['node', str(ROOT / 'scripts/audit_messages.cjs')],
        input=json.dumps({'catalogs': checks}, ensure_ascii=False), text=True,
        capture_output=True, timeout=180, check=False)
    audit = json.loads(validation.stdout)
    if validation.returncode or audit['errors']:
        raise ValueError('ICU validation failed: ' + validation.stdout)
    gates = {}
    for name, digest in GATES.items():
        raw = entry('ion-dist.asar', name)
        patched, count = LANG_LIST_RE.subn(BASE_LANGUAGE_LIST + ',"zh-CN","zh-Hans"]', raw.decode())
        if sha(raw) != digest or count != 1:
            raise ValueError('language gate source mismatch')
        gates[name] = {'sha256': digest, 'patched_sha256': sha(patched.encode()), 'expected': 1,
                      'locales':['zh-CN','zh-Hans']}
    ui = {}
    for checked in report['ui_in_memory']:
        row = next(r for r in discovery if r['file'] == checked['file'] and r['all_match'])
        archive, path = row['file'].split('/', 1)
        if sha(entry(archive, path)) != checked['source_sha256']:
            raise ValueError('UI source mismatch')
        ui[row['file']] = {'sha256': row['sha256'], 'patched_sha256': checked['patched_sha256'],
                          'rules': [{k: r[k] for k in ('context', 'source', 'expected')} for r in row['rules']]}
    outputs['hardcoded-ui-reviewed.json'] = {'version': VERSION, 'source_archives': ARCHIVES, 'files': ui}
    outputs['language-whitelist-reviewed.json'] = {'version': VERSION, 'files': gates}
    frontend_zh = outputs['frontend-zh-CN.json']
    new_chat = []
    for name, ids in {
        'assets/v1/index-C5hoiU2R.js': ['knhVwvlQOb'],
        'assets/v1/shared-130-o9Pr1Pt_.js': ['knhVwvlQOb'],
        'assets/v1/shared-6-CUEhov0g.js': ['qCRVxuNbN3'],
    }.items():
        raw = entry('ion-dist.asar', name)
        text = raw.decode()
        for key in ids:
            if text.count('id:"' + key + '"') != 1 or key not in frontend_zh:
                raise ValueError('new-chat catalog binding changed')
        new_chat.append({'file': name, 'source_sha256': sha(raw),
                         'catalog_ids': {k: frontend_zh[k] for k in ids}})
    outputs['new-chat-evidence.json'] = {
        'catalog_bindings': new_chat, 'visual_verified': False,
        'old_children_rule': 'not transplanted; new ComparisonRoute has no matching literal',
        'remaining_fallback': 'shared-29-BBxMe69G.js has New chat/New task title fallback; not yet reauthored',
        'gated_publisher': 'index.chunk-CInLNSAV.js has empty zh-Hans and no zh-CN alias; separate coverage still required',
    }
    outputs['manifest.json'] = {'target_version': VERSION, 'source_archives': ARCHIVES,
        'status': 'incomplete_source_candidate' if any(gaps.values()) else 'source_candidate_not_gui_verified',
        'catalogs': manifests, 'icu_validation': audit, 'installed': False, 'visual_ui_verified': False,
        'cowork': 'native_equivalence_unverified_hook_not_removed_or_ported',
        'sidebar': 'lifecycle_unverified', 'new_chat_anchor': 'catalog_bindings_verified_fallback_still_pending'}
    outputs['manifest.json']['assessment_inputs_sha256'] = ASSESSMENT_HASHES
    import claude5_compat
    outputs.update(claude5_compat.prepare(entry, candidate, outputs))
    outputs['new-chat-evidence.json'].update(
        remaining_fallback='shared-29-BBxMe69G.js title fallback patched with pinned source/output hashes',
        gated_publisher='112 messages embedded for zh-CN and zh-Hans; pinned output verified, no GUI acceptance')
    outputs['manifest.json']['compatibility_validation'] = outputs['compatibility-reviewed.json']['validation']
    outputs['manifest.json']['catalogs']['gated'] = {'source_messages':len(outputs['gated-en-US.json']),
        'translated':len(outputs['gated-zh-CN.json']), 'missing':0}
    outputs['manifest.json'].update(cowork='optional_session_policy_adapter_native_scope_not_equivalent',
        sidebar='source_lifecycle_patch_ready_GUI_pending',new_chat_anchor='catalog_and_title_fallback_patched')
    outputs['manifest.json']['authored_inputs_sha256'] = {
        name: sha((candidate / name).read_bytes()) for name in
        ['authored-zh-CN.json', 'official-icu-normalized.json','gated-authored-zh-CN.json',
         *[p.name for p in sorted(candidate.glob('frontend-authored*.json'))]]}
    outputs['manifest.json']['generated_files_sha256'] = {
        name: sha((json.dumps(obj, ensure_ascii=False, indent=2) + '\n').encode())
        for name, obj in outputs.items() if name != 'manifest.json'}
    for name, obj in outputs.items():
        (candidate / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')
    return outputs['manifest.json']

if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--official-app', type=Path, default=BASE / 'Claude.app')
    cli.add_argument('--assessment', type=Path, default=BASE)
    args = cli.parse_args()
    if not os.path.ismount('/Volumes/Mars-私人服务器'):
        raise SystemExit('external build volume unmounted')
    print(json.dumps(prepare(args.official_app, args.assessment), ensure_ascii=False))
