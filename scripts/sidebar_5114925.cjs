'use strict';

// Source-bound adapter for the signed 5.11492.5 sidebar. No app files are
// written by this module; only a separate audited candidate may use its bytes.
const acorn = require('acorn');
const crypto = require('node:crypto');
const fs = require('node:fs');
const SOURCE_SHA256 = '6a54cf575da69d4539e9157f97a0fef36d5f87edbd5d9617656e3ca7e2f8a9f7';
const rules = [
  ['var Xv={collapsed:!1,sidebarWidth:Wv}',
   'var Xv={collapsed:!0,sidebarWidth:Wv}'],
  ['return{collapsed:!!r.collapsed,sidebarWidth:',
   'return{collapsed:!0,sidebarWidth:'],
  ['X=Fi()(Ni((e,t)=>({collapsed:!1,',
   'X=Fi()(Ni((e,t)=>({collapsed:!0,'],
  ['return{...t,...n}',
   'return{...t,...n,collapsed:!0,composedSidebarOpen:!1,composedSidebarMounted:!1}'],
];
function parse(source) {
  return acorn.parse(source,{ecmaVersion:'latest',sourceType:'module'});
}
function patchSidebar(source) {
  parse(source);
  let result = source;
  for (const [before, after] of rules) {
    if (result.split(before).length !== 2 || result.includes(after))
      throw new Error('current sidebar source or idempotency changed');
    result = result.replace(before,after);
  }
  parse(result);
  return result;
}
function patchPinned(source, expected = SOURCE_SHA256) {
  if (crypto.createHash('sha256').update(source).digest('hex') !== expected ||
      expected !== SOURCE_SHA256) throw new Error('current signed sidebar hash changed');
  return patchSidebar(source);
}
function walk(n,visit) {
  if (!n || typeof n !== 'object') return;
  visit(n);
  for (const v of Object.values(n)) {
    if (Array.isArray(v)) v.forEach(x=>walk(x,visit));
    else if (v && typeof v==='object') walk(v,visit);
  }
}
function parts(source) {
  const found={};
  walk(parse(source),node=>{
    if (node.type==='FunctionDeclaration' && ['Qv','uk','Vk'].includes(node.id?.name))
      found[node.id.name]=source.slice(node.start,node.end);
    if (node.type==='ObjectExpression' && node.properties.some(p=>p.key?.name==='merge') &&
        node.properties.some(p=>p.key?.name==='partialize')) {
      for (const p of node.properties) if (['merge','partialize'].includes(p.key?.name))
        found[p.key.name]=source.slice(p.value.start,p.value.end);
    }
    if (node.type==='Property' && node.key?.name==='setCollapsedPref')
      found.setCollapsedPref=source.slice(node.value.start,node.value.end);
  });
  if (Object.keys(found).sort().join(',') !==
      ['Qv','Vk','merge','partialize','setCollapsedPref','uk'].sort().join(','))
    throw new Error('current sidebar lifecycle scope changed');
  return found;
}
module.exports={patchSidebar,patchPinned,parts,SOURCE_SHA256};
if (require.main===module) {
  try {
    const input=JSON.parse(fs.readFileSync(0,'utf8'));
    const result=input.operation==='patch' ? patchPinned(input.source) :
      input.operation==='parts' ? parts(input.source) :
      (()=>{throw new Error('unsupported operation')})();
    process.stdout.write(JSON.stringify(result)+'\n');
  } catch (error) {console.error('sidebar_5114925_error='+error.name);process.exitCode=1;}
}
