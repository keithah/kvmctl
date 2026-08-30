# Target-Bound KVM Final Safety Remediation

## Verification results

```text
Focused semantic/MCP/CLI tests:
37 passed in 0.14s

Full suite:
259 passed, 3 skipped in 23.30s

compileall:
exit 0

git diff --check:
exit 0
```

## Remediation

- Added explicit named-workflow authorization and exact token-bound execution.
- Preserved exact-plan checks for inline execution and workflow revision/target checks.
- Persisted authorization capabilities through the secure CLI/MCP authorization store and retained per-context executor state for separate dispatcher calls.
- Added compatibility filtering for legacy thin CLI adapters without changing the real semantic authorization gate.
- Extended rejection journal records with final result, timestamp, and duration evidence.
- Migrated regression tests to the plan -> authorize -> execute lifecycle and covered the public workflow authorization tool registry.

## Final safety-gap verification (2026-08-28)

```text
Focused final safety-gap and sequence tests:
46 passed in 0.15s

Full suite:
263 passed, 3 skipped in 23.20s

compileall:
exit 0

git diff --check:
exit 0
```

Final gap coverage includes endpoint/session-derived authorization binding, flock-protected atomic single-use consumption, bounded screen assertion capture/OCR with fail-closed behavior and exact authorized journal binding, and centralized aborted checkpoints for pre-execution rejection paths.

## Persisted capability schema and TTL hardening (2026-08-29)

`FileAuthorizationStore` now rejects MAC-valid capabilities with non-boolean use state, invalid token/binding/session/workflow types, non-canonical plans, mismatched hashes, expired or overlong finite expiries, and any unexpected persisted fields before consumption. All records are validated before a rewrite; malformed records remain byte-for-byte unchanged. Added regression coverage for falsey use values, binding/workflow/session type corruption, distant expiry, and preservation.

## Final follow-up verification (2026-08-28)

```text
Persistence/journal/deadline focused tests:
52 passed in 0.17s

Full suite:
265 passed, 3 skipped in 23.06s

compileall:
exit 0

git diff --check:
exit 0
```

Follow-up changes harden session, key, lock, temporary, and authorization files with fail-closed owner/mode checks; preserve multiple persisted capabilities with atomic flock-protected append/consume; journal semantic policy, workflow-resolution, and adapter rejection paths; use non-empty deterministic rejection evidence identifiers; and fail immediately when screen capture/OCR deadlines expire.

## Final review closure verification (2026-08-28)

```text
Focused final-remediation tests:
52 passed in 0.17s

Full suite:
271 passed, 3 skipped in 23.38s

compileall:
exit 0

git diff --check:
exit 0
```

Final closure changes reject non-finite and overlong authorization TTLs; journal replay and in-memory identity rejection with bound target, timestamps, duration, final-result, and verification evidence; and centralize safe error normalization for sequence CLI/MCP envelopes and journal reasons.

## Final safety-gap remediation verification (2026-08-28)

```text
Focused remediation tests:
63 passed in 0.32s

Full suite:
274 passed, 3 skipped in 22.92s

compileall:
exit 0

git diff --check:
exit 0
```

Implemented a SHA-256 endpoint/device keyed flock lock with fail-closed acquisition and process-local contention handling; propagated canonical plan hashes into semantic policy rejection checkpoints; added JSON workflow-file loading via `--workflows`/`KVMCTL_WORKFLOWS_FILE` for standalone CLI and MCP invocations; and retained one bounded screen worker per executor, poisoning it after timeout instead of accumulating timed-out threads. Added cross-process lock and worker-bound regression coverage.

## Final review expiry, persistence, and catalog parity remediation (2026-08-28)

```text
Focused remediation tests:
78 passed in 1.58s

Full suite:
279 passed, 3 skipped in 23.10s

compileall and git diff --check:
exit 0
```

Rechecked authorization expiry and sequence deadlines before each HID mutation, bounded blocking waits with fail-closed timeout behavior, replaced predictable staging with exclusive 0600 temporary files plus file/directory fsync and symlink rejection, and moved `kvm_workflow_authorize` into the canonical operation catalog while removing duplicate MCP registry entries. Added advancing-clock, blocking-wait, symlink, staging, and catalog-parity regressions.

## Effective HTTP Host binding remediation (2026-08-28)

Canonicalized endpoint identity now includes scheme, network hostname, effective port, and configured HTTP Host/virtual-host authority. CLI session persistence and sequence authorization binding derive this identity from the constructed client rather than caller-supplied binding fields. Equivalent clients with the same URL and Host can replay persisted state; a different Host is rejected. Invalid or ambiguous Host authorities fail closed. Added same-URL/different-Host rejection and matching-Host persistence regressions.

## Boundary hardening remediation (2026-08-29)

- Replaced permissive Host handling with strict HTTP authority parsing: valid DNS names, IPv4, bracketed IPv6, and 1-65535 ports are canonicalized consistently; userinfo, whitespace/control characters, malformed brackets/IPv6, ambiguous colons, invalid ports, and path-like authorities are rejected.
- Persistence now validates every parent component without following symlinks, creates private directories/files safely, uses exclusive no-follow secret creation, and keeps atomic file plus directory fsync persistence.
- Authorization and device locks use no-follow descriptor opens and validate regular-file ownership and exact 0600 mode; unsafe symlink, type, parent, and race outcomes fail closed. Portable protections are limited to these OS-supported no-follow and metadata checks.
- CLI `sequence-execute --plan` now passes the supplied plan, approval state, and TTL into semantic execution for exact-plan stale/changed rejection.
- Added Host positive/negative, symlinked parent/lock, and CLI plan propagation regressions.
- Follow-up hardening canonicalizes numeric ports, rejects ambiguous numeric IPv4 authorities, prevents recursive lock-directory setup from traversing symlinked parents, validates authorization lock metadata from the opened descriptor, and excludes caller-controlled fallback URL/Host fields from non-HTTP binding identities.

```text
Focused rejection/integrity/wait tests:
37 passed in 0.18s
```

Additional hardening makes semantic validation and session-bound authorization rejection paths emit redacted aborted checkpoints without allowing journal failures to mask the original exception. Authorization-store MAC and structural corruption now raises a typed integrity error before any write, preserving the existing state; execution converts that failure into a safe rejection. Injected blocking waits now use one process-bounded executor worker rather than spawning unmanaged daemon threads per attempt.

## Boundary hardening verification (2026-08-29)

```text
Focused endpoint/persistence/CLI tests:
51 passed in 0.26s

Full suite:
301 passed, 3 skipped in 22.76s

compileall:
exit 0

git diff --check:
exit 0
```

## Strict input and rejection-integrity remediation (2026-08-29)

`SequenceExecutor` now treats aborted, cleanup-failure, and screen-mismatch journal checkpoints as best-effort evidence, preserving the original structured execution/preflight result and redacting exception details. Integer-valued sequence fields reject fractional numbers and booleans instead of truncating. `FileAuthorizationStore.put()` validates the complete canonical capability schema, bindings, plan hash, and finite TTL before creating or rewriting persistence state; invalid capabilities leave existing bytes unchanged. Added regression coverage for journal failures, strict integer fields, malformed capabilities, and invalid expiries.

Focused remediation tests: 76 passed

## MCP workflow authorization strict-input remediation (2026-08-29)

The direct MCP dispatcher now requires non-empty workflow name and revision strings, exact boolean approval, non-empty optional targets, and finite integral TTL values within the 30-second authorization policy. It no longer coerces approval or TTL values, and missing approval is rejected before reaching the semantic authorization gate. Added regressions for string approval, missing/null and malformed fields, fractional/non-finite TTLs, and valid explicit authorization.

Verification: `tests/test_sequence_mcp.py` — `30 passed`; full suite — `336 passed, 3 skipped`; compileall and `git diff --check` — exit 0.

## Semantic/executor authorization scalar validation remediation (2026-08-29)

The semantic authorization boundary and `SequenceExecutor.authorize()` now require exact boolean approval and finite, integral, positive TTL values no greater than the 30-second policy maximum. MCP sequence authorization (including inline plans) applies the same integral bounded TTL validation and forwards values without coercion. Malformed authorization inputs are rejected before executor authorization; valid integer-valued controls remain accepted. Added direct semantic, executor, and inline MCP regressions with valid controls.

Verification: focused semantic/executor/MCP tests — `74 passed in 0.32s`; full suite — `342 passed, 3 skipped in 23.19s`; compileall and `git diff --check` — exit 0.

## Rejected review remediation (2026-08-30)

Semantic sequence and workflow execution boundaries now validate exact boolean approval and finite, integral, positive bounded TTL values even when a caller supplies an approval token. Screen capture and OCR submission both re-check authorization expiry and use the minimum authorization/deadline remaining window. Short-lived sequence executions explicitly shut down their screen worker in final cleanup while preserving poisoned-worker behavior for direct bounded-call use. Added regressions for scalar bypass, expiry between capture and OCR, pre-expiry snapshot rejection, and worker cleanup.

Verification: focused semantic/screen tests — `22 passed in 0.11s`; full suite — `350 passed, 3 skipped in 24.01s`; compileall and `git diff --check` — exit 0.
