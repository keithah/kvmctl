# Task reboot report

## Outcome

Implemented the explicitly authorized host reboot lifecycle at the host adapter and semantic boundary without exposing it through `exec_command` or changing the legacy CLI/MCP `TOOL_SPEC`.

## Files changed

- `kvmctl/host.py`
  - Added normalized target/operation confirmation hashing.
  - Added argv-only `HostAdapter` identity and reboot lifecycle.
  - Performs preflight identity validation, invokes only `systemctl reboot`, requires observed disappearance, polls for readiness, and verifies post-return identity.
  - Returns stable `host_reboot_timeout`, `host_identity_mismatch`, and `host_reboot_failed` error codes without raw command output.
- `kvmctl/policy.py`
  - Added `host.reboot` to write-gated operations.
- `kvmctl/semantics.py`
  - Added optional `host_runner` injection and `host_reboot(...)` semantic operation.
- `tests/test_reboot.py`
  - Added strict authorization, lifecycle, timeout, mismatch, and no-exec-command tests.
- `tests/test_semantic_reboot.py`
  - Added semantic-boundary and write-gate coverage.
- `.superpowers/sdd/remote-operations-substrate/task-reboot-report.md`
  - This report.

## TDD and verification

- RED: focused reboot test collection failed because `HostAdapter` was not yet implemented.
- GREEN: focused host and semantic reboot tests passed: `5 passed`.
- Full suite: `123 passed, 3 skipped`.
- `git diff --check`: clean.

## Compatibility

Adding `host.reboot` to the existing `TOOL_SPEC` caused the legacy exact-name metadata test to fail. That catalog change was removed; the old CLI/MCP behavior remains unchanged for this task. Host reboot remains available only through the new host/semantic boundary pending the later integration task.

## Concerns

- Reboot readiness currently uses repeated host identity probing through the injected argv runner; production wiring must provide a bounded runner that reports transient disappearance as an exception.
- The lifecycle treats a failed identity probe as disappearance. A future adapter may distinguish transport-unavailable from malformed probe data if that distinction is needed operationally.
- No journal integration was added because checkpoint journaling is the next plan task.
