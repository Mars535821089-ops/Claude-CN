# Cowork native automatic compaction adapter

This optional compatibility adapter applies a native operational compaction
window to an explicitly listed Cowork session. It preserves model and provider
selectors, credentials, tool permissions, and session identities. It does not
rewrite history or invent token usage.

The tested application build is **Claude Desktop 4.4212.0**, with native agent
**2.1.309**. The patcher requires both the exact app version and the expected SDK
option-construction binding. Unknown builds are rejected.

The 4.23118.0 official signed candidate has a separately fingerprint-pinned
HostLoop chunk (`D6Y0BUdo`): the existing session-specific option policy was
ported in source, and a real official chunk passes JavaScript syntax parsing
with exactly one ASAR entry changed in memory. Neither a fully translated app
nor a user-session policy installation has been built or activated. The old
ad-hoc-signed app remains installed and keeps its current hook. An unknown
future Claude build fails closed; never reuse a minified chunk name without a
fresh signed-release fingerprint and native test.

`scripts/cowork_compaction_policy.cjs` reads a local policy at each worker
launch. It sets the supported `CLAUDE_CODE_AUTO_COMPACT_WINDOW` environment
variable and the inline `autoCompactWindow` option. Ordinary user/project
settings alone are insufficient because Cowork restricts setting sources.

The policy format contains no credentials:

```json
{
  "version": 1,
  "sessions": {
    "local-SESSION-ID": {
      "enabled": true,
      "windowTokens": 700000
    }
  }
}
```

The configured window is an operational budget, not a claim about a provider's
maximum context. Native compaction reserves its own buffer and may start before
the configured window is full. A window below the fixed system/tool prefix can
make compaction ineffective; do not use an undersized production window.

`scripts/patch_cowork_compaction.py` stages a clone of the installed app, changes
only the expected HostLoop archive entry, validates JavaScript, updates Electron
archive integrity, and re-signs the clone with existing entitlements. It does not
activate the staged app or change user profiles. Activate only while the app and
its workers are idle; retain the previous app as a rollback.

Validation for the repaired session included an ordinary read-only message at a
temporary 120,000-token window. Its native transcript recorded **`trigger: auto`,
86,995 → 2,966 tokens**, then continued the pending message. The production
window was restored to **700,000**, observed in the actual worker environment
and inline options, and confirmed by `/context`.

App upgrades can replace the archive hook. After an update, re-detect the build
and revalidate the adapter; do not automatically reuse an old archive or disable
integrity verification. Setting a session rule's `enabled` to `false` disables
that adapter on its next worker launch.

Tests:

```sh
node --test tests/*.cjs
python3.12 -m unittest discover -s tests -p 'test_*.py'
```
