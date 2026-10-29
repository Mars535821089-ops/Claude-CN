const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { scan } = require('../scripts/audit_ui.cjs');
const { applyRules, prepareBundle, sha256, structure } = require('../scripts/ui_localization.cjs');

test('finds labels, nested rendered children, placeholders and accessibility text', () => {
  const findings = scan('f({label:"Connect",children:ok?"Copy":"Cancel",placeholder:"Search",'
    + '"aria-label":"Copy code",icon:"Copy",role:"button",type:"Copy"})', 'fixture.js');
  assert.deepEqual(findings.map(item => item.text), ['Connect', 'Copy', 'Cancel', 'Search', 'Copy code']);
});

test('does not translate condition tests, logical guards, icon names or message IDs', () => {
  const findings = scan('f({children:state==="loading"?"Loading":null,'
    + 'title:state==="error"&&"Retry",icon:"Copy",label:t({id:"Copy"})})', 'fixture.js');
  assert.deepEqual(findings.map(item => item.text), ['Loading', 'Retry']);
});

test('defaultMessage and translator description are not hardcoded UI controls', () => {
  assert.deepEqual(scan('t({id:"x",defaultMessage:"Copy",description:"Copy code"})', 'fixture.js'), []);
});

test('handles DOM text, attribute setters, native buttons and React children', () => {
  const findings = scan('button.textContent="Copy";button.setAttribute("aria-label","Copy code");'
    + 'React.createElement("b",null,"Connect");dialog({buttons:["Cancel","Copy"]})', 'fixture.js');
  assert.deepEqual(findings.map(item => item.text), ['Copy', 'Copy code', 'Connect', 'Cancel', 'Copy']);
});

test('native consent details are separate from programmatic error messages', () => {
  const findings = scan('dialog({title:"Permission",buttons:["Cancel"],'
    + 'message:"Allow access?",detail:"Read and modify files",checkboxLabel:"Remember"});'
    + 'error({message:"Allow access?"})', 'fixture.js');
  assert.deepEqual(findings.map(item => item.context), [
    'title', 'buttons', 'dialog:message', 'dialog:detail', 'checkboxLabel', 'message',
  ]);
});

test('only replaces reviewed exact UI values; programmatic strings stay untouched', () => {
  const source = 'f({children:"Copy",icon:"Copy",type:"Copy",id:"Copy",'
    + 'onClick:()=>send("Copy"),label:"copy"});const Copy="Copy"';
  const patched = applyRules(source, 'fixture.js', [
    { source: 'Copy', context: 'children', expected: 1 },
  ]);
  assert.match(patched.source, /children:"复制",icon:"Copy",type:"Copy",id:"Copy"/);
  assert.match(patched.source, /send\("Copy"\)/);
  assert.deepEqual(structure(patched.source), structure(source));
});

test('template expressions keep their order and original code', () => {
  const source = 'f({title:`Claude deleted ${next()} sessions`})';
  const result = applyRules(source, 'fixture.js', [
    { source: 'Claude deleted {…} sessions', context: 'title', expected: 1 },
  ]);
  assert.equal(result.source, 'f({title:`Claude 已删除 ${next()} 个会话`})');
});

test('nested template and literal translations do not overlap or alter guard strings', () => {
  const source = 'f({title:`Item ${flag==="copy"?"Copy":"Cancel"}`})';
  const result = applyRules(source, 'fixture.js', [
    { source: 'Item {…}', context: 'title', expected: 1 },
    { source: 'Copy', context: 'title', expected: 1 },
    { source: 'Cancel', context: 'title', expected: 1 },
  ], { 'Item {…}': '条目 {…}', Copy: '复制', Cancel: '取消' });
  assert.equal(result.source, 'f({title:`条目 ${flag==="copy"?"复制":"取消"}`})');
  assert.deepEqual(structure(result.source), structure(source));
});

test('rejects template translations losing placeholders', () => {
  assert.throws(() => applyRules('f({title:`Count ${next()}`})', 'fixture.js', [
    { source: 'Count {…}', context: 'title', expected: 1 },
  ], { 'Count {…}': '计数' }), /placeholders changed/);
});

test('count drift, missing translation and overlapping rules stop explicitly', () => {
  assert.throws(() => applyRules('f({children:"Copy"})', 'fixture.js', [
    { source: 'Copy', context: 'children', expected: 2 },
  ]), /source changed/);
  assert.throws(() => applyRules('f({children:"Unknown"})', 'fixture.js', [
    { source: 'Unknown', context: 'children', expected: 1 },
  ]), /Missing reviewed translation/);
  assert.throws(() => applyRules('f({children:"Copy"})', 'fixture.js', [
    { source: 'Copy', context: 'children', expected: 1 },
    { source: 'Copy', context: 'children', expected: 1 },
  ]), /Overlapping/);
});

test('replacement escapes quotes, backticks and interpolation-like text', () => {
  const source = 'f({title:`Item ${id}`})';
  const result = applyRules(source, 'fixture.js', [
    { source: 'Item {…}', context: 'title', expected: 1 },
  ], { 'Item {…}': '文本 ` "${notCode}" {…}' });
  assert.deepEqual(structure(result.source), structure(source));
});

test('file fingerprint mismatch preflights before any write, patched files are idempotent', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'claude-ui-test-'));
  try {
    const source = 'f({children:"Copy"})';
    const rules = [{ source: 'Copy', context: 'children', expected: 1 }];
    const patched = applyRules(source, 'fixture.js', rules).source;
    const manifest = { files: { 'app.asar/fixture.js': {
      sha256: sha256(source), patched_sha256: sha256(patched), rules,
    } } };
    const file = path.join(root, 'fixture.js');
    fs.writeFileSync(file, source);
    assert.equal(prepareBundle(root, 'app.asar', manifest).length, 1);
    assert.equal(fs.readFileSync(file, 'utf8'), source);
    fs.writeFileSync(file, source + ';');
    assert.throws(() => prepareBundle(root, 'app.asar', manifest), /Unknown UI source fingerprint/);
    assert.equal(fs.readFileSync(file, 'utf8'), source + ';');
    fs.writeFileSync(file, patched);
    assert.deepEqual(prepareBundle(root, 'app.asar', manifest), []);
  } finally { fs.rmSync(root, { recursive: true }); }
});

test('bundle path traversal is refused', () => {
  assert.throws(() => prepareBundle('/tmp/test', 'app.asar', {
    files: { 'app.asar/../escape.js': {} },
  }), /escapes its bundle/);
});

test('symlink targets are refused and a later fingerprint failure leaves earlier files unchanged', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'claude-ui-test-'));
  try {
    const source = 'f({children:"Copy"})';
    const rules = [{ source: 'Copy', context: 'children', expected: 1 }];
    const spec = { sha256: sha256(source), patched_sha256: sha256(
      applyRules(source, 'fixture.js', rules).source), rules };
    const first = path.join(root, 'first.js');
    const second = path.join(root, 'second.js');
    fs.writeFileSync(first, source);
    fs.symlinkSync(first, second);
    const manifest = { files: { 'app.asar/first.js': spec, 'app.asar/second.js': spec } };
    assert.throws(() => prepareBundle(root, 'app.asar', manifest), /symlink refused/);
    fs.unlinkSync(second);
    fs.writeFileSync(second, 'changed');
    assert.throws(() => prepareBundle(root, 'app.asar', manifest), /Unknown UI source fingerprint/);
    assert.equal(fs.readFileSync(first, 'utf8'), source);
  } finally { fs.rmSync(root, { recursive: true }); }
});

test('malformed JavaScript is not silently ignored', () => {
  assert.throws(() => scan('const = ;', 'fixture.js'), SyntaxError);
});
