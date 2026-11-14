#!/usr/bin/env python3
"""Pin current official new-chat title fallback without patching the app."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import review_claude_5114925 as current
from upstream_patch_lib import read_frontend_entry

ROOT = Path(__file__).resolve().parents[1]
APP = Path('/Applications/Claude.app')
RESOURCE = 'assets/v1/shared-29-DpYwKb3M.js'
SOURCE_SHA = '17330f89022d40b4ff05d5894378fd4fdfb67a96a8a29dad2eb02e4c39f2597e'
TARGET = ROOT / 'resources/candidates/claude-desktop-5.11492.5/fallback-reviewed.json'


def review(app: Path = APP) -> dict:
    report, _ = current.review(app)
    raw = read_frontend_entry(app, RESOURCE)
    if raw is None or hashlib.sha256(raw).hexdigest() != SOURCE_SHA:
        raise ValueError('signed new-chat source fingerprint differs')
    program = '''const fs=require('fs'),crypto=require('crypto');
const src=fs.readFileSync(0,'utf8'),m=require(process.argv[1]);
process.stdout.write(crypto.createHash('sha256').update(m.patchPinned(src)).digest('hex'));'''
    result = subprocess.run(['node', '-e', program,
                             str(ROOT / 'scripts/fallback_5114925.cjs')],
                            input=raw.decode(), capture_output=True, text=True,
                            timeout=80, check=False)
    if result.returncode or len(result.stdout.strip()) != 64:
        raise ValueError('new-build chat/task fallback context changed')
    if current.update_guard.bundle_identity(app)['archives'] != report['source_archives_sha256']:
        raise ValueError('current official app changed during fallback review')
    return {'version': current.VERSION, 'status': 'review_required_not_installable',
            'source_archives_sha256': report['source_archives_sha256'],
            'resource': RESOURCE, 'source_sha256': SOURCE_SHA,
            'patched_sha256': result.stdout.strip(),
            'explicit_and_saved_titles_unchanged': True,
            'installed': False, 'visual_ui_verified': False}


if __name__ == '__main__':
    receipt = review()
    if TARGET.exists() or TARGET.is_symlink():
        raise SystemExit('current fallback review already exists')
    with TARGET.open('x') as output:
        output.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'status': receipt['status'],
                      'source_sha256': SOURCE_SHA,
                      'patched_sha256': receipt['patched_sha256'],
                      'installed': False, 'visual_ui_verified': False}))
