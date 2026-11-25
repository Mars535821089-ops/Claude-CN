const test = require('node:test');
const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const vm = require('node:vm');
const { applyTitleFallback } = require('../scripts/title_fallback_adapter.cjs');

const hash = s => createHash('sha256').update(s).digest('hex');
const expression = 'n??(g||r?.listTitle||g)??(me(t)?"New chat":"New task")';
// Vendor code is deliberately unsafe to execute; only the extracted title is evaluated.
const fixture = `throw new Error('vendor must never execute');function title(){return ${expression}}`;
function profile(source = fixture, anchor = expression) {
  return {
    schemaVersion: 1, version: 'fixture', group: 'frontend',
    status: 'source_reviewed_not_installable',
    source: { file: 'fixture.js', sha256: hash(source) },
    anchor: { expression: anchor, sha256: hash(anchor), expectedOccurrences: 1 },
    replacements: [
      { branch: 'consequent', from: 'New chat', to: '新对话' },
      { branch: 'alternate', from: 'New task', to: '新任务' },
    ],
  };
}

test('only the two final literals change; vendor statements remain inert', () => {
  const result = applyTitleFallback(fixture, profile());
  assert.equal(result.source, `throw new Error('vendor must never execute');function title(){return n??(g||r?.listTitle||g)??(me(t)?"新对话":"新任务")}`);
  assert.equal(result.edits.length, 2);
  let cursor = 0;
  for (const edit of result.edits) {
    assert.equal(fixture.slice(edit.start, edit.end), JSON.stringify(edit.from));
    assert.equal(result.source.includes(fixture.slice(cursor, edit.start)), true);
    cursor = edit.end;
  }
  assert.equal(result.sourceSha256, hash(fixture));
  assert.equal(result.patchedSha256, hash(result.source));
});

test('chat/task fallback changes without changing nullish, truthy, empty or lazy precedence', () => {
  const result = applyTitleFallback(fixture, profile());
  const cases = [
    [{ n: 'explicit', g: 'generated', r: { listTitle: 'list' } }, 'explicit'],
    [{ n: '', g: 'generated' }, ''],
    [{ n: 0, g: 'generated' }, 0],
    [{ n: false, g: 'generated' }, false],
    [{ n: null, g: 'generated', r: { listTitle: 'list' } }, 'generated'],
    [{ n: undefined, g: undefined, r: { listTitle: 'list' } }, 'list'],
    [{ n: null, g: '', r: { listTitle: 'list' } }, 'list'],
    [{ n: undefined, g: '', r: { listTitle: '' } }, ''],
    [{ n: undefined, g: '', r: null }, ''],
    [{ n: null, g: 0, r: { listTitle: '' } }, 0],
    [{ n: null, g: false, r: undefined }, false],
  ];
  for (const chat of [true, false]) {
    for (const [values, want] of cases) {
      const context = { n: undefined, g: undefined, r: undefined, ...values, t: chat,
        me() { assert.fail('predicate must not run when a title exists'); } };
      for (const expr of [result.expressionBefore, result.expressionAfter]) {
        assert.equal(vm.runInNewContext(`(${expr})`, context, { timeout: 100 }), want);
      }
    }
    for (const values of [
      { n: null, g: null, r: null },
      { n: undefined, g: undefined, r: undefined },
      { n: undefined, g: undefined, r: { listTitle: '' } },
      { n: undefined, g: null, r: { listTitle: null } },
    ]) {
      let calls = 0;
      const context = { ...values, t: chat, me(t) { calls++; return t; } };
      assert.equal(vm.runInNewContext(`(${result.expressionBefore})`, context), chat ? 'New chat' : 'New task');
      assert.equal(vm.runInNewContext(`(${result.expressionAfter})`, context), chat ? '新对话' : '新任务');
      assert.equal(calls, 2);
    }
  }
});

test('source drift and already-patched input fail closed', () => {
  assert.throws(() => applyTitleFallback(fixture + ' ', profile()), /source.*hash/i);
  const result = applyTitleFallback(fixture, profile());
  assert.throws(() => applyTitleFallback(result.source, profile()), /already.*patched/i);
});

test('AST anchor must be a unique executable title node, not a comment or string', () => {
  for (const source of [
    `// ${expression}\nconst x = 1;`,
    `const x = ${JSON.stringify(expression)};`,
    `${fixture}\nfunction other(){return ${expression}}`,
  ]) assert.throws(() => applyTitleFallback(source, profile(source)), /anchor|duplicate/i);
});

test('hash pinning does not bypass reviewed title shape or anchor validation', () => {
  for (const anchor of [
    'n||(g||r?.listTitle||g)||(me(t)?"New chat":"New task")',
    'n??(g||r?.listTitle||n)??(me(t)?"New chat":"New task")',
    'n??(g||r.listTitle||g)??(me(t)?"New chat":"New task")',
    'n??(g||r?.listTitle||g)??(me(t)?doSomething():"New task")',
    expression + ';doSomething()',
  ]) {
    const source = `function title(){return ${anchor}}`;
    assert.throws(() => applyTitleFallback(source, profile(source, anchor)), /anchor|shape|literal/i);
  }
  const p = profile(); p.anchor.sha256 = '0'.repeat(64);
  assert.throws(() => applyTitleFallback(fixture, p), /anchor.*hash/i);
});

test('replacement data cannot select other nodes or inject raw code', () => {
  for (const mutate of [
    p => { p.replacements[0].code = 'process.exit()'; },
    p => { p.replacements[0].branch = 'left'; },
    p => { p.replacements[1].branch = 'consequent'; },
    p => { p.replacements[0].from = 'wrong'; },
    p => { p.replacements[0].to = { code: 'process.exit()' }; },
    p => { p.replacements[0].to = ''; },
    p => { p.replacements.push(p.replacements[0]); },
    p => { p.status = 'unreviewed'; },
  ]) {
    const p = profile(); mutate(p);
    assert.throws(() => applyTitleFallback(fixture, p), /profile|replacement|branch|literal|reviewed/i);
  }
  const p = profile(); p.replacements[0].to = '标题";throw new Error("not code")//';
  const out = applyTitleFallback(fixture, p);
  assert.equal(vm.runInNewContext(`(${out.expressionAfter})`, { n: null, g: null, r: null, me: () => true, t: 1 }), p.replacements[0].to);
});

test('future source versions use the same function with reviewed data and renamed bindings', () => {
  const anchor = 'explicit??(generated||session?.heading||generated)??(isChat(kind)?"New chat":"New task")';
  const source = `function next(){return ${anchor}}`;
  const p = profile(source, anchor); p.version = 'future-reviewed-fixture';
  const out = applyTitleFallback(source, p);
  assert.equal(vm.runInNewContext(`(${out.expressionAfter})`, { explicit: null, generated: null, session: null, isChat: () => false, kind: 'task' }), '新任务');
});

test('reviewed release data pins vendor input and supplies the established Chinese title labels', () => {
  const reviewed = require('../resources/candidates/claude-desktop-7.37095.0/title-fallback-reviewed.json');
  assert.throws(() => applyTitleFallback(fixture, reviewed), /source.*hash/i);
  const synthetic = { ...reviewed, source: { ...reviewed.source, sha256: hash(fixture) } };
  const out = applyTitleFallback(fixture, synthetic);
  for (const [chat, want] of [[true, '新建聊天'], [false, '新建任务']]) {
    assert.equal(vm.runInNewContext(`(${out.expressionAfter})`, {
      n: undefined, g: null, r: undefined, me: () => chat, t: 0,
    }), want);
  }
});
