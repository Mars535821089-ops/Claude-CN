"""Fingerprint-pinned 5.x compatibility preparation; all vendor JS is parsed only."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import hashlib

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
 'gated': ('app.asar', '.vite/build/index.chunk-D3ouEfeo.js', 'dbd75dc373531b25b4d5bdeb0d7640c196cf32f106bb10a9276f1883712141dd'),
 'sidebar': ('ion-dist.asar', 'assets/v1/shared-98-DEv7MzNi.js', 'd42d27652bf16486d8f8d0217db134bbd00a3086f3cbd0080f84ddcdd0d1728b'),
 'fallback': ('ion-dist.asar', 'assets/v1/shared-29-Ca8yur2i.js', '876fd02455294ad8679322425ccfc8db1f7846f599ccf1433ffc7f9b6eaf884a'),
 'cowork': ('app.asar', '.vite/build/index.chunk-CWaGU0Cp.js', '06e27c74c391ae98e010fe18076e63e4b803508fda01bbdbacfefe11ebf92809'),
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
 owner=entry('app.asar','.vite/build/index.chunk-BV3uOqbK.js').decode()
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
  'compatibility-reviewed.json':{'version':'5.1231.2','files':specs,'validation':validation,
   'policy_entry':POLICY_ENTRY,'policy_sha256':sha((ROOT/'scripts/cowork_compaction_policy.cjs').read_bytes()),
   'native_scope_equivalence':False,'gui_verified':False}}
 return outputs

def apply(entry, candidate):
 """Return validated in-memory edits. The caller owns an isolated new build."""
 specs=json.loads((candidate/'compatibility-reviewed.json').read_text())
 if specs['version']!='5.1231.2' or set(specs['files'])!=set(TARGETS):
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
