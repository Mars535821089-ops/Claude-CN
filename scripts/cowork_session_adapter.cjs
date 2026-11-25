'use strict';

// Pure source transformation: parse vendor code, never require or evaluate it.
// Version-specific bindings and fingerprints belong in reviewed JSON profiles.
const acorn = require('acorn');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const identifier = /^[A-Za-z_$][A-Za-z0-9_$]*$/;
const digest = /^[a-f0-9]{64}$/;
const markerPrefix = 'local-auto-compact-policy:';

function requireProfile(p, targetVersion) {
  if (!p || p.format_version !== 1 || !/^\d+\.\d+\.\d+$/.test(p.target_version) || p.target_version !== targetVersion) {
    throw Error('Reviewed profile version does not match requested target version');
  }
  for (const value of [p.owner_function, p.options_param, p.session_binding?.parameter, p.session_binding?.local]) {
    if (typeof value !== 'string' || !identifier.test(value)) throw Error('Invalid profile identifier');
  }
  if (new Set([p.options_param, p.session_binding.parameter, p.session_binding.local]).size !== 3 || p.session_binding.property !== 'sessionId') {
    throw Error('Invalid profile session binding');
  }
  if (typeof p.logger !== 'string' || !p.logger.split('.').every(x => identifier.test(x))) throw Error('Invalid profile logger');
  const relative = s => typeof s === 'string' && s.split('/').every(x => x && x !== '.' && x !== '..' && /^[\w.-]+$/.test(x));
  if (p.target?.archive !== 'app.asar' || !relative(p.target.path) || !relative(p.module_path) ||
      path.posix.dirname(p.target.path) !== path.posix.dirname(p.module_path) ||
      path.posix.basename(p.module_path) !== 'claude-cn-compaction-policy.cjs') throw Error('Invalid profile module or target path');
  for (const key of ['source_sha256', 'policy_module_sha256', 'proposed_output_sha256']) {
    if (typeof p[key] !== 'string' || !digest.test(p[key])) throw Error('Invalid profile hash: ' + key);
  }
  if (p.anchor !== p.logger + '.info("[HostLoop] sdkOptions after patch: %o",') throw Error('Invalid profile log anchor');
  const policy = fs.readFileSync(path.join(__dirname, 'cowork_compaction_policy.cjs'));
  if (sha(policy) !== p.policy_module_sha256) throw Error('Policy module fingerprint changed');
}

function walk(node, visit, ancestors = []) {
  if (!node || typeof node !== 'object' || typeof node.type !== 'string') return;
  visit(node, ancestors);
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) value.forEach(child => walk(child, visit, [...ancestors, node]));
    else if (value && typeof value === 'object') walk(value, visit, [...ancestors, node]);
  }
}
const isFunction = node => ['FunctionDeclaration', 'FunctionExpression', 'ArrowFunctionExpression'].includes(node.type);
function boundNames(pattern) {
  if (!pattern) return [];
  if (pattern.type === 'Identifier') return [pattern.name];
  if (pattern.type === 'RestElement') return boundNames(pattern.argument);
  if (pattern.type === 'AssignmentPattern') return boundNames(pattern.left);
  if (pattern.type === 'ArrayPattern') return pattern.elements.flatMap(boundNames);
  if (pattern.type === 'ObjectPattern') return pattern.properties.flatMap(p => boundNames(p.type === 'RestElement' ? p.argument : p.value));
  return [];
}

function verifyOwnership(source, ast, p, offset) {
  const owners = [], calls = [];
  walk(ast, (node, ancestors) => {
    if (node.type === 'FunctionDeclaration' && node.id?.name === p.owner_function) owners.push(node);
    if (node.type === 'CallExpression' && node.start === offset) calls.push({ node, ancestors });
  });
  if (owners.length !== 1) throw Error('Expected unique owner function declaration');
  if (calls.length !== 1) throw Error('Anchor is not an executable log call');
  const owner = owners[0], { node: call, ancestors } = calls[0];
  if (ancestors.findLast(isFunction) !== owner) throw Error('Log anchor belongs to a nested or wrong owner function');
  if (!owner.async || owner.generator || owner.params.length !== 2 ||
      owner.params[0].type !== 'Identifier' || owner.params[0].name !== p.options_param ||
      owner.params[1].type !== 'Identifier' || owner.params[1].name !== p.session_binding.parameter) throw Error('Owner parameter binding changed');
  if (source.slice(call.callee.start, call.callee.end) !== p.logger + '.info' ||
      call.arguments[0]?.value !== '[HostLoop] sdkOptions after patch: %o') throw Error('Log call anchor changed');
  const within = ancestors.slice(ancestors.indexOf(owner) + 1);
  if (within[0] !== owner.body || within.slice(1).some(n => !['SequenceExpression', 'ReturnStatement', 'ExpressionStatement'].includes(n.type))) {
    throw Error('Log anchor has a nested block or unsupported scope');
  }
  const binding = p.session_binding;
  const protectedNames = new Set([p.options_param, binding.parameter, binding.local]);
  let bindings = 0;
  walk(owner.body, (node, parents) => {
    if (parents.some(isFunction)) return;
    if (node.type === 'VariableDeclarator') {
      const declaration = parents.at(-1);
      const direct = parents.at(-2) === owner.body || declaration.kind === 'var';
      const names = boundNames(node.id);
      if (direct && names.some(n => protectedNames.has(n))) {
        const properties = node.id.type === 'ObjectPattern' ? node.id.properties : [];
        const matching = properties.filter(prop => prop.type === 'Property' && !prop.computed && prop.key.name === binding.property && prop.value.type === 'Identifier' && prop.value.name === binding.local);
        if (parents.at(-2) !== owner.body || matching.length !== 1 || names.filter(n => protectedNames.has(n)).length !== 1 ||
            node.init?.type !== 'Identifier' || node.init.name !== binding.parameter || node.end >= offset) throw Error('Session binding is not the reviewed parameter destructuring');
        bindings++;
      }
    }
    if ((node.type === 'AssignmentExpression' && boundNames(node.left).some(n => protectedNames.has(n))) ||
        (node.type === 'UpdateExpression' && boundNames(node.argument).some(n => protectedNames.has(n))) ||
        (['ForOfStatement', 'ForInStatement'].includes(node.type) && boundNames(node.left).some(n => protectedNames.has(n)))) throw Error('Protected options/session binding is reassigned');
  });
  if (bindings !== 1) throw Error('Expected unique session binding in owner function');
}

function injection(p) {
  // Avoid capturing future minifier names (e.g. sessionId:p) inside the IIFE.
  const used = new Set([p.options_param, p.session_binding.local, p.logger.split('.')[0], 'require', 'process', '__dirname']);
  const local = preferred => {
    while (used.has(preferred)) preferred += '$';
    used.add(preferred);
    return preferred;
  };
  const policy = local('p'), url = local('pathToFileURL'), module = local('m'), result = local('d'), error = local('z');
  return '/*' + markerPrefix + p.target_version + '*/(await(async()=>{try{'
    + 'const ' + policy + '=process.env.CLAUDE_CN_SESSION_COMPACTION_POLICY;if(!' + policy + ')return;'
    + 'const{' + (url === 'pathToFileURL' ? url : 'pathToFileURL:' + url) + '}=await import("node:url");'
    + 'const ' + module + '=await import(' + url + '(require("node:path").join(__dirname,' + JSON.stringify(path.posix.basename(p.module_path)) + ')).href);'
    + 'const ' + result + '=(' + module + '.default??' + module + ').applyCompactionPolicy(' + p.options_param + ',{sessionId:' + p.session_binding.local + ',policyPath:' + policy + '});'
    + p.logger + '.info("[LocalAutoCompact] policy %o",' + result + ')'
    + '}catch(' + error + '){' + p.logger + '.warn("[LocalAutoCompact] policy unavailable (%s); native defaults retained",' + error + '?.name??"Error")}'
    + '})()),';
}

function patchSource(source, profile, targetVersion) {
  requireProfile(profile, targetVersion);
  if (typeof source !== 'string') throw Error('Source must be an unmodified UTF-8 string');
  if (source.includes(markerPrefix)) throw Error('Compaction adapter marker already applied');
  if (sha(source) !== profile.source_sha256) throw Error('Source fingerprint changed');
  const offset = source.indexOf(profile.anchor);
  if (offset < 0 || source.indexOf(profile.anchor, offset + 1) !== -1) throw Error('Expected unique log anchor');
  const ast = acorn.parse(source, { ecmaVersion: 'latest', sourceType: 'module' });
  verifyOwnership(source, ast, profile, offset);
  const hook = injection(profile);
  const output = source.slice(0, offset) + hook + source.slice(offset);
  acorn.parse(output, { ecmaVersion: 'latest', sourceType: 'module' });
  if (sha(output) !== profile.proposed_output_sha256) throw Error('Proposed output fingerprint changed');
  return output;
}

module.exports = { patchSource };
