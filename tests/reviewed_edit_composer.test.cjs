'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const { composeReviewedEdits } = require('../scripts/reviewed_edit_composer.cjs');

const hash = s => createHash('sha256').update(s).digest('hex');
const edit = (source, before, after) => ({ start: source.indexOf(before), end: source.indexOf(before) + before.length, before, after });
const step = (name, source, expected, edits) => ({ name, sourceSha256: hash(source), patchedSha256: hash(expected), edits });

test('both independent same-base edits survive instead of last writer winning', () => {
  const source = 'const sidebar="open";const locale="en";';
  const sidebar = step('sidebar', source, 'const sidebar="closed";const locale="en";', [edit(source, '"open"', '"closed"')]);
  const language = step('language', source, 'const sidebar="open";const locale="zh-CN";', [edit(source, '"en"', '"zh-CN"')]);
  const originalSteps = JSON.stringify([sidebar, language]);
  const result = composeReviewedEdits(source, [sidebar, language]);
  assert.equal(result.source, 'const sidebar="closed";const locale="zh-CN";');
  assert.equal(result.sourceSha256, hash(source));
  assert.equal(result.patchedSha256, hash('const sidebar="closed";const locale="zh-CN";'));
  assert.deepEqual(composeReviewedEdits(source, [language, sidebar]), result);
  assert.equal(JSON.stringify([sidebar, language]), originalSteps, 'input review records are immutable');
  assert.deepEqual(result.edits, [sidebar.edits[0], language.edits[0]]);
});

test('UTF-16 offsets, astral Unicode, CRLF and every untouched character survive', () => {
  const source = '// 😀 保留\r\nconst a="一";\r\nconst b="二";\r\n';
  const a = step('a', source, '// 😀 保留\r\nconst a="甲乙";\r\nconst b="二";\r\n', [edit(source, '"一"', '"甲乙"')]);
  const b = step('b', source, '// 😀 保留\r\nconst a="一";\r\nconst b="丙";\r\n', [edit(source, '"二"', '"丙"')]);
  const out = composeReviewedEdits(source, [b, a]);
  assert.equal(out.source, '// 😀 保留\r\nconst a="甲乙";\r\nconst b="丙";\r\n');
  assert.equal(Buffer.from(out.source).toString('hex'), Buffer.from('// 😀 保留\r\nconst a="甲乙";\r\nconst b="丙";\r\n').toString('hex'));
});

test('stale shared-base claims and steps based on another step output are rejected', () => {
  const source = 'const a=1,b=2;';
  const first = step('first', source, 'const a=3,b=2;', [edit(source, '1', '3')]);
  const second = step('second', 'const a=3,b=2;', 'const a=3,b=4;', [edit(source, '2', '4')]);
  assert.throws(() => composeReviewedEdits(source, [first, second]), /source.*hash|same.base/i);
  assert.throws(() => composeReviewedEdits(source + ' ', [first]), /source.*hash|same.base/i);
});

test('each step must reconstruct its claimed output even when combined output looks right', () => {
  const source = 'const a=1,b=2;';
  const a = step('a', source, 'const a=3,b=4;', [edit(source, '1', '3')]);
  const b = step('b', source, 'const a=1,b=4;', [edit(source, '2', '4')]);
  assert.throws(() => composeReviewedEdits(source, [a, b]), /step.*hash|patched.*hash/i);
});

test('overlap is rejected both inside one step and across different steps', () => {
  const source = 'const value="abc";';
  const outer = edit(source, 'abc', 'XYZ');
  const inner = edit(source, 'b', 'q');
  const a = step('a', source, 'const value="XYZ";', [outer]);
  const b = step('b', source, 'const value="aqc";', [inner]);
  for (const steps of [[a, b], [b, a], [step('both', source, source, [outer, inner])]]) {
    assert.throws(() => composeReviewedEdits(source, steps), /overlap|conflict/i);
  }
});

test('same-point inserts and inserts touching or inside replacement boundaries fail in either order', () => {
  const source = 'const s="abc";';
  const replace = edit(source, 'abc', 'XYZ');
  const a = step('replace', source, 'const s="XYZ";', [replace]);
  for (const point of [replace.start, replace.start + 1, replace.end]) {
    const insertion = { start: point, end: point, before: '', after: '!' };
    const expected = source.slice(0, point) + '!' + source.slice(point);
    const b = step('insert', source, expected, [insertion]);
    assert.throws(() => composeReviewedEdits(source, [a, b]), /overlap|conflict|ambiguous/i);
    assert.throws(() => composeReviewedEdits(source, [b, a]), /overlap|conflict|ambiguous/i);
  }
  const point = source.indexOf('b');
  const b = step('one', source, 'const s="a!bc";', [{ start: point, end: point, before: '', after: '!' }]);
  const c = step('two', source, 'const s="a?bc";', [{ start: point, end: point, before: '', after: '?' }]);
  assert.throws(() => composeReviewedEdits(source, [b, c]), /overlap|conflict|ambiguous/i);
});

test('adjacent nonempty edits, isolated inserts and deletion compose deterministically', () => {
  const source = 'const s="abc";';
  const a = step('a', source, 'const s="Abc";', [edit(source, 'a', 'A')]);
  const b = step('b', source, 'const s="aBc";', [edit(source, 'b', 'B')]);
  const del = step('delete', source, 'const s="ab";', [edit(source, 'c"', '"')]);
  const insert = step('insert', source, '// hi\nconst s="abc";', [{ start: 0, end: 0, before: '', after: '// hi\n' }]);
  assert.equal(composeReviewedEdits(source, [del, insert, b, a]).source, '// hi\nconst s="AB";');
  const end = step('end', source, 'const s="abc";\n// end', [{ start: source.length, end: source.length, before: '', after: '\n// end' }]);
  assert.equal(composeReviewedEdits(source, [end, insert]).source, '// hi\nconst s="abc";\n// end');
});

test('bad spans, wrong before text and non-meaningful changes are rejected', () => {
  const source = 'const a=1;';
  const good = edit(source, '1', '2');
  for (const bad of [
    { ...good, start: -1 }, { ...good, end: source.length + 1 },
    { ...good, start: good.end + 1 }, { ...good, start: 0.5 },
    { ...good, start: NaN }, { ...good, end: Infinity },
    { ...good, start: '8' }, { ...good, before: 'x' },
    { ...good, before: null }, { ...good, after: null },
    { ...good, after: '1' }, { start: 0, end: 0, before: '', after: '' },
  ]) assert.throws(() => composeReviewedEdits(source, [step('bad', source, 'const a=2;', [bad])]), /span|before|change|edit|offset/i);
});

test('malformed review steps, duplicate names and unclaimed changes fail closed', () => {
  const source = 'const a=1;';
  const a = step('same', source, 'const a=2;', [edit(source, '1', '2')]);
  assert.throws(() => composeReviewedEdits(source, [a, { ...a }]), /duplicate.*name/i);
  for (const malformed of [
    { ...a, name: '' }, { ...a, sourceSha256: undefined },
    { ...a, patchedSha256: 'bad' }, { ...a, edits: [] },
    { ...a, edits: undefined }, { ...a, code: 'anything' }, null,
  ]) assert.throws(() => composeReviewedEdits(source, [malformed]), /step|hash|name|edit|fields/i);
  assert.throws(() => composeReviewedEdits(source, null), /steps/i);
});

test('invalid original or composed JavaScript is rejected without executing vendor code', () => {
  const invalid = 'const a=;';
  assert.throws(() => composeReviewedEdits(invalid, []), /syntax|parse/i);
  const source = 'throw new Error("must not execute");const a=1;';
  const bad = step('bad', source, 'throw new Error("must not execute");const a=;', [edit(source, '1', '')]);
  assert.throws(() => composeReviewedEdits(source, [bad]), /syntax|parse/i);
  const good = step('good', source, 'throw new Error("must not execute");const a=2;', [edit(source, '1', '2')]);
  assert.equal(composeReviewedEdits(source, [good]).source, 'throw new Error("must not execute");const a=2;');
});

test('empty composition is a parsed identity and output makes no review-promotion claim', () => {
  const source = 'export const a=1;\r\n';
  const out = composeReviewedEdits(source, []);
  assert.deepEqual(out, { source, sourceSha256: hash(source), patchedSha256: hash(source), edits: [] });
});

test('Unicode surrogate splitting and malformed UTF-16 cannot silently change source bytes', () => {
  const source = 'const emoji="😀";';
  const point = source.indexOf('😀') + 1;
  const split = { start: point, end: point, before: '', after: 'x' };
  assert.throws(() => composeReviewedEdits(source, [step('split', source, source, [split])]), /unicode|surrogate|UTF/i);
  const malformed = 'const s="\ud800";';
  assert.throws(() => composeReviewedEdits(malformed, []), /unicode|surrogate|UTF/i);
  const bad = step('bad', source, 'const emoji="\ud800";', [edit(source, '😀', '\ud800')]);
  assert.throws(() => composeReviewedEdits(source, [bad]), /unicode|surrogate|UTF/i);
});

test('a step with individually nonempty edits but zero net change is not meaningful', () => {
  const source = 'const  a=1;';
  const edits = [
    { start: 5, end: 5, before: '', after: ' ' },
    { start: 6, end: 7, before: ' ', after: '' },
  ];
  assert.throws(() => composeReviewedEdits(source, [step('cancelled', source, source, edits)]), /meaningful|net change/i);
});

test('one reviewed step can contain multiple unsorted edits against the original offsets', () => {
  const source = 'const a="a",b="b";';
  const a = step('two gates', source, 'const a="long",b="other";', [
    edit(source, '"b"', '"other"'), edit(source, '"a"', '"long"'),
  ]);
  assert.equal(composeReviewedEdits(source, [a]).source, 'const a="long",b="other";');
});
