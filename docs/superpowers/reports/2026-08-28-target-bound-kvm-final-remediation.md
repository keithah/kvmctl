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
