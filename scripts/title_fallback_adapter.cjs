'use strict';

// Pure source-to-source adapter. No vendor module is loaded or executed, and no
// files are read/written here. New releases require a newly reviewed data profile.
const { createHash } = require('node:crypto');
const acorn = require('acorn');
const sha256 = value => createHash('sha256').update(value).digest('hex');
const fail = message => { throw new Error(message); };

function keys(value, expected, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).sort().join('|') !== [...expected].sort().join('|')) {
    fail(`Invalid ${label} profile fields`);
  }
}

function titleShape(node) {
  const is = (n, type, operator) => n?.type === type &&
    (operator === undefined || n.operator === operator);
  const inner = node.left, middle = inner?.right, first = middle?.left;
  const member = first?.right?.expression, fallback = node.right;
  if (!(is(node, 'LogicalExpression', '??') &&
      is(inner, 'LogicalExpression', '??') && is(inner.left, 'Identifier') &&
      is(middle, 'LogicalExpression', '||') && is(first, 'LogicalExpression', '||') &&
      is(first.left, 'Identifier') && is(middle.right, 'Identifier') &&
      first.left.name === middle.right.name && is(first.right, 'ChainExpression') &&
      is(member, 'MemberExpression') && member.optional === true && !member.computed &&
      is(member.object, 'Identifier') && is(member.property, 'Identifier') &&
      is(fallback, 'ConditionalExpression') && is(fallback.test, 'CallExpression') &&
      !fallback.test.optional && is(fallback.test.callee, 'Identifier') &&
      fallback.test.arguments.length === 1 && is(fallback.test.arguments[0], 'Identifier') &&
      is(fallback.consequent, 'Literal') && typeof fallback.consequent.value === 'string' &&
      is(fallback.alternate, 'Literal') && typeof fallback.alternate.value === 'string')) {
    fail('Unreviewable title anchor shape: only final string literals may change');
  }
  return fallback;
}

function walk(node, visit) {
  if (!node || typeof node !== 'object') return;
  if (typeof node.type === 'string') visit(node);
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) value.forEach(child => walk(child, visit));
    else if (value && typeof value === 'object') walk(value, visit);
  }
}

function parse(source) {
  return acorn.parse(source, { ecmaVersion: 'latest', sourceType: 'module' });
}

function splice(source, edits) {
  let result = source;
  for (const edit of [...edits].sort((a, b) => b.start - a.start)) {
    // Replacement strings are JSON-encoded data, never raw JavaScript fragments.
    result = result.slice(0, edit.start) + JSON.stringify(edit.to) + result.slice(edit.end);
  }
  return result;
}

function applyTitleFallback(input, profile) {
  keys(profile, ['schemaVersion', 'version', 'group', 'status', 'source', 'anchor', 'replacements'], 'reviewed');
  keys(profile.source, ['file', 'sha256'], 'source');
  keys(profile.anchor, ['expression', 'sha256', 'expectedOccurrences'], 'anchor');
  if (profile.schemaVersion !== 1 || profile.status !== 'source_reviewed_not_installable' ||
      profile.group !== 'frontend' || typeof profile.version !== 'string' || !profile.version ||
      typeof profile.source.file !== 'string' || !/\.js$/.test(profile.source.file) ||
      profile.source.file.split(/[\\/]/).includes('..') ||
      !/^[a-f0-9]{64}$/.test(profile.source.sha256) ||
      typeof profile.anchor.expression !== 'string' || !profile.anchor.expression ||
      profile.anchor.expectedOccurrences !== 1) fail('Invalid reviewed profile');
  const anchor = profile.anchor.expression;
  if (sha256(anchor) !== profile.anchor.sha256) fail('Title anchor hash mismatch');
  let ast;
  try { ast = parse(anchor); } catch { fail('Invalid title anchor syntax'); }
  const node = ast.body[0]?.expression;
  if (ast.body.length !== 1 || ast.body[0].type !== 'ExpressionStatement' ||
      node?.start !== 0 || node.end !== anchor.length) fail('Invalid title anchor expression');
  const fallback = titleShape(node);
  if (!Array.isArray(profile.replacements) || profile.replacements.length !== 2) {
    fail('Exactly two literal replacements are required');
  }
  const branches = new Set();
  const localEdits = profile.replacements.map(replacement => {
    keys(replacement, ['branch', 'from', 'to'], 'replacement');
    if (!['consequent', 'alternate'].includes(replacement.branch) || branches.has(replacement.branch)) {
      fail('Invalid or duplicate replacement branch');
    }
    branches.add(replacement.branch);
    const literal = fallback[replacement.branch];
    if (typeof replacement.from !== 'string' || replacement.from !== literal.value ||
        typeof replacement.to !== 'string' || !replacement.to.trim() ||
        replacement.to === replacement.from) fail('Invalid replacement literal');
    return { start: literal.start, end: literal.end, from: literal.value, to: replacement.to };
  });
  const patchedAnchor = splice(anchor, localEdits);
  let source;
  if (Buffer.isBuffer(input)) {
    source = input.toString('utf8');
    if (!Buffer.from(source).equals(input)) fail('Source must be valid UTF-8');
  } else if (typeof input === 'string') source = input;
  else fail('Source must be a UTF-8 buffer or string');
  if (source.includes(patchedAnchor) && !source.includes(anchor)) fail('Title source already patched');
  const sourceSha256 = sha256(source);
  if (sourceSha256 !== profile.source.sha256) fail('Source hash mismatch');
  if (source.split(anchor).length - 1 !== 1) fail('Title anchor missing or duplicated');
  let vendorAst;
  try { vendorAst = parse(source); } catch { fail('Invalid vendor source syntax'); }
  const matches = [];
  walk(vendorAst, candidate => {
    if (candidate.type === node.type && source.slice(candidate.start, candidate.end) === anchor) {
      titleShape(candidate);
      matches.push(candidate);
    }
  });
  if (matches.length !== 1) fail('Expected exactly one AST-verified title anchor');
  const at = matches[0].start;
  const edits = localEdits.map(edit => ({ ...edit, start: edit.start + at, end: edit.end + at }))
    .sort((a, b) => a.start - b.start);
  const patched = splice(source, edits);
  parse(patched);
  return {
    source: patched, sourceSha256, patchedSha256: sha256(patched),
    expressionBefore: anchor, expressionAfter: patchedAnchor,
    anchor: { start: at, end: matches[0].end },
    offsetEncoding: 'JavaScript UTF-16 code units', edits,
  };
}

module.exports = { applyTitleFallback };
