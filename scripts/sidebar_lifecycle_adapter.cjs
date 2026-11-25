'use strict';
// Pure source transformation. No file writes and no vendor code evaluation.
// Future releases supply a separately reviewed profile, not another adapter.
const acorn = require('acorn');
const crypto = require('node:crypto');
const sha = source => crypto.createHash('sha256').update(source, 'utf8').digest('hex');
const parse = source => acorn.parse(source, {ecmaVersion: 'latest', sourceType: 'module'});
function walk(node, visit) {
  if (!node || typeof node !== 'object') return;
  visit(node);
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) value.forEach(n => walk(n, visit));
    else if (value && typeof value === 'object') walk(value, visit);
  }
}
function checkProtected(source, ast, protections) {
  for (const protection of protections) {
    let matches = 0;
    walk(ast, node => {
      if (node.type === 'Property' && (node.key?.name ?? node.key?.value) === protection.property &&
          sha(source.slice(node.start, node.end)) === protection.sha256) matches++;
    });
    if (matches !== 1) throw new Error('protected native node changed: ' + protection.property);
  }
}
function applyReviewedSidebar(source, profile) {
  if (typeof source !== 'string' || !profile || profile.schema !== 1 ||
      typeof profile.version !== 'string' || !profile.version ||
      !/^[a-f0-9]{64}$/.test(profile.source_sha256) ||
      !/^[a-f0-9]{64}$/.test(profile.patched_sha256) || profile.source_sha256 === profile.patched_sha256)
    throw new Error('invalid reviewed source/output profile');
  if (!Array.isArray(profile.anchors) || profile.anchors.length !== 4)
    throw new Error('exactly four reviewed anchors required');
  const protections = profile.protected_nodes;
  if (!Array.isArray(protections) || protections.length !== 2 ||
      protections.map(p => p.property).sort().join(',') !== 'partialize,setCollapsedPref' ||
      protections.some(p => !/^[a-f0-9]{64}$/.test(p.sha256)))
    throw new Error('protected native node fingerprints required');
  const digest = sha(source);
  if (digest === profile.patched_sha256) throw new Error('already applied source rejected');
  if (digest !== profile.source_sha256) throw new Error('reviewed source fingerprint changed');
  const edits = profile.anchors.map(anchor => {
    if (!anchor || typeof anchor.source !== 'string' || !anchor.source ||
        typeof anchor.replacement !== 'string' || !anchor.replacement ||
        anchor.source === anchor.replacement || anchor.expected !== 1)
      throw new Error('invalid reviewed anchor');
    const start = source.indexOf(anchor.source);
    if (start < 0 || source.indexOf(anchor.source, start + 1) >= 0)
      throw new Error('missing, partial or duplicate anchor: ' + anchor.name);
    return {start, end: start + anchor.source.length, replacement: anchor.replacement};
  }).sort((a, b) => a.start - b.start);
  if (edits.some((edit, i) => i && edit.start < edits[i - 1].end))
    throw new Error('overlapping reviewed anchors');
  checkProtected(source, parse(source), protections);
  let output = source;
  for (const edit of edits.reverse()) output = output.slice(0, edit.start) + edit.replacement + output.slice(edit.end);
  if (sha(output) !== profile.patched_sha256) throw new Error('reviewed output fingerprint changed');
  checkProtected(output, parse(output), protections);
  return output;
}
module.exports = {applyReviewedSidebar};
