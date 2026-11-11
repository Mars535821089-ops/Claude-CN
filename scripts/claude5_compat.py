"""Fingerprint-pinned 5.x compatibility preparation; all vendor JS is parsed only."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import hashlib

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
 'gated': ('app.asar', '.vite/build/index.chunk-CInLNSAV.js', '07fac0b520e6f3640e42dec24fe353ac2effabf00975952a9406e95556829820'),
 'sidebar': ('ion-dist.asar', 'assets/v1/shared-98-ChPwuRcq.js', 'e09e48ec03b7823388347fbe73d730ae287c08e2b0813721d9573ba52551d66d'),
 'fallback': ('ion-dist.asar', 'assets/v1/shared-29-BBxMe69G.js', '1436e82e5c46f529908e44f2388340d8f6ec72cd865d27f236c4a4eda2eb6e04'),
 'cowork': ('app.asar', '.vite/build/index.chunk-2cjcenUp.js', '2dbca5ad88d9a8a8b63efa1ed8a1bbe2105d8204d58080dbd5c4cf4a78f0a4fd'),
}
POLICY_ENTRY = '.vite/build/claude-cn-compaction-policy.cjs'
def sha(raw): return hashlib.sha256(raw).hexdigest()
def js(action, source, **kwargs):
 result = subprocess.run(['node', str(ROOT/'scripts/claude5_compat.cjs')],
  input=json.dumps(dict(action=action, source=source, **kwargs)),text=True,capture_output=True,check=True,timeout=90)
 return json.loads(result.stdout)

def prepare(entry, candidate, catalogs):
 sources={}
 for action,(archive,path,digest) in TARGETS.items():
  raw=entry(archive,path)
  if sha(raw)!=digest: raise ValueError('unknown 5.x compatibility source: '+action)
  sources[action]=raw.decode()
 english=js('gated',sources['gated'])
 authored=json.loads((candidate/'gated-authored-zh-CN.json').read_text())
 if authored['source_sha256']!=TARGETS['gated'][2] or not authored['translations'].keys()<=english.keys():
  raise ValueError('stale gated translations')
 by_text={}
 for group,(archive,path) in {
  'desktop':('app.asar','resources/i18n/en-US.json'),
  'frontend':('ion-dist.asar','i18n/en-US.json'),
  'dynamic':('ion-dist.asar','i18n/dynamic/en-US.json')}.items():
  en=json.loads(entry(archive,path));zh=catalogs[group+'-zh-CN.json']
  for k,v in en.items():
   if k in zh: by_text.setdefault(v,set()).add(zh[k])
 chinese,provenance={},{}
 for k,v in english.items():
  if k in authored['translations']:
   chinese[k]=authored['translations'][k];provenance[k]='authored_pinned_gated_source'
  elif len(by_text.get(v,[]))==1:
   chinese[k]=next(iter(by_text[v]));provenance[k]='unambiguous_exact_english'
  else: raise ValueError('gated Chinese missing: '+k)
 patches={}
 for action,source in sources.items():
  patched=js('patch-'+action,source,**({'chinese':chinese} if action=='gated' else {}))
  patches[action]=patched
 before=js('sidebar-parts',sources['sidebar']);after=js('sidebar-parts',patches['sidebar'])
 if before['setCollapsedPref']!=after['setCollapsedPref'] or before['partialize']!=after['partialize']:
  raise ValueError('native sidebar controls/persistence changed')
 owner=entry('app.asar','.vite/build/index.chunk-C6mgT-OL.js').decode()
 getter='function y2r(){let e=Ul()?.autoCompactWindow;return typeof e=="number"&&e>0?e:void 0}'
 propagation='...t._H()&&{CLAUDE_CODE_AUTO_COMPACT_WINDOW:String(t._H())}'
 if owner.count(getter)!=1 or sources['cowork'].count(propagation)!=1:
  raise ValueError('native compaction dataflow changed')
 payload={'catalogs':{'gated':{'en':english,'zh':chinese,'strictSelections':True}},
          'scripts':{TARGETS[a][0]+'/'+TARGETS[a][1]:s for a,s in patches.items()}}
 result=subprocess.run(['node',str(ROOT/'scripts/audit_messages.cjs')],input=json.dumps(payload),text=True,capture_output=True,check=True,timeout=180)
 validation=json.loads(result.stdout)
 specs={a:{'archive':ar,'path':p,'sha256':h,'patched_sha256':sha(patches[a].encode())} for a,(ar,p,h) in TARGETS.items()}
 outputs={
  'gated-en-US.json':english,'gated-zh-CN.json':chinese,'gated-provenance.json':provenance,
  'sidebar-lifecycle-fixture.json':{'source_sha256':TARGETS['sidebar'][2],'before':before,'after':after},
  'cowork-native-fixture.json':{'getter':getter,'propagation':propagation,
   'config_scope':'shared in-memory provider; getter has no session argument',
   'native_equivalent_to_session_allowlist':False,
   'minimal_adapter':'optional CLAUDE_CN_SESSION_COMPACTION_POLICY; no configuration is created or enabled by this build',
   'runtime_compaction_verified':False},
  'compatibility-reviewed.json':{'version':'5.1231.0','files':specs,'validation':validation,
   'policy_entry':POLICY_ENTRY,'policy_sha256':sha((ROOT/'scripts/cowork_compaction_policy.cjs').read_bytes()),
   'native_scope_equivalence':False,'gui_verified':False}}
 return outputs

def apply(entry, candidate):
 """Return validated in-memory edits. The caller owns an isolated new build."""
 specs=json.loads((candidate/'compatibility-reviewed.json').read_text())
 if specs['version']!='5.1231.0' or set(specs['files'])!=set(TARGETS):
  raise ValueError('compatibility profile changed')
 edits={}
 for action,(archive,path,digest) in TARGETS.items():
  source=entry(archive,path)
  spec=specs['files'][action]
  if sha(source)!=digest or spec['sha256']!=digest or spec['archive']!=archive or spec['path']!=path:
   raise ValueError('compatibility source changed')
  kwargs={'chinese':json.loads((candidate/'gated-zh-CN.json').read_text())} if action=='gated' else {}
  patched=js('patch-'+action,source.decode(),**kwargs).encode()
  if sha(patched)!=spec['patched_sha256']:raise ValueError('compatibility output changed')
  edits[(archive,path)]=patched
 policy=(ROOT/'scripts/cowork_compaction_policy.cjs').read_bytes()
 if sha(policy)!=specs['policy_sha256']:raise ValueError('session policy changed')
 edits[('app.asar',POLICY_ENTRY)]=policy
 return edits
