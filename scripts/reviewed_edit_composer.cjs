'use strict';

const { createHash } = require('node:crypto');
const acorn = require('acorn');
const hash = text => createHash('sha256').update(text, 'utf8').digest('hex');
const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);

function fields(value, expected, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).sort().join('|') !== [...expected].sort().join('|')) {
    throw new Error(`Invalid ${label} fields`);
  }
}

function unicode(text) {
  if (Buffer.from(text, 'utf8').toString('utf8') !== text) {
    throw new Error('Malformed Unicode / UTF-16 surrogate sequence');
  }
}

function splitsSurrogate(source, offset) {
  const before = source.charCodeAt(offset - 1), after = source.charCodeAt(offset);
  return before >= 0xd800 && before <= 0xdbff && after >= 0xdc00 && after <= 0xdfff;
}

function parse(source, label) {
  try {
    acorn.parse(source, { ecmaVersion: 'latest', sourceType: 'module', allowHashBang: true });
  } catch (cause) {
    throw new Error(`${label} JavaScript syntax parse failed: ${cause.message}`, { cause });
  }
}

function disjoint(edits) {
  for (let i = 0; i < edits.length; i++) {
    for (let j = i + 1; j < edits.length; j++) {
      const a = edits[i], b = edits[j];
      const ai = a.start === a.end, bi = b.start === b.end;
      // Insertions touching either endpoint of a replacement are rejected too:
      // accepting them would impose a new ordering not present in either review.
      const conflict = ai && bi ? a.start === b.start :
        ai ? a.start >= b.start && a.start <= b.end :
        bi ? b.start >= a.start && b.start <= a.end :
        a.start < b.end && b.start < a.end;
      if (conflict) throw new Error('Overlapping or ambiguous edit conflict');
    }
  }
}

function apply(source, edits) {
  let result = source;
  for (const edit of [...edits].sort((a, b) => b.start - a.start)) {
    result = result.slice(0, edit.start) + edit.after + result.slice(edit.end);
  }
  return result;
}

/**
 * Compose independently reviewed edits against ONE original UTF-8 JS string.
 * All offsets are original JavaScript UTF-16 code-unit offsets, not byte offsets.
 * Empty steps array is a parsed identity; every supplied step must change text.
 * Hashes verify claims, not review authority: the composed hash needs root review.
 * Pure operation: never loads vendor code, writes files, or mutates input records.
 */
function composeReviewedEdits(source, steps) {
  if (typeof source !== 'string') throw new Error('Source must be a JavaScript string');
  unicode(source);
  if (!Array.isArray(steps)) throw new Error('Steps must be an array');
  parse(source, 'Original');
  const sourceSha256 = hash(source), names = new Set(), all = [];
  for (const step of steps) {
    fields(step, ['name', 'sourceSha256', 'patchedSha256', 'edits'], 'step');
    if (typeof step.name !== 'string' || !step.name.trim() || step.name !== step.name.trim()) {
      throw new Error('Invalid step name');
    }
    if (names.has(step.name)) throw new Error('Duplicate step name');
    names.add(step.name);
    if (!digest(step.sourceSha256) || step.sourceSha256 !== sourceSha256) {
      throw new Error(`Step ${step.name} source hash mismatch: all steps require the same base`);
    }
    if (!digest(step.patchedSha256)) throw new Error('Invalid step patched hash');
    if (!Array.isArray(step.edits) || step.edits.length === 0) throw new Error('Step edits must be nonempty');
    const edits = step.edits.map(edit => {
      fields(edit, ['start', 'end', 'before', 'after'], 'edit');
      const { start, end, before, after } = edit;
      if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) ||
          start < 0 || end < start || end > source.length) throw new Error('Invalid edit span offsets');
      if (splitsSurrogate(source, start) || splitsSurrogate(source, end)) {
        throw new Error('Edit span splits a Unicode UTF-16 surrogate pair');
      }
      if (typeof before !== 'string' || typeof after !== 'string') throw new Error('Edit before/after must be strings');
      unicode(after);
      if (source.slice(start, end) !== before) throw new Error('Edit before text does not match original source span');
      if (before === after) throw new Error('Edit must make a meaningful text change');
      return { start, end, before, after };
    });
    disjoint(edits);
    const stepOutput = apply(source, edits);
    if (stepOutput === source) throw new Error('Step must make a meaningful net change');
    if (hash(stepOutput) !== step.patchedSha256) {
      throw new Error(`Step ${step.name} patched output hash mismatch`);
    }
    all.push(...edits);
  }
  disjoint(all);
  const edits = all.sort((a, b) => a.start - b.start);
  const patched = apply(source, edits);
  parse(patched, 'Composed output');
  return { source: patched, sourceSha256, patchedSha256: hash(patched), edits };
}

module.exports = { composeReviewedEdits };
