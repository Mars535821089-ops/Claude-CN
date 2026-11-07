'use strict';

const fs = require('node:fs');
const path = require('node:path');

function enabled(value) {
  return /^(1|true|yes|on)$/i.test(String(value ?? '').trim());
}

/**
 * Apply a smaller native operational window to an explicitly listed session.
 * Never change a model, provider, credential, permission rule, or transcript.
 * Read the policy on each launch so a temporary acceptance window is reversible.
 */
function applyCompactionPolicy(options, { sessionId, policyPath }) {
  const policy = JSON.parse(fs.readFileSync(policyPath, 'utf8'));
  if (policy.version !== 1 || !policy.sessions || typeof policy.sessions !== 'object') {
    throw new Error('Unsupported local compaction policy version or session map');
  }
  const rule = policy.sessions[sessionId];
  if (!rule || rule.enabled === false) {
    return { applied: false, reason: 'session_not_enabled' };
  }
  if (rule.enabled !== true) throw new Error('Compaction policy enabled must be boolean');
  const windowTokens = rule.windowTokens;
  if (!Number.isSafeInteger(windowTokens) || windowTokens < 100000 || windowTokens > 1000000) {
    throw new Error('Compaction policy windowTokens must be an integer between 100000 and 1000000');
  }
  if (options.settings != null && (typeof options.settings !== 'object' || Array.isArray(options.settings))) {
    throw new Error('Compaction policy requires inline settings; existing file-based settings must not be discarded');
  }
  const environment = { ...process.env, ...options.env };
  for (const name of ['DISABLE_COMPACT', 'DISABLE_AUTO_COMPACT']) {
    if (enabled(environment[name])) {
      throw new Error(`${name} explicitly disables native compaction`);
    }
  }
  if (options.managedSettings?.autoCompactEnabled === false) {
    throw new Error('Managed autoCompactEnabled explicitly disables automatic compaction');
  }
  const configDir = environment.CLAUDE_CONFIG_DIR;
  if (configDir) {
    const preferencesPath = path.join(configDir, '.claude.json');
    if (fs.existsSync(preferencesPath)) {
      const preferences = JSON.parse(fs.readFileSync(preferencesPath, 'utf8'));
      if (preferences.autoCompactEnabled === false) {
        throw new Error('Native autoCompactEnabled is explicitly false');
      }
    }
  }

  // Both are supported native controls. The environment value is authoritative
  // even when Cowork disables ordinary user/project setting sources.
  options.env = { ...options.env, CLAUDE_CODE_AUTO_COMPACT_WINDOW: String(windowTokens) };
  options.settings = { ...options.settings, autoCompactWindow: windowTokens };
  if (rule.diagnostics === true) {
    options.debugFile = path.join(path.dirname(policyPath), 'native-compaction-diagnostic.log');
  }
  return { applied: true, sessionId, windowTokens, autoCompactEnabled: true };
}

exports.applyCompactionPolicy = applyCompactionPolicy;
