# Cowork native automatic compaction adapter

This optional compatibility adapter applies a native operational compaction
window to an explicitly listed Cowork session. It preserves model and provider
selectors, credentials, tool permissions, and session identities. It does not
rewrite history or invent token usage.

The last **real-session-tested** application build was Claude Desktop
**4.4212.0**, with native agent **2.1.309**. It is no longer installed.
Version-bound source/synthetic validation also covers 4.23118.0 and 4.23118.1,
but neither newer build has passed a real Cowork session. The patcher requires
the exact app version and SDK option-construction binding; unknown builds fail.

The 4.23118.0 official signed candidate has a separately fingerprint-pinned
HostLoop chunk (`D6Y0BUdo`): the existing session-specific option policy was
ported in source, and a real official chunk passes JavaScript syntax parsing
with exactly one ASAR entry changed in memory. Neither a fully translated app
nor a user-session policy installation has been built or activated. The old
ad-hoc-signed app is no longer the current installation: the observed
`/Applications/Claude.app` is official-signed 4.23118.1 without this hook.
An unknown
future Claude build fails closed; never reuse a minified chunk name without a
fresh signed-release fingerprint and native test. The isolated signed smoke
candidate was built from the 4.23118.0 official source and independently
verified to have only `index.chunk-D6Y0BUdo.js` changed; signed resource
integrity passed. It uses a synthetic session ID and `/dev/null` policy, has
the stock English locale, and **must never be installed** as the user's
Chinese app. Real-session native compaction remains untested in the new build.

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

Real-session staging must use an **existing** user-owned `0700` directory on
the internal disk or the mounted encrypted private archive. The ordinary
`/Volumes/.../01-编译与构建` directory is not permitted for a production
session ID, even when it stores harmless smoke candidates. Staging refuses
symlinked or reused paths and writes the session-scoped JavaScript and receipt
as `0600` files with exclusive creation. Do not put those private artifacts
in Git, plain external build areas or public logs. Terminal output is reduced
to version/count/status without session IDs or private paths; validation or
copy failures emit only a sanitized error type, not the private command or
subprocess stderr. The full receipt stays only in the private staging slot.
The synthetic smoke build
uses a separate code path and does not grant real-session installation status.

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
