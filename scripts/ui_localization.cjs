const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const readline = require('node:readline');
const acorn = require('acorn');
const { scan } = require('./audit_ui.cjs');

const ROOT = path.resolve(__dirname, '..');
const dictionary = JSON.parse(fs.readFileSync(path.join(ROOT, 'resources/ui-reviewed-zh-CN.json'), 'utf8'));
const sha256 = source => crypto.createHash('sha256').update(source).digest('hex');
const parse = source => acorn.parse(source, {
  ecmaVersion: 'latest', sourceType: 'module', allowHashBang: true,
  allowReturnOutsideFunction: true,
});

function structure(source) {
  function visit(node) {
    if (!node || typeof node !== 'object') return node;
    if (Array.isArray(node)) return node.map(visit);
    const result = {};
    for (const [key, value] of Object.entries(node)) {
      if (['start', 'end', 'raw'].includes(key)) continue;
      if (node.type === 'Literal' && key === 'value' && typeof value === 'string') {
        result.value = '<string>';
      } else if (node.type === 'TemplateElement' && key === 'value') {
        result.value = '<template-text>';
      } else result[key] = visit(value);
    }
    return result;
  }
  return visit(parse(source));
}

function replacement(finding, target) {
  if (finding.kind === 'Literal') {
    return [{ start: finding.start, end: finding.end, value: JSON.stringify(target) }];
  }
  const segments = target.split('{…}');
  if (segments.length !== finding.expressions.length + 1) {
    throw new Error(`Template placeholders changed for ${finding.text}`);
  }
  const escape = text => JSON.stringify(text).slice(1, -1)
    .replace(/`/g, '\\`').replace(/\$\{/g, '\\${');
  return segments.map((segment, index) => ({
    ...finding.quasis[index], value: escape(segment),
  }));
}

function applyRules(source, filename, rules, translations = dictionary) {
  const findings = scan(source, filename);
  const edits = [];
  let occurrences = 0;
  for (const rule of rules) {
    const target = translations[rule.source];
    if (typeof target !== 'string' || target === rule.source) {
      throw new Error(`Missing reviewed translation: ${rule.source}`);
    }
    const matches = findings.filter(item => item.context === rule.context && item.text === rule.source);
    if (matches.length !== rule.expected) {
      throw new Error(`${filename}: ${rule.context} ${JSON.stringify(rule.source)}: expected `
        + `${rule.expected} matches, found ${matches.length}; source changed`);
    }
    occurrences += matches.length;
    for (const match of matches) edits.push(...replacement(match, target));
  }
  edits.sort((a, b) => b.start - a.start);
  let patched = source, boundary = source.length;
  for (const edit of edits) {
    if (edit.end > boundary) throw new Error(`Overlapping UI replacements: ${filename}`);
    patched = patched.slice(0, edit.start) + edit.value + patched.slice(edit.end);
    boundary = edit.start;
  }
  assert.deepEqual(structure(patched), structure(source), `JavaScript structure changed: ${filename}`);
  return { source: patched, replacements: occurrences };
}

function checkedPath(root, relative) {
  const target = path.resolve(root, relative);
  if (!target.startsWith(path.resolve(root) + path.sep)) {
    throw new Error(`UI patch path escapes its bundle: ${relative}`);
  }
  let current = path.resolve(root);
  for (const part of relative.split('/')) {
    current = path.join(current, part);
    if (fs.lstatSync(current).isSymbolicLink()) throw new Error(`UI patch symlink refused: ${relative}`);
  }
  return target;
}

function prepareBundle(root, bundle, manifest) {
  if (manifest.preview_only) throw new Error('Review-only UI manifest cannot be applied');
  const prepared = [];
  for (const [file, spec] of Object.entries(manifest.files)) {
    if (!file.startsWith(bundle + '/')) continue;
    if (file.endsWith('.html')) continue;
    const target = checkedPath(root, file.slice(bundle.length + 1));
    const source = fs.readFileSync(target, 'utf8');
    const digest = sha256(source);
    if (digest === spec.patched_sha256) continue;
    if (digest !== spec.sha256) throw new Error(`Unknown UI source fingerprint: ${file}; stopped without writing`);
    const patched = applyRules(source, file, spec.rules);
    if (sha256(patched.source) !== spec.patched_sha256) {
      throw new Error(`UI dictionary or generated manifest changed: ${file}`);
    }
    prepared.push({ target, ...patched });
  }
  if (!Object.keys(manifest.files).some(file => file.startsWith(bundle + '/'))) {
    throw new Error(`No reviewed UI rules for bundle ${bundle}`);
  }
  return prepared;
}

async function generateManifest(version = '4.29.0', translations = dictionary, preview = false) {
  const manifest = { version, ...(preview ? { preview_only: true } : {}), files: {} };
  for await (const line of readline.createInterface({ input: process.stdin, crlfDelay: Infinity })) {
    const { file, source } = JSON.parse(line);
    if (!file.startsWith('ion-dist.asar/assets/v1/')
      && !/^ion-dist\.asar\/[^/]+\.html#inline-\d+$/.test(file)
      && !/^app\.asar\/\.vite\/(?:renderer\/|build\/index[./])/.test(file)) continue;
    const groups = new Map();
    for (const finding of scan(source, file)) {
      // Error/protocol messages may also be consumed by code or by a model.
      if (finding.context === 'message' || finding.context === 'errorMessage') continue;
      if (['preview', 'open'].includes(finding.text) && finding.context !== 'dialog:action') continue;
      if (!Object.hasOwn(translations, finding.text)) continue;
      const key = JSON.stringify([finding.context, finding.text]);
      const rule = groups.get(key) ?? { context: finding.context, source: finding.text, expected: 0 };
      rule.expected++;
      groups.set(key, rule);
    }
    if (groups.size) {
      const rules = [...groups.values()];
      const patched = applyRules(source, file, rules, translations);
      if (scan(patched.source, file).some(finding => rules.some(
        rule => rule.context === finding.context && rule.source === finding.text,
      ))) throw new Error(`Reviewed English UI remains: ${file}`);
      manifest.files[file] = { sha256: sha256(source), patched_sha256: sha256(patched.source), rules };
    }
  }
  process.stdout.write(JSON.stringify(manifest, null, 2) + '\n');
}

async function verifyManifest() {
  const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, 'resources/ui-patch-manifest.json'), 'utf8'));
  const seen = new Set();
  const report = { javascript_files: 0, replacements: 0, remaining_reviewed_literals: 0 };
  for await (const line of readline.createInterface({ input: process.stdin, crlfDelay: Infinity })) {
    const { file, source } = JSON.parse(line);
    const spec = manifest.files[file];
    if (!spec || file.endsWith('.html')) continue;
    if (sha256(source) !== spec.sha256) throw new Error(`Unknown UI source fingerprint: ${file}`);
    const patched = applyRules(source, file, spec.rules);
    if (sha256(patched.source) !== spec.patched_sha256) throw new Error(`Generated UI manifest differs: ${file}`);
    const remaining = scan(patched.source, file).filter(finding => spec.rules.some(
      rule => rule.context === finding.context && rule.source === finding.text,
    ));
    if (remaining.length) throw new Error(`Reviewed English UI remains: ${file}`);
    report.javascript_files++;
    report.replacements += patched.replacements;
    seen.add(file);
  }
  for (const file of Object.keys(manifest.files)) {
    if (!file.endsWith('.html') && !seen.has(file)) throw new Error(`Manifest source file missing: ${file}`);
  }
  process.stdout.write(JSON.stringify(report, null, 2) + '\n');
}

function main() {
  const [root, bundle, mode] = process.argv.slice(2);
  if (!root || !['app.asar', 'ion-dist.asar'].includes(bundle)
    || !['--apply', '--check'].includes(mode)) {
    throw new Error('Usage: node scripts/ui_localization.cjs EXTRACTED_BUNDLE app.asar|ion-dist.asar --check|--apply');
  }
  const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, 'resources/ui-patch-manifest.json'), 'utf8'));
  if (manifest.version !== '4.29.0') throw new Error('Unsupported reviewed UI manifest version');
  // Preflight every target before writing any file; caller owns a disposable stage.
  const prepared = prepareBundle(root, bundle, manifest);
  if (mode === '--apply') {
    for (const item of prepared) fs.writeFileSync(item.target, item.source);
  }
  console.log(JSON.stringify({
    bundle, mode, files: prepared.length,
    replacements: prepared.reduce((count, item) => count + item.replacements, 0),
  }));
}

function patchInline() {
  const items = JSON.parse(fs.readFileSync(0, 'utf8'));
  const patched = items.map(({ file, source, spec, translations = {} }) => {
    if (sha256(source) !== spec.sha256) throw new Error(`Unknown inline UI fingerprint: ${file}`);
    const result = applyRules(source, file, spec.rules, { ...dictionary, ...translations });
    if (sha256(result.source) !== spec.patched_sha256) throw new Error(`Inline UI output differs: ${file}`);
    return result.source;
  });
  process.stdout.write(JSON.stringify(patched));
}

module.exports = { applyRules, prepareBundle, sha256, structure };
if (require.main === module) {
  const stopped = error => {
    console.error(`Reviewed UI patch stopped: ${error.message}`);
    process.exitCode = 1;
  };
  if (process.argv[2] === '--generate-manifest') generateManifest().catch(stopped);
  else if (process.argv[2] === '--preview-manifest') {
    const [version, resource] = process.argv.slice(3);
    try {
      if (!/^\d+\.\d+\.\d+$/.test(version) || !resource) throw new Error('Preview version/dictionary required');
      const translations = { ...dictionary, ...JSON.parse(fs.readFileSync(resource, 'utf8')) };
      generateManifest(version, translations, true).catch(stopped);
    } catch (error) { stopped(error); }
  }
  else if (process.argv[2] === '--verify-manifest') verifyManifest().catch(stopped);
  else if (process.argv[2] === '--patch-inline') {
    try { patchInline(); } catch (error) { stopped(error); }
  }
  else try { main(); } catch (error) { stopped(error); }
}
