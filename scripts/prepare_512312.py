"""Point-release preparation using verified full-06 dictionaries, never real home."""
import json
from pathlib import Path
import shutil
import subprocess

import claude5_candidate as c
import claude5_compat as compat
from upstream_patch_lib import read_asar_header, read_asar_entry

OLD_BASE = Path('/Volumes/Mars-私人服务器/内置盘写入统一管理/01-编译与构建/Codex-provider-switch/claude5-compat-20261111')


def reuse_catalog(previous_english, current_english, previous_chinese):
    if previous_english != current_english or previous_chinese.keys() != current_english.keys():
        raise ValueError('point-release English or Chinese coverage changed; explicit authoring required')
    return dict(previous_chinese)


def copy_source_inputs(old_pack,candidate):
    # A derived install profile belongs to its signed generation, never to the
    # reusable translation input pack. Only a NEW slot may be populated here.
    return shutil.copytree(old_pack,candidate,ignore=lambda directory,names:
        {'install-profile.json'} if Path(directory)==old_pack else set())


def prepare_from_full06(app, candidate):
    if candidate != c.CANDIDATE or candidate.exists():
        raise ValueError('fresh isolated .2 resource slot required')
    entry = c.read_source(app)
    old_pack = c.ROOT / 'resources/candidates/claude-desktop-5.1231.0'
    baseline_raw = (OLD_BASE / 'implementation-01/full-06/source-resource-manifest.json').read_bytes()
    baseline = json.loads(baseline_raw)
    old_receipt = c.load(OLD_BASE / 'implementation-01/full-06/engineering-receipt.json')
    if (baseline['target_version'] != '5.1231.0'
        or c.sha(baseline_raw) != old_receipt['resource_manifest_sha256']
        or c.sha((old_pack / 'manifest.json').read_bytes()) != c.sha(baseline_raw)):
        raise ValueError('full-06 baseline provenance mismatch')
    for name, digest in {**baseline['generated_files_sha256'], **baseline['authored_inputs_sha256']}.items():
        if Path(name).name != name or c.sha((old_pack / name).read_bytes()) != digest:
            raise ValueError('reviewed baseline resource changed')
    old_archives = {}
    for name, digest in baseline['source_archives'].items():
        path = OLD_BASE / 'Claude.app/Contents/Resources' / name
        raw = path.read_bytes()
        if c.sha(raw) != digest:
            raise ValueError('old official English source changed')
        size, _, header = read_asar_header(raw, path)
        old_archives[name] = raw, size, header
    outputs, catalogs, checks = {}, {}, {}
    for group, (archive, name) in c.PATHS.items():
        english_raw = entry(archive, name)
        english = json.loads(english_raw)
        previous = json.loads(read_asar_entry(*old_archives[archive], name))
        chinese = reuse_catalog(previous, english, c.load(old_pack / f'{group}-zh-CN.json'))
        outputs[f'{group}-zh-CN.json'] = chinese
        outputs[f'{group}-missing.json'] = {}
        outputs[f'{group}-provenance.json'] = c.load(old_pack / f'{group}-provenance.json')
        catalogs[group] = {'source_messages': len(english), 'translated': len(chinese), 'missing': 0,
            'source_sha256': c.sha(english_raw), 'baseline_reviewed_translation_sha256': c.sha((old_pack / f'{group}-zh-CN.json').read_bytes())}
        checks[group] = {'en': english, 'zh': chinese, 'strictSelections': True}
    official = json.loads(entry('app.asar', 'resources/i18n/zh-Hans.json'))
    if official != c.load(old_pack / 'desktop-official-zh-Hans.json'):
        raise ValueError('official Chinese changed; review ICU normalizations before reuse')
    outputs['desktop-official-zh-Hans.json'] = official
    validation = subprocess.run(['node', str(c.ROOT / 'scripts/audit_messages.cjs')],
        input=json.dumps({'catalogs': checks}), text=True, capture_output=True, check=True, timeout=180)
    audit = json.loads(validation.stdout)
    if audit['errors']:
        raise ValueError('ordinary ICU validation failed')
    review = c.load(c.BASE / 'target-review.json')
    if set(review['gates']) != set(c.GATES) or any(review['gates'][p]['sha256'] != h for p,h in c.GATES.items()):
        raise ValueError('reviewed gate pins differ from fixed .2 inputs')
    outputs['language-whitelist-reviewed.json'] = {'version': c.VERSION, 'files': review['gates']}
    translations = {**c.load(c.ROOT / 'resources/ui-reviewed-zh-CN.json'),
                    **c.load(c.ROOT / 'resources/candidates/claude-desktop-4.23118.1/hardcoded-ui-zh-CN.json')}
    payload = [{'file': name, 'source': entry(*name.split('/', 1)).decode(),
                'rules': spec['rules']} for name, spec in review['ui'].items()]
    program = '''const fs=require('fs'),crypto=require('crypto'),u=require(process.argv[1]);
const p=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(p.items.map(x=>{
const out=u.applyRules(x.source,x.file,x.rules,p.translations).source;
return crypto.createHash('sha256').update(out).digest('hex')})));'''
    patched = subprocess.run(['node', '-e', program, str(c.ROOT / 'scripts/ui_localization.cjs')],
        input=json.dumps({'items': payload, 'translations': translations}), text=True, capture_output=True, check=True, timeout=90)
    hashes = json.loads(patched.stdout)
    if len(hashes) != len(payload):
        raise ValueError('UI outputs missing')
    ui = {item['file']: {'sha256': c.sha(item['source'].encode()), 'patched_sha256': digest, 'rules': item['rules']}
          for item, digest in zip(payload, hashes, strict=True)}
    if sum(r['expected'] for s in ui.values() for r in s['rules']) != 71:
        raise ValueError('full-06 reviewed UI scope changed')
    outputs['hardcoded-ui-reviewed.json'] = {'version': c.VERSION, 'source_archives': c.ARCHIVES, 'files': ui}
    evidence = []
    for name, message in [('assets/v1/index-m4Lmn-QT.js', 'knhVwvlQOb'),
                          ('assets/v1/shared-130-EowXggL2.js', 'knhVwvlQOb'),
                          ('assets/v1/shared-6-wiyzoaNH.js', 'qCRVxuNbN3')]:
        raw = entry('ion-dist.asar', name)
        if raw.decode().count('id:"' + message + '"') != 1:
            raise ValueError('new-chat native catalog binding changed')
        evidence.append({'file': name, 'source_sha256': c.sha(raw), 'catalog_ids': {message: outputs['frontend-zh-CN.json'][message]}})
    outputs['new-chat-evidence.json'] = {'catalog_bindings': evidence, 'visual_verified': False,
        'old_children_rule': 'not transplanted; catalog-based new UI retained',
        'remaining_fallback': compat.TARGETS['fallback'][1] + ' narrowly patched',
        'gated_publisher': '112 unchanged English messages; both Chinese aliases'}
    # Only generate after ordinary dictionaries and every UI/new-chat anchor pass.
    copy_source_inputs(old_pack, candidate)
    gated = c.load(candidate / 'gated-authored-zh-CN.json')
    gated['source_sha256'] = compat.TARGETS['gated'][2]
    (candidate / 'gated-authored-zh-CN.json').write_text(json.dumps(gated, ensure_ascii=False, indent=2) + '\n')
    outputs.update(compat.prepare(entry, candidate, outputs))
    catalogs['gated'] = {'source_messages': len(outputs['gated-en-US.json']), 'translated': len(outputs['gated-zh-CN.json']), 'missing': 0}
    manifest = {'target_version': c.VERSION, 'source_archives': c.ARCHIVES,
        'status': 'source_candidate_not_gui_verified', 'catalogs': catalogs,
        'icu_validation': audit, 'installed': False, 'visual_ui_verified': False,
        'cowork': 'optional_session_policy_adapter_native_scope_not_equivalent',
        'sidebar': 'source_lifecycle_patch_ready_GUI_pending', 'new_chat_anchor': 'catalog_and_title_fallback_patched',
        'compatibility_validation': outputs['compatibility-reviewed.json']['validation'],
        'baseline_full06_resource_manifest_sha256': c.sha(baseline_raw),
        'target_review_sha256': c.sha((c.BASE / 'target-review.json').read_bytes()),
        'source_diff_sha256': c.sha((c.BASE / 'source-diff.json').read_bytes()),
        'authored_inputs_sha256': {name: c.sha((candidate / name).read_bytes()) for name in baseline['authored_inputs_sha256']}}
    for name, value in outputs.items():
        (candidate / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    manifest['generated_files_sha256'] = {name: c.sha((candidate / name).read_bytes()) for name in outputs}
    (candidate / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return manifest
