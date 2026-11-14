#!/usr/bin/env python3
"""Attest a current-version optional Cowork source adapter without installing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import review_claude_5114925 as current
from upstream_patch_lib import read_asar_entry, read_asar_header

ROOT = Path(__file__).resolve().parents[1]
APP = Path('/Applications/Claude.app')
TARGET = ROOT / 'resources/candidates/claude-desktop-5.11492.5/cowork-reviewed.json'
OWNER = '.vite/build/index.chunk-xV3fJhc7.js'
WORKER = '.vite/build/index.chunk-BqZKACTO.js'
OWNER_SHA = '6b4474d1c0e0c0f0a6dc0c92c0205994b2e91d2073b457a28db3cbe199b45c95'
WORKER_SHA = 'be53e2ca65f39dae7c3cbb7358d309cb27be2a1f6a93c46e9f63d4b97dfb9939'
GETTER = 'function c9r(){let e=Gl()?.autoCompactWindow;return typeof e=="number"&&e>0?e:void 0}'
EXPORT = 'Object.defineProperty(exports,"aU",{enumerable:!0,get:function(){return c9r}})'
PROPAGATION = '...t.aU()&&{CLAUDE_CODE_AUTO_COMPACT_WINDOW:String(t.aU())}'


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def native_contract(owner: str, worker: str) -> bool:
    if (owner.count(GETTER) != 1 or owner.count(EXPORT) != 1
            or worker.count(PROPAGATION) != 1):
        raise ValueError('current native Cowork compaction dataflow changed')
    return True


def review(app: Path = APP) -> dict:
    report, _ = current.review(app)
    archive = app / 'Contents/Resources/app.asar'
    raw = archive.read_bytes()
    offset, _, header = read_asar_header(raw, archive)
    owner = read_asar_entry(raw, offset, header, OWNER)
    worker = read_asar_entry(raw, offset, header, WORKER)
    if (owner is None or worker is None or digest(owner) != OWNER_SHA
            or digest(worker) != WORKER_SHA):
        raise ValueError('current signed Cowork resource identity differs')
    native_contract(owner.decode(), worker.decode())
    program = '''const fs=require('fs'),crypto=require('crypto');
const module=require(process.argv[1]);const src=fs.readFileSync(0,'utf8');
const out=module.patchPinned(src);
process.stdout.write(crypto.createHash('sha256').update(out).digest('hex'));'''
    patch = subprocess.run(['node', '-e', program,
                            str(ROOT / 'scripts/cowork_5114925.cjs')],
                           input=worker.decode(), capture_output=True, text=True,
                           timeout=100, check=False)
    if patch.returncode or len(patch.stdout.strip()) != 64:
        raise ValueError('new-version Cowork hook refused signed source')
    if current.update_guard.bundle_identity(app)['archives'] != report['source_archives_sha256']:
        raise ValueError('current official app changed during Cowork review')
    policy = ROOT / 'scripts/cowork_compaction_policy.cjs'
    return {'version': current.VERSION, 'status': 'review_required_not_installable',
            'source_archives_sha256': report['source_archives_sha256'],
            'owner': {'path': OWNER, 'sha256': OWNER_SHA},
            'worker': {'path': WORKER, 'sha256': WORKER_SHA,
                       'patched_sha256': patch.stdout.strip()},
            'policy_module_sha256': digest(policy.read_bytes()),
            'native_global_scope_unmodified': True,
            'per_session_policy_requires_explicit_configuration': True,
            'installed': False, 'visual_ui_verified': False}


if __name__ == '__main__':
    result = review()
    if TARGET.exists() or TARGET.is_symlink():
        raise SystemExit('current-version Cowork review already exists')
    with TARGET.open('x') as output:
        output.write(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'owner_sha256': OWNER_SHA,
                      'worker_sha256': WORKER_SHA,
                      'patched_sha256': result['worker']['patched_sha256'],
                      'installed': False, 'visual_ui_verified': False}))
