'use strict';
// Only isolated, reviewed AST slices are evaluated, never a vendor bundle.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const vm = require('node:vm');
const acorn = require('acorn');
const adapterPath = path.join(__dirname, '../scripts/sidebar_lifecycle_adapter.cjs');
const adapter = fs.existsSync(adapterPath) ? require(adapterPath) : {};
const profilePath = path.join(__dirname, '../resources/candidates/claude-desktop-7.37095.0/sidebar-reviewed.json');
const archivePath = '/Volumes/Mars-私人服务器/内置盘写入统一管理/01-编译与构建/Claude-CN/official-7.37095.0-20261124T230340Z/Claude.app/Contents/Resources/ion-dist.asar';
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const plain = value => JSON.parse(JSON.stringify(value));
const fixturePath = path.join(__dirname, '../resources/candidates/claude-desktop-7.37095.0/sidebar-native-fixture.json');
// Independent reviewed file pins. Do not recompute these from the adapter.
const FIXTURE_SHA256 = 'dbe67d4b036a1e6edb1d1f16e3c48f750f6b92feec0f15eb71682d390f5d9ab8';
const PROFILE_SHA256 = '74734bbdad9a0aece15ecc10cf411a6b55ec391ebfa1dc4283a37a66202e1524';
const fixtureRaw = fs.readFileSync(fixturePath);
assert.equal(hash(fixtureRaw), FIXTURE_SHA256, 'reviewed native fixture drift');
const nativeFixture = JSON.parse(fixtureRaw);
function originalProfile() {
  const raw = fs.readFileSync(profilePath);
  assert.equal(hash(raw), PROFILE_SHA256, 'reviewed production profile drift');
  assert.equal(nativeFixture.reviewed_profile_sha256, PROFILE_SHA256);
  const profile = JSON.parse(raw);
  assert.equal(nativeFixture.original_source_sha256, profile.source_sha256);
  assert.equal(profile.source_sha256, '9c3209d7331769a49ecdc292329ce4cc07418de4e8f47aa50888e107098923b4');
  return profile;
}
function sourceFixture() {
  originalProfile();
  assert.equal(hash(nativeFixture.source), nativeFixture.fixture_source_sha256);
  return nativeFixture.source;
}

function walk(node, visit) {
  if (!node || typeof node !== 'object') return;
  visit(node);
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) value.forEach(n => walk(n, visit));
    else if (value && typeof value === 'object') walk(value, visit);
  }
}
function slices(source) {
  const ast = acorn.parse(source, {ecmaVersion: 'latest', sourceType: 'module'});
  const functions = {}, values = {}, properties = {};
  walk(ast, node => {
    if (node.type === 'FunctionDeclaration') functions[node.id.name] = source.slice(node.start, node.end);
    if (node.type === 'VariableDeclarator' && node.id.type === 'Identifier' && node.init)
      values[node.id.name] = source.slice(node.init.start, node.init.end);
    if (node.type === 'Property' && node.key?.name === 'setCollapsedPref')
      properties.setter = source.slice(node.value.start, node.value.end);
    if (node.type === 'ObjectExpression' && node.properties.some(p => p.key?.name === 'partialize') &&
        node.properties.some(p => p.key?.name === 'merge')) {
      const p = node.properties.find(p => p.key?.name === 'partialize');
      if (source.slice(p.start, p.end).includes('pinnedOrder'))
        for (const p of node.properties) if (['merge', 'partialize'].includes(p.key?.name))
          properties[p.key.name] = source.slice(p.value.start, p.value.end);
    }
  });
  // The store initializer is reviewed independently of the adapter's selectors.
  let initializer;
  walk(ast, node => {
    if (node.type === 'ObjectExpression' && node.properties.some(p => p.key?.name === 'setCollapsedPref'))
      initializer = node;
  });
  assert.ok(initializer && properties.merge && properties.partialize && properties.setter);
  const initialProperties = initializer.properties.filter(p => p.type === 'Property' && p.value &&
    ['Literal', 'UnaryExpression', 'ArrayExpression', 'ObjectExpression'].includes(p.value.type));
  const initial = '{' + initialProperties.map(p => source.slice(p.start, p.end)).join(',') + '}';
  return {functions, values, properties, initial};
}
function native(source, composed, narrow = false) {
  const f = slices(source);
  const context = vm.createContext({b1: composed, X: x => x !== null && typeof x === 'object' && !Array.isArray(x),
    E1: () => {}, SJ: {getItem: () => null}, window: {matchMedia: () => ({matches: narrow})}});
  const evaluate = text => vm.runInContext('(' + text + ')', context, {timeout: 1000});
  for (const key of ['O$', 'D$', 'W$', 'M$', 'y1', 'v1']) context[key] = evaluate(f.values[key]);
  // Real native width/normalization/guard/state functions. Nonempty legacy group
  // migrations are not fabricated; whole merge is statically preserved below.
  for (const key of ['k$', 'A$', 'j$', 'Vpe', 'g1', 'x1', 'S1', 'C1', 'U1', 'yQ',
    'AQ', 't1', 'H$', 'U$', 'G$', 'lme', 'm1', 'vme', 'hme', 'l1'])
    context[key] = evaluate(f.functions[key]);
  let state = {...evaluate(f.initial), ...context.v1};
  const merge = evaluate(f.properties.merge), partialize = evaluate(f.properties.partialize);
  context.e = update => { state = {...state, ...update(state)}; };
  const setter = evaluate(f.properties.setter);
  return {get: () => plain(state), hydrate: value => {state = merge(structuredClone(value), state);},
    prepaint: raw => plain(context.Vpe(raw)), collapsed: () => context.U1(state),
    open: () => setter(false), close: () => setter(true), persisted: () => plain(partialize(state))};
}
const original = sourceFixture();
function reviewed() {
  assert.equal(typeof adapter.applyReviewedSidebar, 'function', 'source-pinned adapter is not implemented');
  // The reduced, parse-only scaffold has its own independently reviewed pins;
  // the production source/output pins are tested only in optional integration.
  const profile = {...originalProfile(), source_sha256: nativeFixture.fixture_source_sha256,
    patched_sha256: nativeFixture.fixture_patched_sha256};
  return {profile, apply: adapter.applyReviewedSidebar};
}
function saved() {
  return {collapsed: false, composedSidebarOpen: true, composedSidebarMounted: true,
    sidebarWidth: 217, isNarrowViewport: false, isRouteDrilldown: false,
    customGroupsByScope: {}, codeSidebarByScope: {}, navPinV: 2,
    pinnedOrder: ['pin-a'], homeProjectsPinnedOrder: ['project-a'],
    folderAppearanceByKey: {folder: {icon: 'Folder', color: 'blue'}},
    groupByByMode: {code: 'folder'}, sortByByMode: {code: 'alpha'},
    foldKeepsTitleRow: true, clickKeepsTitleRow: true,
    opaqueFutureField: {keep: ['all', 'data']}};
}
// Catches a missing adaptation and reproduces the original failure first.
test('official7 traditional hydration reproduces expansion; adapter collapses it', () => {
  const old = native(original, false); old.hydrate(saved()); assert.equal(old.collapsed(), false);
  const {profile, apply} = reviewed();
  const fixed = native(apply(original, profile), false); fixed.hydrate(saved());
  assert.equal(fixed.collapsed(), true);
});
for (const composed of [false, true]) test(`fresh/hydrated/refresh and native one-click close composed=${composed}`, () => {
  const {profile, apply} = reviewed(), output = apply(original, profile);
  const fixed = native(output, composed);
  assert.equal(fixed.collapsed(), true);
  fixed.hydrate(saved()); assert.equal(fixed.collapsed(), true);
  fixed.open(); assert.equal(fixed.collapsed(), false);
  fixed.close(); assert.equal(fixed.collapsed(), true);
  fixed.open(); const refreshed = native(output, composed); refreshed.hydrate(fixed.get());
  assert.equal(refreshed.collapsed(), true);
});
for (const composed of [false, true]) test(`all other fields, narrow saved width and native persistence preserved composed=${composed}`, () => {
  const {profile, apply} = reviewed(), output = apply(original, profile), input = saved();
  input.isNarrowViewport = true;
  const old = native(original, composed), fixed = native(output, composed);
  old.hydrate(input); fixed.hydrate(input);
  const expected = old.get(); Object.assign(expected, {collapsed: true, composedSidebarOpen: false, composedSidebarMounted: false});
  assert.deepEqual(fixed.get(), expected);
  assert.equal(fixed.get().sidebarWidth, 217);
  assert.equal(fixed.get().isNarrowViewport, true);
  assert.deepEqual(fixed.get().opaqueFutureField, {keep: ['all', 'data']});
  const before = slices(original), after = slices(output);
  assert.equal(after.properties.setter, before.properties.setter);
  assert.equal(after.properties.partialize, before.properties.partialize);
  const a = old.persisted(), b = fixed.persisted(); a.collapsed = true;
  assert.deepEqual(b, a);
  // Native setters must keep composed title retention semantics, including a
  // narrow viewport which refuses composed opening through its original guard.
  old.open(); fixed.open(); assert.equal(fixed.collapsed(), old.collapsed());
  old.close(); fixed.close(); assert.equal(fixed.collapsed(), old.collapsed());
  assert.equal(fixed.get().foldKeepsTitleRow, old.get().foldKeepsTitleRow);
  assert.equal(fixed.get().clickKeepsTitleRow, old.get().clickKeepsTitleRow);
});
test('prepaint corrupt input and width clamps stay native except collapse', () => {
  const {profile, apply} = reviewed(), old = native(original, false), fixed = native(apply(original, profile), false);
  for (const raw of [null, '{}', 'broken', 'null', '{"state":{"collapsed":false,"sidebarWidth":217}}',
    '{"state":{"sidebarWidth":50}}', '{"state":{"sidebarWidth":900}}']) {
    assert.equal(fixed.prepaint(raw).collapsed, true);
    assert.equal(fixed.prepaint(raw).sidebarWidth, old.prepaint(raw).sidebarWidth);
  }
});
test('wrong, partially applied, duplicate, already applied and output hash mismatches fail closed', () => {
  const {profile, apply} = reviewed(), out = apply(original, profile);
  assert.equal(hash(out), profile.patched_sha256);
  assert.throws(() => apply(out, profile), /already|source/i);
  assert.throws(() => apply(original + '\n', profile), /source/i);
  assert.throws(() => apply(original, {...profile, patched_sha256: '0'.repeat(64)}), /output/i);
  for (const changed of [original.replace(profile.anchors[0].source, profile.anchors[0].replacement),
    original + ';' + profile.anchors[0].source + ';']) {
    assert.throws(() => apply(changed, {...profile, source_sha256: hash(changed)}), /anchor|partial|applied/i);
  }
  assert.throws(() => apply(original, {...profile, anchors: profile.anchors.slice(1)}), /four|anchor/i);
  const damaged = structuredClone(profile); damaged.protected_nodes[0].sha256 = '0'.repeat(64);
  assert.throws(() => apply(original, damaged), /protected/i);
});
test('only the four reviewed replacements occur and future generation is data-driven', () => {
  const {profile, apply} = reviewed(), output = apply(original, profile);
  let reversed = output;
  for (const a of [...profile.anchors].reverse()) reversed = reversed.replace(a.replacement, a.source);
  assert.equal(reversed, original, 'no changes outside the four reviewed anchors');
  // A future reviewed profile can describe another uniquely pinned source;
  // there is no copied adapter or source-version constant to update.
  const futureSource = original + '\n/* synthetic future generation */';
  const future = {...profile, version: '8.0.0', source_sha256: hash(futureSource),
    patched_sha256: hash(output + '\n/* synthetic future generation */')};
  assert.equal(apply(futureSource, future), output + '\n/* synthetic future generation */');
});
for (const composed of [false, true]) test(`native setters preserve every field and physical narrow guard composed=${composed}`, () => {
  const {profile, apply} = reviewed(), output = apply(original, profile);
  for (const physicalNarrow of [false, true]) {
    const old = native(original, composed, physicalNarrow), fixed = native(output, composed, physicalNarrow);
    const initial = {...saved(), collapsed: true, composedSidebarOpen: false, composedSidebarMounted: false};
    old.hydrate(initial); fixed.hydrate(initial);
    for (const operation of ['open', 'close', 'open', 'close']) {
      old[operation](); fixed[operation]();
      assert.deepEqual(fixed.get(), old.get(), `native ${operation} must keep title/width/pins/transients`);
      if (composed && physicalNarrow) assert.equal(fixed.collapsed(), true, 'native matchMedia guard refuses opening');
    }
  }
});
test('each reviewed anchor individually rejects partial and duplicate input; overlap is rejected', () => {
  const {profile, apply} = reviewed();
  for (const anchor of profile.anchors) {
    for (const source of [original.replace(anchor.source, anchor.replacement), original + ';' + anchor.source])
      assert.throws(() => apply(source, {...profile, source_sha256: hash(source)}), /anchor|partial|duplicate/i);
  }
  const overlap = structuredClone(profile);
  overlap.anchors[1].source = 'collapsed:!1,sidebarWidth:288';
  assert.throws(() => apply(original, overlap), /overlap/i);
});

// Default CI is fully offline: all 11 core tests run, with no skipped tests.
// Opt in only when the separately preserved official source is available.
if (process.env.CLAUDE_SIDEBAR_REAL7_INTEGRATION === '1') {
  test('optional real7 integration: reviewed native slices and actual output pin', () => {
    const profile = originalProfile(), raw = fs.readFileSync(archivePath);
    assert.equal(hash(raw), profile.source_archive_sha256);
    const header = JSON.parse(raw.subarray(16, 16 + raw.readInt32LE(12)));
    let entry = header;
    for (const part of profile.resource.path.split('/')) entry = entry.files[part];
    const start = 8 + raw.readUInt32LE(4) + Number(entry.offset);
    const source = raw.subarray(start, start + entry.size).toString('utf8');
    assert.equal(hash(source), nativeFixture.original_source_sha256);
    for (const slice of nativeFixture.original_ast_slices)
      assert.equal(hash(source.slice(slice.original_char_start, slice.original_char_end)), slice.sha256);
    const output = adapter.applyReviewedSidebar(source, profile);
    assert.equal(hash(output), 'c6a261bae2c177c91f86a2c096e8f49e9a864086fbb596122e0927162a3de455');
    const before = slices(source), after = slices(output);
    assert.equal(before.properties.setter, after.properties.setter);
    assert.equal(before.properties.partialize, after.properties.partialize);
  });
}
