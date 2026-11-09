#!/usr/bin/env python3
"""Version-bound, session-scoped SDK compaction option hook.

Stage a clone of the currently installed app. This never edits user histories,
provider profiles, API credentials, or the running app in place.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import struct
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from upstream_patch_lib import (
    align4, asar_entry, build_asar, read_asar_entry, read_asar_header,
    resign_app, update_electron_asar_integrity,
    verify_electron_asar_integrity, walk_asar_entries,
)
from update_guard import require_official_signature

MARKER = 'local-auto-compact-policy:v1'
ANCHOR = 't.Yut.info("[HostLoop] sdkOptions after patch: %o",'
BINDING = 'async function Ah(e,r){let{sessionId:a,'
ENTRY = '.vite/build/index.chunk-Bvm2vgWR.js'
NEW_ENTRY = '.vite/build/index.chunk-D6Y0BUdo.js'
NEW_OFFICIAL_CHUNK_SHA256 = '5af1d0d66409c1558186a748fcb7dd02505f65ea97c04f9927fb43ccbc691b10'
NEW_ANCHOR = 't.tvt.info("[HostLoop] sdkOptions after patch: %o",'
NEW_BINDING = 'async function eh(e,r){let{sessionId:i,'
LATEST_ENTRY = '.vite/build/index.chunk-vznsWXEK.js'
LATEST_OFFICIAL_CHUNK_SHA256 = 'ebe8305f396e1f8e9fb8d8f22c749ae8b4eddedb75c5e684d1c2f621e21e1c35'
LATEST_BINDING = 'async function th(e,r){let{sessionId:i,'


def patch_host_loop_source(source, module_path, policy_path, session_id, *, version='4.4212.0'):
    if version == '4.4212.0':
        anchor, binding, owner, logger = ANCHOR, BINDING, 'a', 't.Yut'
    elif version == '4.23118.0':
        anchor, binding, owner, logger = NEW_ANCHOR, NEW_BINDING, 'i', 't.tvt'
        if MARKER not in source and hashlib.sha256(source.encode()).hexdigest() != NEW_OFFICIAL_CHUNK_SHA256:
            raise ValueError('Unknown official HostLoop source fingerprint')
    elif version == '4.23118.1':
        anchor, binding, owner, logger = NEW_ANCHOR, LATEST_BINDING, 'i', 't.tvt'
        if MARKER not in source and hashlib.sha256(source.encode()).hexdigest() != LATEST_OFFICIAL_CHUNK_SHA256:
            raise ValueError('Unknown official HostLoop source fingerprint')
    else:
        raise ValueError('Unsupported official Claude build for session-scoped compaction')
    if MARKER in source:
        if source.count(MARKER) == 1 and all(json.dumps(x) in source for x in [module_path, policy_path, session_id]):
            return source
        raise ValueError('Existing compaction hook has a different scope')
    if source.count(anchor) != 1:
        raise ValueError('Expected exactly one HostLoop option log anchor')
    if binding not in source:
        raise ValueError('Unsupported HostLoop session/SDK option binding')
    hook = (
        f'/*{MARKER}*/{owner}==={json.dumps(session_id)}&&(await(async()=>{{'
        'try{const{pathToFileURL}=await import("node:url");'
        f'const m=await import(pathToFileURL({json.dumps(module_path)}).href);'
        f'const d=(m.default??m).applyCompactionPolicy(e,{{sessionId:{owner},policyPath:{json.dumps(policy_path)}}});'
        f'{logger}.info("[LocalAutoCompact] policy %o",d)'
        f'}}catch(z){{{logger}.warn("[LocalAutoCompact] policy unavailable (%s); native defaults retained",z?.name??"Error")}}'
        '})()),'
    )
    return source.replace(anchor, hook + anchor, 1)


def replace_asar_entry(data, target, replacement):
    old_header_size, _, header = read_asar_header(data, Path('existing.asar'))
    entry = asar_entry(header, target)
    if entry is None or entry.get('unpacked') or 'offset' not in entry:
        raise ValueError('Target is not an ordinary packed ASAR entry')
    entries = sorted(
        [(name, item, int(item['offset']), int(item['size']))
         for name, item in walk_asar_entries(header)
         if not item.get('unpacked') and 'offset' in item],
        key=lambda item: (item[2], item[0]),
    )
    chunks = []
    offset = 0
    for name, item, old_offset, size in entries:
        start = 8 + old_header_size + old_offset
        if start + size > len(data):
            raise ValueError('Existing archive contains an out-of-bounds entry')
        content = replacement if name == target else data[start:start + size]
        item['offset'] = str(offset)
        item['size'] = len(content)
        if name == target:
            block_size = item.get('integrity', {}).get('blockSize', 4194304)
            item['integrity'] = {
                'algorithm': 'SHA256',
                'hash': hashlib.sha256(content).hexdigest(),
                'blockSize': block_size,
                'blocks': [hashlib.sha256(content[i:i + block_size]).hexdigest()
                           for i in range(0, len(content), block_size)] or [hashlib.sha256(b'').hexdigest()],
            }
        offset += len(content)
        chunks.append(content)
    header_bytes = json.dumps(header, separators=(',', ':'), ensure_ascii=False).encode()
    payload_size = align4(4 + len(header_bytes))
    header_size = 4 + payload_size
    return (
        struct.pack('<IIII', 4, header_size, payload_size, len(header_bytes))
        + header_bytes + b'\0' * (payload_size - 4 - len(header_bytes))
        + b''.join(chunks)
    )


def stage_app(source_app, destination, module_path, policy_path, session_id, expected_version):
    if expected_version not in ('4.4212.0', '4.23118.0', '4.23118.1'):
        raise ValueError('Unsupported official Claude build for session-scoped compaction')
    info = plistlib.loads((source_app / 'Contents/Info.plist').read_bytes())
    version = info.get('CFBundleShortVersionString')
    if version != expected_version:
        raise ValueError(f'Installed app version changed: expected {expected_version}, got {version}')
    if destination.exists():
        raise ValueError('Staging destination already exists; refusing to overwrite it')
    if expected_version in ('4.23118.0', '4.23118.1'):
        require_official_signature(source_app)
    target_entry = {
        '4.4212.0': ENTRY,
        '4.23118.0': NEW_ENTRY,
        '4.23118.1': LATEST_ENTRY,
    }[expected_version]
    original = (source_app / 'Contents/Resources/app.asar').read_bytes()
    hs, _, header = read_asar_header(original, source_app / 'Contents/Resources/app.asar')
    js = read_asar_entry(original, hs, header, target_entry)
    if js is None:
        raise ValueError('Unsupported app build: HostLoop entry is missing')
    patched_js = patch_host_loop_source(js.decode(), str(module_path), str(policy_path),
                                       session_id, version=expected_version).encode()
    replacement = replace_asar_entry(original, target_entry, patched_js)
    new_hs, _, new_header = read_asar_header(replacement, Path('staged.asar'))
    changed = []
    for name, old_entry in walk_asar_entries(header):
        if old_entry.get('unpacked') or 'offset' not in old_entry:
            continue
        old = read_asar_entry(original, hs, header, name)
        new = read_asar_entry(replacement, new_hs, new_header, name)
        if old != new:
            changed.append(name)
    if changed != [target_entry]:
        raise ValueError(f'Archive edit exceeded scope: {changed}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    syntax_path = destination.parent / 'host-loop-auto-compact-staged.js'
    syntax_path.write_bytes(patched_js)
    acorn = Path(__file__).resolve().parents[1] / 'node_modules/acorn'
    validator = 'const fs=require("fs"),a=require(process.argv[1]);a.parse(fs.readFileSync(process.argv[2],"utf8"),{ecmaVersion:"latest",sourceType:"module"});'
    subprocess.run(['node', '-e', validator, str(acorn), str(syntax_path)], check=True)
    subprocess.run(['cp', '-cR', str(source_app), str(destination)], check=True)
    archive_path = destination / 'Contents/Resources/app.asar'
    archive_path.write_bytes(replacement)
    update_electron_asar_integrity(destination, 'Resources/app.asar', archive_path)
    verify_electron_asar_integrity(destination)
    resign_app(destination)
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(destination)], check=True)
    receipt = {
        'version': version, 'session_id': session_id, 'changed_archive_entries': changed,
        'original_archive_sha256': hashlib.sha256(original).hexdigest(),
        'staged_archive_sha256': hashlib.sha256(replacement).hexdigest(),
        'staged_app': str(destination), 'module_path': str(module_path), 'policy_path': str(policy_path),
    }
    (destination.parent / 'automatic-compaction-stage.json').write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-app', type=Path, required=True)
    parser.add_argument('--stage-app', type=Path, required=True)
    parser.add_argument('--module', type=Path, required=True)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--session', required=True)
    parser.add_argument('--expected-version', required=True)
    args = parser.parse_args()
    stage_app(args.source_app, args.stage_app, args.module, args.policy, args.session, args.expected_version)


if __name__ == '__main__':
    main()
