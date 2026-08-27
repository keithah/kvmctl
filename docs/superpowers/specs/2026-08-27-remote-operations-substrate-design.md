# Remote Operations Substrate Design

**Status:** Proposed
**Scope:** kvmctl only; no Hermes orchestration and no BIOS automation.

## Goal

Make kvmctl a safe, evidence-backed remote-operations tool that can observe and perform narrowly defined operations across configured adapters, using the current KVM implementation as the first adapter.

## Non-goals

- BIOS or firmware navigation
- Arbitrary shell execution
- Arbitrary keyboard/mouse macros
- Hermes-specific prompts or orchestration
- Automatic rollback through unverified device paths
- Generic support claims for unknown hardware

## Architecture

The existing `KvmClient` remains the transport client. The semantic surface remains the shared facade for CLI and MCP, but new functionality is organized around four concepts:

1. **Adapters** provide typed transport capabilities such as KVM snapshots or SSH probes.
2. **Profiles** describe known machines and their approved probes/actions.
3. **Operations** expose named, typed intent-level actions with read/write metadata.
4. **Evidence** records observations, transport status, state transitions, and postcondition results.

The first increment does not require a full workflow database. It adds stable result/evidence conventions, named read-only host probes, and an explicitly authorized reboot lifecycle. Checkpoint records use a small append-only JSONL journal so interrupted operations can be inspected without replaying actions.

## Public operations

Initial named operations:

- `capabilities`
- `snapshot`
- `ocr`
- `verify`
- `select`
- `hid_reset`
- `rearm_otg`
- `host.identity.inspect`
- `host.graphics.inspect`
- `service.render_access.inspect`
- `host.reboot`

`exec_command` remains available only as a compatibility surface during migration. New host operations select commands internally from a profile-owned registry and do not accept arbitrary commands from MCP callers.

## Result contract

Every operation returns JSON-compatible data containing:

- `operation`
- `target` when applicable
- `transport`
- `read_only`
- `ok`
- `changed`
- `state`
- `evidence`
- `warnings`
- `error` with a stable `code`, `retryable`, and `requires_human` where applicable
- `next_actions`

Transport success, action acceptance, observation, and postcondition verification remain distinguishable inside `evidence`.

## Authorization

Read-only operations are allowed by default. Mutating operations require the existing explicit write gate and, for reboot, an operation-specific confirmation. A reboot must not be reachable through the generic command string path.

Authorization binds to target, operation, and a normalized plan hash. The tool must reject missing target identity, disabled write policy, and mismatched confirmation rather than guessing.

## Host probes

Host probes use an injected argv-based SSH runner with `shell=False` or equivalent. Each probe has:

- stable probe name
- approved argv template
- parser
- redaction rules
- output schema
- timeout

The first probes are identity, graphics inventory, DRM inventory, and render-access verification. Raw secret material and unbounded command output are not returned by default.

## Reboot lifecycle

`host.reboot` performs:

1. target identity preflight
2. checkpoint before mutation
3. explicit write/confirmation checks
4. named reboot action
5. bounded disappearance/readiness polling
6. post-return identity verification
7. checkpointed result

Timeouts produce `HOST_NOT_RETURNED` with `requires_human=true` when the next action cannot be safely inferred. The operation never blindly retries or continues through KVM input after an uncertain reboot.

## Checkpoints

A JSONL journal records operation id, target, profile version, state transition, plan hash, timestamp, adapter, action summary, and evidence references. Secrets, raw passwords, and unrestricted command output are excluded. Resume, when later added, must always re-observe before any action and must never replay an action solely because it was present in the journal.

## Testing

Tests must cover:

- result contract for existing operations
- probe parsing and redaction
- argv-only SSH execution and absence of shell operators
- target identity mismatch
- reboot refusal without authorization
- reboot success with mocked disappearance/return
- reboot timeout and structured human-intervention state
- journal atomic append and secret exclusion
- MCP tool registration and structured results
- CLI help and explicit confirmation behavior

Live hardware remains opt-in and read-only until the mocked lifecycle is stable. No BIOS or reboot live test is enabled by default.

## Acceptance criteria

The implementation is acceptable when:

- existing CLI/MCP behavior remains compatible
- all new operations are named and typed
- no new arbitrary transport escape hatch is introduced
- reboot cannot run without explicit authorization
- a host returning after reboot is verified by identity, not merely TCP reachability
- interrupted operations produce inspectable evidence
- the full test suite and package build pass
- documentation states exactly which host probes and adapters are supported
