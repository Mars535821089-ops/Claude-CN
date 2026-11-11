#!/usr/bin/env python3
"""Fixed full-06 controlled cutover. Default read-only; no download or credentials.

Only --apply plus a private, expiring authorization bound to the preflight plan
can mutate anything. User state is NEVER restored from a snapshot by this tool.
"""
from __future__ import annotations
import argparse
import contextlib
import contextvars
import ctypes
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import time
import uuid

from install_current import digest
from repair_macos_429 import locale_config_paths
from upstream_patch_lib import verify_electron_asar_integrity

ROOT=Path(__file__).resolve().parents[1]
EXTERNAL=Path('/Volumes/Mars-私人服务器')
BUILD=EXTERNAL/'内置盘写入统一管理/01-编译与构建/Codex-provider-switch/claude5-compat-20261111/implementation-01/full-06'
PRIVATE=Path.home()/'Library/Application Support/Mars Storage Maintenance/PrivateArchive'
IMAGE=EXTERNAL/'内置盘写入统一管理/08-隔离验证与回迁/私密历史产物加密仓/PrivateArtifacts.sparsebundle'
PROFILE=ROOT/'resources/candidates/claude-desktop-5.1231.0/install-profile.json'

@dataclass(frozen=True)
class Paths:
    candidate:Path
    installed:Path
    private:Path
    external:Path
    recovery:Path
    home:Path

def file_hash(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def safe(path):
    if not path.is_absolute() or '..' in path.parts or any(p.is_symlink() for p in (path,*path.parents)):
        raise ValueError('unsafe or redirected path')

def tree_hash(root):
    """Content/mode/link manifest; independent of volume, inode and copy time."""
    safe(root)
    if not root.is_dir():raise ValueError('bundle directory missing')
    rows=[]
    for parent,dirs,files in os.walk(root,followlinks=False):
        for name in sorted(dirs+files):
            p=Path(parent)/name;info=p.lstat();rel=p.relative_to(root).as_posix()
            if stat.S_ISLNK(info.st_mode):
                target=os.readlink(p)
                if not p.resolve().is_relative_to(root.resolve()):raise ValueError('bundle link escapes root')
                rows.append([rel,'link',target])
            elif stat.S_ISREG(info.st_mode):rows.append([rel,'file',stat.S_IMODE(info.st_mode),info.st_size,file_hash(p)])
            elif stat.S_ISDIR(info.st_mode):rows.append([rel,'dir',stat.S_IMODE(info.st_mode)])
            else:raise ValueError('special bundle entry')
    return digest(json.dumps(sorted(rows),ensure_ascii=False,separators=(',',':')).encode())

def private_dir(path):
    safe(path);s=path.stat()
    if not path.is_dir() or s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)!=0o700:
        raise ValueError('owner-only private directory required')

def private_file(path):
    safe(path);s=path.lstat()
    if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)!=0o600 or s.st_nlink!=1:
        raise ValueError('owner-only ordinary private file required')

def load(path):
    def unique(pairs):
        out={}
        for k,v in pairs:
            if k in out:raise ValueError('duplicate JSON key')
            out[k]=v
        return out
    return json.loads(path.read_bytes(),object_pairs_hook=unique)

class BoundDir:
    """Existing directory vnode, opened component-wise without following links."""
    def __init__(self,path,expected=None,private=False,fd=None):
        self.path=path;self.private=private
        if fd is None:
            if not path.is_absolute() or '..' in path.parts:raise ValueError('unsafe write parent')
            current=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
            try:
                for part in path.parts[1:]:
                    child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=current)
                    os.close(current);current=child
                fd=current;current=None
            finally:
                if current is not None:os.close(current)
        self.fd=fd;self.identity=self.key(os.fstat(fd))
        try:
            if expected is not None and list(self.identity)!=expected:raise ValueError('write parent generation changed')
            self.check()
        except BaseException:self.close();raise
    @staticmethod
    def key(s):return (s.st_dev,s.st_ino)
    def check(self,visible=True):
        s=os.fstat(self.fd)
        if self.key(s)!=self.identity or not stat.S_ISDIR(s.st_mode):raise ValueError('bound write parent changed')
        if self.private and (s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)!=0o700):raise ValueError('private write parent changed')
        if visible:
            try:current=self.path.lstat()
            except OSError as e:raise ValueError('write parent detached') from e
            if self.key(current)!=self.identity or stat.S_ISLNK(current.st_mode):raise ValueError('write parent detached or replaced')
    def child(self,name,create=False):
        if Path(name).name!=name:raise ValueError('unsafe child write parent')
        self.check()
        if create:os.mkdir(name,0o700,dir_fd=self.fd)
        fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=self.fd)
        return BoundDir(self.path/name,private=self.private,fd=fd)
    def close(self):
        if self.fd is not None:os.close(self.fd);self.fd=None
    def __enter__(self):return self
    def __exit__(self,*_):self.close()

_WRITES=contextvars.ContextVar('claude5_bound_write_parents',default=None)

def write_roots(p):
    return {str(x):list(BoundDir.key(x.stat())) for x in (p.private,p.external,p.installed.parent)}

def bound_parent(path):
    scope=_WRITES.get()
    if scope is None or path not in scope:raise ValueError('write requires an existing bound parent')
    return scope[path]

def check_writes():
    scope=_WRITES.get()
    if scope is None:raise ValueError('write scope absent')
    for parent in scope.values():parent.check()

def durable(path,data,*,detached=False):
    parent=bound_parent(path.parent)
    if detached:parent.check(visible=False)
    if not detached:
        try:check_writes()
        except (OSError,ValueError):
            durable(path,{**data,'phase':'manual_recovery_required','write_parent_detached':True},detached=True);raise
    try:s=os.stat(path.name,dir_fd=parent.fd,follow_symlinks=False)
    except FileNotFoundError:s=None
    if s is not None and (not stat.S_ISREG(s.st_mode) or s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)!=0o600 or s.st_nlink!=1):
        raise ValueError('private receipt replaced or redirected')
    temp='.'+path.name+'-'+uuid.uuid4().hex
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent.fd)
    with os.fdopen(fd,'wb') as f:f.write(json.dumps(data,sort_keys=True).encode()+b'\n');f.flush();os.fsync(f.fileno())
    os.replace(temp,path.name,src_dir_fd=parent.fd,dst_dir_fd=parent.fd);os.fsync(parent.fd)
    if not detached:
        try:check_writes()
        except (OSError,ValueError):
            durable(path,{**data,'phase':'manual_recovery_required','write_parent_detached':True},detached=True);raise

def _swap_at(afd,a,bfd,b):
    fn=ctypes.CDLL('/usr/lib/libSystem.B.dylib',use_errno=True).renameatx_np
    fn.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint];fn.restype=ctypes.c_int
    if fn(afd,os.fsencode(a),bfd,os.fsencode(b),2):raise OSError(ctypes.get_errno(),'atomic exchange failed')
    os.fsync(afd)
    if bfd!=afd:os.fsync(bfd)

def protected_roots(p):
    return [p.home/'Library/Application Support/Claude',p.home/'Library/Application Support/Claude-3p',
            p.home/'.claude',p.home/'Library/Preferences/com.anthropic.claudefordesktop.plist',
            p.home/'Library/Keychains']

def state_hash(p):
    """Metadata only. Known links are followed AND protected, never ignored.

    Exact Chromium-generated cache roots/validated singleton IPC and the known
    diagnostic alias are not user-state generations. Directory timestamps are
    not generations: adding an excluded cache cannot hide a new ordinary child,
    whose name/type/metadata is still recorded. Keychain contents are not opened.
    """
    rows=[];seen=set();home=p.home
    apps=[home/'Library/Application Support'/n for n in ('Claude','Claude-3p')]
    caches={'Cache','Code Cache','GPUCache','DawnGraphiteCache','DawnWebGPUCache',
            'GPUPersistentCache','GraphiteDawnCache','Crashpad'}
    def singleton_socket(link):
        if not link.is_symlink() or link.lstat().st_uid!=os.getuid():raise ValueError('unknown singleton socket')
        target=link.resolve(strict=True)
        temporary=Path(os.confstr('CS_DARWIN_USER_TEMP_DIR')).resolve(strict=True)
        if (not target.is_relative_to(temporary) or target.name!='SingletonSocket'
                or not target.parent.name.startswith('.org.chromium.Chromium.')
                or not stat.S_ISSOCK(target.stat().st_mode) or target.stat().st_uid!=os.getuid()):
            raise ValueError('unknown singleton socket target')
    def excluded(item,s):
        if item.parent in apps and item.name in caches:
            if not stat.S_ISDIR(s.st_mode):raise ValueError('cache root redirected or changed type')
            # Do not silently allow arbitrary links/special files hidden inside
            # a cache exemption. No cache file contents are read.
            for child in item.rglob('*'):
                mode=child.lstat().st_mode
                if not(stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                    raise ValueError('unknown link or special entry inside browser cache')
            return True
        third=apps[1];diagnostic=third/'native-compaction-diagnostic.log'
        if item==diagnostic:
            if not stat.S_ISREG(s.st_mode):raise ValueError('diagnostic file redirected')
            return True
        if item==third/'latest':
            if not stat.S_ISLNK(s.st_mode) or os.readlink(item)!=str(diagnostic):raise ValueError('unknown diagnostic alias')
            return True
        if item.parent in apps and item.name in ('SingletonCookie','SingletonLock','SingletonSocket'):
            if item.name=='SingletonSocket':singleton_socket(item)
            else:
                # NEVER read/hash the cookie or lock symlink's nonce value.
                # A real persistent target is not accepted as transient IPC.
                if not stat.S_ISLNK(s.st_mode) or s.st_uid!=os.getuid() or item.exists():raise ValueError('unknown singleton IPC target')
                singleton_socket(item.parent/'SingletonSocket')
            return True
        return False
    def visit(item,label):
        if item in seen:return
        seen.add(item);s=item.lstat()
        if excluded(item,s):return
        common=[label,s.st_mode,s.st_uid,s.st_gid,s.st_ino,s.st_dev]
        if stat.S_ISLNK(s.st_mode):
            target=known_state_link(p,item)
            rows.append(common+['link',os.readlink(item),s.st_mtime_ns,s.st_ctime_ns])
            if target.exists():visit(target,'approved-target:'+str(target))
            else:rows.append(['approved-target:'+str(target),'absent'])
            return
        if stat.S_ISDIR(s.st_mode):
            rows.append(common+['dir'])
            for child in sorted(item.iterdir()):visit(child,label+'/'+child.name)
        elif stat.S_ISREG(s.st_mode):rows.append(common+['file',s.st_size,s.st_mtime_ns,s.st_ctime_ns])
        else:raise ValueError('unknown special protected state entry: '+label)
    for index,root in enumerate(protected_roots(p)):
        safe(root)
        if not root.exists():rows.append([index,'absent']);continue
        visit(root,str(index))
    return digest(json.dumps(rows,separators=(',',':')).encode())

def known_state_link(p,item):
    """Explicit observed layout, not a general symlink allow/ignore switch."""
    home=p.home;rel=item.relative_to(home).as_posix();raw=os.readlink(item)
    lexical=Path(os.path.abspath(item.parent/raw));expected=None
    if rel=='.claude/relay-cards':
        expected=home/'Library/Mobile Documents/com~apple~CloudDocs/ClaudeRelay/relay-cards'
    elif rel=='.claude/skills/connect-chrome/SKILL.md':
        alias=home/'.claude/skills/gstack/connect-chrome'
        if raw!=str(alias/'SKILL.md') or not alias.is_symlink() or os.readlink(alias)!='open-gstack-browser':
            raise ValueError('unreviewed composed gstack alias')
        expected=home/'.claude/skills/gstack/open-gstack-browser/SKILL.md';safe(expected)
        if not expected.is_file():raise ValueError('known gstack target missing')
        return expected
    elif rel=='Library/Application Support/Claude-3p/vm_bundles/claudevm.bundle/rootfs.img':
        private=getattr(p,'private',home/'Library/Application Support/Mars Storage Maintenance/PrivateArchive')
        if not os.path.ismount(getattr(p,'external',EXTERNAL)) or not os.path.ismount(private):raise ValueError('VM archive mount unavailable')
        expected=private/'ClaudeCowork/VM基础镜像候选/rootfs.img'
    elif rel=='.claude/skills/gstack/connect-chrome' and raw=='open-gstack-browser':
        expected=home/'.claude/skills/gstack/open-gstack-browser'
    else:
        gstack=set('autoplan benchmark benchmark-models browse canary careful codex context-restore context-save cso design-consultation design-html design-review design-shotgun devex-review diagram document-generate document-release freeze gstack-upgrade guard health investigate ios-clean ios-design-review ios-fix ios-qa ios-sync land-and-deploy landing-report learn make-pdf office-hours open-gstack-browser pair-agent plan-ceo-review plan-design-review plan-devex-review plan-eng-review plan-tune qa qa-only retro review scrape setup-browser-cookies setup-deploy setup-gbrain ship skillify spec sync-gbrain unfreeze'.split())
        parts=Path(rel).parts
        if len(parts)==4 and parts[:2]==('.claude','skills') and parts[3] in ('SKILL.md','sections'):
            if parts[2] in gstack:expected=home/'.claude/skills/gstack'/parts[2]/parts[3]
            elif parts[2]=='_gstack-command' and parts[3]=='SKILL.md':expected=home/'.claude/skills/gstack/SKILL.md'
        agents=set('coze-agent-collaboration huashu-design pua using-coze-cli lark-approval lark-apps lark-attendance lark-base lark-calendar lark-contact lark-doc lark-drive lark-event lark-im lark-mail lark-markdown lark-minutes lark-okr lark-openapi-explorer lark-shared lark-sheets lark-skill-maker lark-slides lark-task lark-vc lark-vc-agent lark-whiteboard lark-wiki lark-workflow-meeting-summary lark-workflow-standup-report'.split())
        if len(parts)==3 and parts[:2]==('.claude','skills') and parts[2] in agents:
            expected=home/'.agents/skills'/parts[2]
        bins={'c8':'../c8/bin/c8.js','node-which':'../which/bin/node-which','semver':'../semver/bin/semver.js',
              'tsc':'../typescript/bin/tsc','tsserver':'../typescript/bin/tsserver','anthropic-ai-sdk':'../@anthropic-ai/sdk/bin/cli',
              'browsers':'../@puppeteer/browsers/lib/cjs/main-cli.js','escodegen':'../escodegen/bin/escodegen.js',
              'esgenerate':'../escodegen/bin/esgenerate.js','esparse':'../esprima/bin/esparse.js','esvalidate':'../esprima/bin/esvalidate.js',
              'extract-zip':'../extract-zip/cli.js','image-size':'../image-size/bin/image-size.js','marked':'../marked/bin/marked.js',
              'nanoid':'../nanoid/bin/nanoid.cjs','playwright':'../playwright/cli.js','playwright-core':'../playwright-core/cli.js'}
        if (rel.startswith(('.claude/plugins/claude-hud/node_modules/','.claude/skills/gstack/node_modules/'))
                and item.parent.name=='.bin' and item.parent.parent.name=='node_modules' and raw==bins.get(item.name)):
            expected=Path(os.path.abspath(item.parent/raw))
            if not expected.is_relative_to(item.parent.parent):raise ValueError('npm shim escapes package scope')
    if expected is None or lexical!=expected:raise ValueError('unreviewed protected symlink: '+rel)
    safe(expected)
    if expected.exists() and not(expected.is_dir() or expected.is_file()):raise ValueError('approved link target is special')
    if rel.endswith('/claudevm.bundle/rootfs.img') and not expected.is_file():raise ValueError('VM archive target unavailable')
    if rel=='.claude/relay-cards' and not expected.is_dir():raise ValueError('relay target unavailable')
    return expected

def stage_path(p,generation):return p.installed.with_name('.Claude-5.1231.0-'+generation+'.app')
def receipt_path(p):return p.recovery/'install-receipt.json'
def plan_hash(plan):return digest(json.dumps(plan,sort_keys=True,separators=(',',':')).encode())

def recovery_check(p,native):
    private_dir(p.private);private_dir(p.recovery)
    if not p.recovery.is_relative_to(p.private) or p.recovery==p.private:raise ValueError('recovery outside private archive')
    mounts=native.mounts(p)
    if mounts.get('private_image_verified') is not True:raise ValueError('private backing image not verified')
    f=p.recovery/'recovery.json';private_file(f);r=load(f)
    if (r.get('schema')!=2 or r.get('kind')!='app-and-metadata-only-no-state-restore'
            or not re.fullmatch('[a-f0-9]{32}',str(r.get('generation','')))
            or any(r.get(k)!=mounts.get(k) for k in ('external_uuid','private_uuid'))):
        raise ValueError('recovery generation/mount identity mismatch')
    if p.recovery.stat().st_dev!=p.private.stat().st_dev:raise ValueError('recovery is not on private encrypted volume')
    metadata=p.recovery/'protected-state.json';private_file(metadata)
    if file_hash(metadata)!=r.get('metadata_sha256'):raise ValueError('prepared metadata manifest changed')
    m=load(metadata)
    if (m.get('schema')!=1 or m.get('kind')!='metadata-only-no-credential-content'
            or m.get('guard')!='claude5-observed-layout-v2' or m.get('generation')!=r['generation']
            or m.get('roots')!=[str(x) for x in protected_roots(p)]
            or not re.fullmatch('[a-f0-9]{64}',str(m.get('state_fingerprint','')))):
        raise ValueError('prepared metadata scope is invalid')
    previous=p.recovery/'previous.app';native.verify(previous)
    if tree_hash(previous)!=r['previous_bundle_sha256']:raise ValueError('private previous app changed')
    return r,digest(f.read_bytes())

def verify_candidate(app,native,profile):
    safe(app);native.verify(app,profile['version'])
    if tree_hash(app)!=profile['bundle_sha256']:raise ValueError('candidate bundle content/mode/link changed')
    for name,expected in profile['archives'].items():
        if name not in ('app.asar','ion-dist.asar') or file_hash(app/'Contents/Resources'/name)!=expected:
            raise ValueError('post-sign ASAR changed')

def locale_preflight(p):
    for path in locale_config_paths(p.home):
        safe(path)
        if not path.exists():continue
        s=path.lstat()
        if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or s.st_size>4*1024*1024:
            raise ValueError('unsupported locale settings file')
        data=load(path)
        if not isinstance(data,dict) or 'locale' in data and data['locale'] is not None and not isinstance(data['locale'],str):
            raise ValueError('unsupported locale settings object')
        if not (isinstance(data.get('locale'),str) and data['locale'].lower().startswith('zh')) and not (
                os.access(path,os.W_OK) and os.access(path.parent,os.W_OK)):
            raise ValueError('locale update is not writable')

def preflight(p,native,profile,*,operation='install'):
    for path in (p.candidate,p.installed,p.external,p.installed.parent,p.home):safe(path)
    r,recovery_sha=recovery_check(p,native)
    for name,h in profile['receipts'].items():
        if Path(name).name!=name or file_hash(p.candidate.parent/name)!=h:raise ValueError('full-06 receipt changed')
    verify_candidate(p.candidate,native,profile)
    locale_preflight(p)
    native.verify(p.installed)
    current=tree_hash(p.installed);fingerprint=state_hash(p)
    stage=stage_path(p,r['generation']);safe(stage)
    if operation=='install':
        if receipt_path(p).exists() or receipt_path(p).is_symlink():raise ValueError('transaction already exists; no replay')
        if stage.exists() or stage.is_symlink():raise ValueError('same-volume staging slot occupied')
        if current!=r['previous_bundle_sha256']:raise ValueError('installed app does not match private recovery')
        if current==profile['bundle_sha256']:raise ValueError('candidate already installed')
    elif operation=='rollback':
        private_file(receipt_path(p));record=load(receipt_path(p))
        if (record.get('generation')!=r['generation'] or record.get('phase') not in
                ('started_pending_acceptance','manual_recovery_required','installed_not_started')
                or record.get('baseline_state')!=fingerprint or record.get('candidate_sha256')!=current
                or record.get('previous_sha256')!=r['previous_bundle_sha256']):
            raise ValueError('stale rollback or newer user state; manual reconciliation required')
        native.verify(stage)
        if tree_hash(stage)!=r['previous_bundle_sha256']:raise ValueError('old same-volume app changed')
    else:raise ValueError('unknown operation')
    return {'operation':operation,'generation':r['generation'],'candidate_sha256':profile['bundle_sha256'],
        'installed_sha256':current,'installed_inode':p.installed.stat().st_ino,
        'recovery_sha256':recovery_sha,'protected_state':fingerprint,
        'receipt_sha256':file_hash(receipt_path(p)) if operation=='rollback' else None,
        'source_receipts':profile['receipts'],'write_roots':write_roots(p)}

def authorize(p,path,plan):
    if path is None:raise ValueError('explicit private authorization required')
    path=Path(path);private_file(path)
    if not path.is_relative_to(p.recovery):raise ValueError('authorization must be in private recovery slot')
    a=load(path);now=time.time();expires=a.get('expires_at')
    required=('allow_prepare',) if plan['operation']=='prepare' else ('allow_quit','allow_exchange')+(
        ('allow_launch',) if plan['operation']=='install' else ())
    if (a.get('operation')!=plan['operation'] or a.get('plan_sha256')!=plan_hash(plan)
            or a.get('phase') is not None
            or type(expires) not in (int,float) or not now<expires<=now+3600
            or any(a.get(k) is not True for k in required)):
        raise ValueError('authorization expired, insufficient or bound to another plan')

def preparation_plan(p,native,profile):
    private_dir(p.private);safe(p.recovery);safe(p.installed)
    if not p.recovery.is_relative_to(p.private) or p.recovery==p.private:raise ValueError('preparation outside PrivateArchive')
    mounts=native.mounts(p)
    if mounts.get('private_image_verified') is not True:raise ValueError('private image not verified')
    if p.recovery.exists():
        private_dir(p.recovery)
        if any(x.name not in {'authorize-prepare.json','.install-51231.lock'} for x in p.recovery.iterdir()):
            raise ValueError('preparation slot already contains material; never overwrite')
    else:private_dir(p.recovery.parent)
    for name,h in profile['receipts'].items():
        if Path(name).name!=name or file_hash(p.candidate.parent/name)!=h:raise ValueError('source receipt changed')
    verify_candidate(p.candidate,native,profile);native.verify(p.installed)
    generation=p.recovery.name if re.fullmatch('[a-f0-9]{32}',p.recovery.name) else uuid.uuid5(uuid.NAMESPACE_URL,str(p.recovery)).hex
    return {'operation':'prepare','generation':generation,
        'installed_sha256':tree_hash(p.installed),'installed_inode':p.installed.stat().st_ino,
        'protected_state':state_hash(p),'external_uuid':mounts['external_uuid'],'private_uuid':mounts['private_uuid'],
        'candidate_sha256':profile['bundle_sha256'],'source_receipts':profile['receipts'],'write_roots':write_roots(p)}

def grant(p,native,profile,*,operation,expected_plan,apply=False):
    """Explicit operator grant, not an API credential and not automatic OAuth."""
    plan=preparation_plan(p,native,profile) if operation=='prepare' else preflight(p,native,profile,operation=operation)
    if not apply or expected_plan!=plan_hash(plan):raise ValueError('explicit --apply and current --expected-plan required')
    with locked(p,plan,create_recovery=True):
        current=preparation_plan(p,native,profile) if operation=='prepare' else preflight(p,native,profile,operation=operation)
        if current!=plan:raise ValueError('plan changed before grant')
        path=p.recovery/('authorize-'+operation+'.json')
        if path.exists() and load(path).get('expires_at',0)>time.time():raise ValueError('unexpired grant already exists')
        data={'operation':operation,'plan_sha256':plan_hash(plan),'expires_at':int(time.time())+900,
            'allow_prepare':operation=='prepare','allow_quit':operation!='prepare',
            'allow_exchange':operation!='prepare','allow_launch':operation=='install'}
        durable(path,data)
    return {'phase':'authorization_granted','authorization':str(path),'plan_sha256':plan_hash(plan)}

def prepare_recovery(p,native,profile,*,apply=False,authorization=None):
    plan=preparation_plan(p,native,profile)
    if not apply:return {'phase':'recovery_preflight_only','plan_sha256':plan_hash(plan),'plan':plan}
    authorize(p,authorization,plan)
    with locked(p,plan):
        if preparation_plan(p,native,profile)!=plan:raise ValueError('preparation state changed')
        try:
            backup=p.recovery/'previous.app';native.copy(p.installed,backup);native.verify(backup)
            if tree_hash(backup)!=plan['installed_sha256'] or preparation_plan_after_copy(p,native,profile,plan)!=plan:
                raise ValueError('app or state changed during recovery preparation')
            metadata={'schema':1,'kind':'metadata-only-no-credential-content','guard':'claude5-observed-layout-v2',
                'generation':plan['generation'],'roots':[str(x) for x in protected_roots(p)],
                'state_fingerprint':plan['protected_state']}
            durable(p.recovery/'protected-state.json',metadata)
            manifest={'schema':2,'kind':'app-and-metadata-only-no-state-restore','generation':plan['generation'],
                'external_uuid':plan['external_uuid'],'private_uuid':plan['private_uuid'],
                'previous_bundle_sha256':plan['installed_sha256'],'metadata_sha256':file_hash(p.recovery/'protected-state.json')}
            durable(p.recovery/'recovery.json',manifest);recovery_check(p,native)
            return {'phase':'recovery_prepared','generation':plan['generation'],'recovery':str(p.recovery)}
        except BaseException as error:
            durable(p.recovery/'prepare-failure.json',{'phase':'manual_recovery_required','operation':'prepare','error_type':type(error).__name__},detached=True);raise

def preparation_plan_after_copy(p,native,profile,plan):
    # The old app copy is now present, so reuse the same checks without the
    # fresh-slot predicate. It is NOT a user-data content snapshot.
    native.verify(p.installed);verify_candidate(p.candidate,native,profile)
    mounts=native.mounts(p)
    return {**plan,'installed_sha256':tree_hash(p.installed),'installed_inode':p.installed.stat().st_ino,
        'protected_state':state_hash(p),'external_uuid':mounts['external_uuid'],'private_uuid':mounts['private_uuid']}

@contextlib.contextmanager
def locked(p,plan=None,*,create_recovery=False):
    scope={};token=None
    with contextlib.ExitStack() as stack:
        pins=(plan or {}).get('write_roots',write_roots(p))
        for path in (p.private,p.external,p.installed.parent):
            scope[path]=stack.enter_context(BoundDir(path,pins[str(path)],private=path==p.private))
        parent=scope[p.private]
        parts=p.recovery.relative_to(p.private).parts
        if not parts:raise ValueError('private recovery leaf required')
        for i,part in enumerate(parts):
            create=create_recovery and i==len(parts)-1 and not (parent.path/part).exists()
            parent=stack.enter_context(parent.child(part,create=create))
        scope[p.recovery]=parent
        token=_WRITES.set(scope)
        fd=os.open('.install-51231.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600,dir_fd=parent.fd)
        try:
            s=os.fstat(fd)
            if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)!=0o600 or s.st_nlink!=1:
                raise ValueError('private lock replaced')
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);check_writes();yield
        finally:
            os.close(fd)
            if token is not None:_WRITES.reset(token)

class LocaleConflict(ValueError):
    def __init__(self,artifacts):
        self.artifacts=artifacts
        super().__init__('locale exchange conflict; both versions retained; manual inspection required')

def _read_at(parent,name):
    fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=parent.fd)
    with os.fdopen(fd,'rb') as f:
        s=os.fstat(f.fileno());raw=f.read();after=os.fstat(f.fileno())
    key=lambda x:(x.st_dev,x.st_ino,x.st_mode,x.st_uid,x.st_gid,x.st_size,x.st_mtime_ns,x.st_nlink)
    if not stat.S_ISREG(s.st_mode) or key(s)!=key(after):raise ValueError('unstable locale file')
    return raw,key(s)

def locale_write(path,before,data):
    """Exchange first, validate displaced exact preimage; NEVER delete a conflict.

    The SHA is only the caller's precondition, not an alleged atomic CAS. The
    kernel exchange retains the actual syscall-boundary version in a 0700 dir.
    """
    with BoundDir(path.parent) as parent:
        s=os.fstat(parent.fd)
        if s.st_uid!=os.getuid() or s.st_mode&0o022:raise ValueError('locale parent not private to writer')
        original,identity=_read_at(parent,path.name)
        if digest(original)!=before:raise ValueError('locale changed before proposal')
        proposed=json.dumps(data,ensure_ascii=False,indent=2).encode()+b'\n'
        holder_name='.locale-'+uuid.uuid4().hex
        with parent.child(holder_name,create=True) as holder:
            holder.private=True;holder.check()
            artifacts={'directory':str(holder.path),'displaced':str(holder.path/'candidate.json'),
                'intended':str(holder.path/'intended.json'),'parent_identity':list(parent.identity)}
            for name in ('candidate.json','intended.json'):
                mode=stat.S_IMODE(identity[2]) if name=='candidate.json' else 0o600
                fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode,dir_fd=holder.fd)
                os.fchmod(fd,mode)
                with os.fdopen(fd,'wb') as f:f.write(proposed);f.flush();os.fsync(f.fileno())
            _,candidate_identity=_read_at(holder,'candidate.json');os.fsync(holder.fd)
            try:
                parent.check();holder.check();check_writes()
                _swap_at(holder.fd,'candidate.json',parent.fd,path.name)
                displaced,displaced_identity=_read_at(holder,'candidate.json')
                current,current_identity=_read_at(parent,path.name)
                # rename changes ctime, so compare inode, mtime, mode and bytes,
                # not ctime; in-place/rename edits at the syscall are retained.
                if displaced!=original or displaced_identity!=identity or current!=proposed or current_identity!=candidate_identity:
                    raise LocaleConflict(artifacts)
                parent.check();holder.check();check_writes()
                return artifacts
            except BaseException as error:
                if isinstance(error,LocaleConflict):raise
                raise LocaleConflict(artifacts) from error

def apply_locale(p,journal,checkpoint):
    prepared=[]
    for path in locale_config_paths(p.home):
        safe(path)
        if not path.exists():continue  # Never create settings on behalf of a user.
        before=file_hash(path);data=load(path)
        if not isinstance(data,dict):raise ValueError('invalid locale settings object')
        value=data.get('locale')
        if isinstance(value,str) and value.lower().startswith('zh'):continue
        item={'path':str(path),'had_locale':'locale' in data,'previous_locale':value,'before_sha256':before}
        data['locale']='zh-CN'
        item['after_sha256']=digest(json.dumps(data,ensure_ascii=False,indent=2).encode()+b'\n')
        prepared.append((path,before,data,item))
    for path,before,data,item in prepared:
        journal.append(item);checkpoint('locale_pending')
        try:item['retained']=locale_write(path,before,data)
        except LocaleConflict as e:item['retained']=e.artifacts;raise
        checkpoint('installed_not_started')

def restore_locale(p,journal):
    allowed=set(locale_config_paths(p.home))
    for item in journal:
        path=Path(item['path'])
        if path not in allowed:raise ValueError('unexpected locale rollback path')
        safe(path)
        if file_hash(path)!=item['after_sha256']:raise ValueError('locale changed after installer; refusing overwrite')
        data=load(path)
        if item['had_locale']:data['locale']=item['previous_locale']
        else:data.pop('locale',None)
        try:item['rollback_retained']=locale_write(path,item['after_sha256'],data)
        except LocaleConflict as e:item['rollback_retained']=e.artifacts;raise

def summary(state):return {k:state[k] for k in ('phase','generation','launch_count')}

def install(p,native,profile,*,apply=False,authorization=None):
    plan=preflight(p,native,profile)
    if not apply:return {'phase':'preflight_only','plan_sha256':plan_hash(plan),'plan':plan}
    authorize(p,authorization,plan)
    with locked(p,plan):
        if preflight(p,native,profile)!=plan:raise ValueError('preflight changed while acquiring lock')
        stage=stage_path(p,plan['generation'])
        native.copy(p.candidate,stage);verify_candidate(stage,native,profile)
        if not native.same_volume(stage,p.installed):raise ValueError('staging not on installed app volume')
        if preflight_for_staged(p,native,profile,plan)!=plan:raise ValueError('pre-quit plan changed')
        authorize(p,authorization,plan)  # Complete checks, including token expiry, BEFORE quit.
        state={'phase':'prepared','generation':plan['generation'],'launch_count':0,'launch_submitted':False,
            'candidate_sha256':profile['bundle_sha256'],'previous_sha256':plan['installed_sha256'],
            'recovery_sha256':plan['recovery_sha256'],'locale_journal':[]}
        durable(receipt_path(p),state)
        swapped=False
        try:
            native.quit(p.installed);native.stopped(p.installed)
            recovery,sha=recovery_check(p,native)
            if sha!=plan['recovery_sha256'] or tree_hash(p.installed)!=plan['installed_sha256'] or p.installed.stat().st_ino!=plan['installed_inode']:
                raise ValueError('app or restore material changed after quit')
            verify_candidate(stage,native,profile)
            if not native.same_volume(stage,p.installed):raise ValueError('volume changed after quit')
            authorize(p,authorization,plan)
            state['baseline_state']=state_hash(p);state['phase']='exchange_submitted';durable(receipt_path(p),state)
            native.exchange(stage,p.installed);swapped=True
            state['phase']='installed_not_started';durable(receipt_path(p),state)
            verify_candidate(p.installed,native,profile);native.stopped(p.installed)
            def locale_checkpoint(phase):
                state['baseline_state']=state_hash(p);state['phase']=phase;durable(receipt_path(p),state)
            apply_locale(p,state['locale_journal'],locale_checkpoint)
            state['baseline_state']=state_hash(p)
            native.stopped(p.installed)
            state['executable_inode']=(p.installed/'Contents/MacOS/Claude').stat().st_ino
            authorize(p,authorization,plan)
            state['phase']='launch_submitted';state['launch_submitted']=True;state['launch_count']=1
            durable(receipt_path(p),state)  # At-most-once across exceptions/crashes/re-entry.
            native.start(p.installed)
            observed=native.observe(p.installed)
            if (not isinstance(observed,dict) or type(observed.get('pid')) is not int or observed['pid']<=0
                    or observed.get('executable_inode')!=state['executable_inode']):
                raise ValueError('new process executable identity is incomplete')
            verify_candidate(p.installed,native,profile)
            state['runtime_identity']=observed
            state['phase']='started_pending_acceptance';durable(receipt_path(p),state)
            return summary(state)
        except BaseException as error:
            state['phase']='manual_recovery_required'
            if swapped and not state['launch_submitted'] and not isinstance(error,LocaleConflict):
                try:
                    native.stopped(p.installed)
                    if state_hash(p)!=state['baseline_state'] or tree_hash(p.installed)!=profile['bundle_sha256'] or tree_hash(stage)!=state['previous_sha256']:
                        raise ValueError('new state or app blocks automatic rollback')
                    native.exchange(stage,p.installed);restore_locale(p,state['locale_journal'])
                    state['phase']='rolled_back_stopped'
                except Exception:pass  # Preserve newest state + both apps, never restore data.
            durable(receipt_path(p),state,detached=True);raise

def preflight_for_staged(p,native,profile,plan):
    """Same read-only preflight with our verified, reserved staging slot present."""
    r,sha=recovery_check(p,native);verify_candidate(p.candidate,native,profile);native.verify(p.installed)
    locale_preflight(p)
    for name,h in profile['receipts'].items():
        if file_hash(p.candidate.parent/name)!=h:raise ValueError('source receipt changed during copy')
    if receipt_path(p).exists():raise ValueError('another transaction appeared')
    return {**plan,'generation':r['generation'],'installed_sha256':tree_hash(p.installed),
        'installed_inode':p.installed.stat().st_ino,'recovery_sha256':sha,'protected_state':state_hash(p)}

def rollback(p,native,profile,*,apply=False,authorization=None):
    plan=preflight(p,native,profile,operation='rollback')
    if not apply:return {'phase':'rollback_preflight_only','plan_sha256':plan_hash(plan),'plan':plan}
    authorize(p,authorization,plan)
    with locked(p,plan):
        if preflight(p,native,profile,operation='rollback')!=plan:raise ValueError('rollback changed while acquiring lock')
        state=load(receipt_path(p))
        try:
            native.quit(p.installed);native.stopped(p.installed)
            if state_hash(p)!=state['baseline_state']:raise ValueError('new startup/shutdown data; no stale rollback')
            if preflight(p,native,profile,operation='rollback')!=plan:raise ValueError('rollback preflight changed after quit')
            authorize(p,authorization,plan)
            state['phase']='rollback_submitted';durable(receipt_path(p),state)
            native.exchange(p.installed,stage_path(p,state['generation']))
            restore_locale(p,state['locale_journal'])
            state['phase']='rolled_back_stopped';durable(receipt_path(p),state)
            return summary(state)  # No second app launch, no settings/database restore.
        except BaseException:
            state['phase']='manual_recovery_required';durable(receipt_path(p),state,detached=True);raise

class Native:
    def run(self,args,**kw):return subprocess.run(args,capture_output=True,check=True,timeout=kw.pop('timeout',30),**kw)
    def mounts(self,p):
        if not os.path.ismount(p.external) or not os.path.ismount(p.private):raise ValueError('real external/private mount required')
        infos=[plistlib.loads(self.run(['/usr/sbin/diskutil','info','-plist',str(x)]).stdout) for x in (p.external,p.private)]
        if any(Path(i.get('MountPoint','/'))!=x or not i.get('VolumeUUID') for i,x in zip(infos,(p.external,p.private))):raise ValueError('mount identity unavailable')
        images=plistlib.loads(self.run(['/usr/bin/hdiutil','info','-plist']).stdout).get('images',[])
        matches=[i for i in images if i.get('image-path')==str(IMAGE) and any(e.get('mount-point')==str(p.private) for e in i.get('system-entities',[]))]
        if len(matches)!=1:raise ValueError('private mount is not the expected external image')
        # hdiutil info reports the encryption of this ALREADY MOUNTED image.
        # Do not scan/decrypt imageinfo (observed 30s timeout), attach, or unlock.
        if matches[0].get('image-encrypted') is not True:raise ValueError('encrypted private image not attested')
        return {'external_uuid':infos[0]['VolumeUUID'],'private_uuid':infos[1]['VolumeUUID'],'private_image_verified':True}
    def verify(self,app,version=None):
        safe(app);info=plistlib.loads((app/'Contents/Info.plist').read_bytes())
        if info.get('CFBundleIdentifier')!='com.anthropic.claudefordesktop' or info.get('CFBundleExecutable')!='Claude' or version and (info.get('CFBundleShortVersionString')!=version or info.get('CFBundleVersion')!=version):raise ValueError('Claude bundle identity mismatch')
        self.run(['/usr/bin/codesign','--verify','--deep','--strict',str(app)],timeout=45)
        if version:verify_electron_asar_integrity(app)
    def copy(self,a,b):
        safe(b)
        with contextlib.ExitStack() as stack:
            scope=_WRITES.get()
            parent=bound_parent(b.parent) if scope else stack.enter_context(BoundDir(b.parent))
            if scope:check_writes()
            parent.check()
            # mkdirat is non-recursive on the held destination volume. The
            # child cwd fd, not an absolute pathname, is passed to ditto.
            target=stack.enter_context(parent.child(b.name,create=True));target.private=False
            if os.fstat(target.fd).st_dev!=parent.identity[0]:raise ValueError('copy target volume changed')
            if scope:check_writes()
            self.run(['/usr/bin/ditto',str(a),'.'],timeout=210,pass_fds=(target.fd,),
                preexec_fn=lambda:os.fchdir(target.fd))
            parent.check();target.check()
            if scope:check_writes()
    def same_volume(self,a,b):return a.stat().st_dev==b.stat().st_dev==b.parent.stat().st_dev
    def pids(self,p):
        r=subprocess.run(['/usr/bin/pgrep','-f','^'+re.escape(str(p/'Contents/MacOS/Claude'))+'($| )'],capture_output=True,timeout=5)
        if r.returncode==1 and not r.stdout and not r.stderr:return []
        if r.returncode or r.stderr or not r.stdout.strip() or not all(x.isdigit() and int(x)>0 for x in r.stdout.split()):raise ValueError('process state uncertain')
        return [int(x) for x in r.stdout.split()]
    def stopped(self,p):
        if self.pids(p):raise ValueError('Claude remains running')
    def quit(self,p):
        if not self.pids(p):return
        self.run(['/usr/bin/osascript','-e','tell application id "com.anthropic.claudefordesktop" to quit'])
        for _ in range(25):
            if not self.pids(p):return
            time.sleep(1)
        raise ValueError('Claude did not exit gracefully')
    def exchange(self,a,b):
        safe(a);safe(b)
        if not self.same_volume(a,b):raise ValueError('atomic exchange requires same volume')
        check_writes();left=bound_parent(a.parent);right=bound_parent(b.parent)
        _swap_at(left.fd,a.name,right.fd,b.name);check_writes()
    def start(self,p):self.run(['/usr/bin/open','-a',str(p)])
    def observe(self,p):
        executable=p/'Contents/MacOS/Claude'
        for _ in range(25):
            ids=self.pids(p)
            if len(ids)==1:
                r=self.run(['/usr/sbin/lsof','-nP','-a','-p',str(ids[0]),'-d','txt','-Fpin'],timeout=5)
                lines=r.stdout.decode().splitlines();inode=None;found=[]
                for line in lines:
                    if line.startswith('f'):inode=None
                    if line.startswith('i') and line[1:].isdigit():inode=int(line[1:])
                    if line=='n'+str(executable) and inode==executable.stat().st_ino:found.append(inode)
                if lines and lines[0]=='p'+str(ids[0]) and len(found)==1:return {'pid':ids[0],'executable_inode':found[0]}
            time.sleep(1)
        raise ValueError('new process executable identity unverified')

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--recovery',type=Path,required=True)
    parser.add_argument('--apply',action='store_true');parser.add_argument('--authorization',type=Path)
    modes=parser.add_mutually_exclusive_group();modes.add_argument('--rollback',action='store_true')
    modes.add_argument('--prepare-recovery',action='store_true')
    parser.add_argument('--grant',action='store_true',help='explicitly create a short-lived grant for the reviewed plan')
    parser.add_argument('--expected-plan',help='reviewed plan SHA256 required by --grant --apply')
    args=parser.parse_args(argv)
    p=Paths(BUILD/'Claude.app',Path('/Applications/Claude.app'),PRIVATE,EXTERNAL,args.recovery,Path.home())
    profile=load(PROFILE)
    if profile['version']!='5.1231.0':raise ValueError('fixed 5.1231.0 entry only')
    operation='prepare' if args.prepare_recovery else 'rollback' if args.rollback else 'install'
    if args.grant:
        result=grant(p,Native(),profile,operation=operation,expected_plan=args.expected_plan,apply=args.apply)
    else:
        action=prepare_recovery if args.prepare_recovery else rollback if args.rollback else install
        result=action(p,Native(),profile,apply=args.apply,authorization=args.authorization)
    print(json.dumps(result,sort_keys=True))

if __name__=='__main__':main()
