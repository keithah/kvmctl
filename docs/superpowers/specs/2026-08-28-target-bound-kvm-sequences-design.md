# Target-Bound KVM Sequences Design

**Status:** Approved conversational design; written-spec review required before implementation planning.

## Goal

Extend kvmctl so interactive MCP and CLI users can authorize and execute short, target-bound sequences through the GLKVM HID path, while repeated automation can invoke named, pre-reviewed workflow definitions. Both entry points must use one shared executor and one safety model.

This feature controls the currently selected target's keyboard and mouse. It does not provide arbitrary shell execution, unrestricted KVMD passthrough, or general-purpose scripting. `exec-command` remains a separate allowlisted SSH capability.

## Modes

### Inline one-shot plans

An inline plan contains one target and an ordered list of bounded actions. The caller submits the plan for normalization and approval, then executes only the approved canonical plan.

Example:

```json
{
  "target": "pve2",
  "actions": [
    {"type": "key", "value": "ControlAltT"},
    {"type": "text", "value": "hostname"},
    {"type": "key", "value": "Enter"}
  ],
  "unexpected_screen_policy": "abort"
}
```

Inline plans are appropriate for interactive troubleshooting and actions that are not worth persisting as a named workflow.

### Named workflows

A named workflow is a declarative, reviewable definition containing a target, bounded steps, execution policy, and immutable revision identity. Invocation must include the workflow name, revision or plan hash, and target.

```yaml
name: open-terminal-and-identify
target: pve2
max_duration_ms: 5000
unexpected_screen_policy: abort
steps:
  - type: key
    value: ControlAltT
  - type: text
    value: hostname
  - type: key
    value: Enter
```

Named workflows are reusable only within their declared target scope unless explicitly declared target-independent. A workflow containing machine-specific assumptions must not be invoked against another target.

## Canonical plan and authorization

Both modes compile to the same internal canonical plan:

- One target identity
- Ordered typed actions
- Normalized key names and arguments
- Maximum duration
- Unexpected-screen policy
- Workflow revision, when applicable
- Expiration deadline
- Plan hash

Authorization binds to the target identity, exact canonical action list, normalized arguments, maximum duration, workflow revision when applicable, and expiration. Approval for one target cannot be reused after the target changes. A named workflow's name alone is never sufficient authorization.

The consent surface should show the target and normalized action summary before execution. The system should return the plan hash and authorization expiry so callers can correlate approval and execution in the journal.

## Execution limits and safety

Initial limits are intentionally conservative:

- At most 10 actions per sequence
- At most 30 seconds total execution duration
- One target per sequence
- No loops, branches, nested workflows, or arbitrary code
- No arbitrary shell commands
- One mutating sequence per KVM device at a time
- Abort on any unexpected screen state
- Automatic key release and stream cleanup on success, failure, or cancellation

`abort` is the only initial unexpected-screen policy. It applies when an expected screen condition is missing, OCR is ambiguous, the screenshot is stale or unavailable, target identity cannot be verified, the stream fails, or a device operation returns an unexpected result. Automatic cleanup is permitted; automatic continuation or fallback to another target is not.

Future retry or recovery policies must be explicitly allowlisted and selected by the caller. They must not be implicit behavior of this executor.

## Action vocabulary

The first implementation supports the existing validated primitives:

- `key` / key chord
- `text`
- `hold_key` with bounded duration
- `release_all`
- Mouse movement, click, and scroll
- Bounded wait
- Read-only screenshot/OCR assertions where needed by a workflow

A sequence must not expose raw HID packets. Key-down and key-up cleanup is owned by the executor. Held-key state is tracked per device and released in a `finally`-equivalent cleanup path.

## Target verification and locking

Before the first mutating action, the executor verifies that the selected KVM target matches the plan target. Selection and verification are part of the target/session state, not inferred from the caller's label. A target change invalidates outstanding approvals for the previous target.

A per-device mutation lock serializes selection, sequence execution, and recovery. Concurrent callers receive a structured conflict result rather than interleaving HID events. Cancellation must release held keys, close any sequence-owned stream, and leave the device in a known unlockable state.

## MCP and CLI surfaces

MCP exposes thin shims over the shared semantic surface with these operations:

- `kvm_sequence_plan` — normalize and validate an inline sequence without executing it.
- `kvm_sequence_execute` — execute an approved inline plan by target and plan hash.
- `kvm_workflow_list` — list available named workflow names and revisions.
- `kvm_workflow_inspect` — return a redacted canonical workflow plan.
- `kvm_workflow_execute` — execute an approved named workflow revision by target and revision/hash.

The exact JSON schema may evolve during implementation, but every planning and execution result must contain target, plan hash, action count, elapsed time when applicable, execution status, cleanup status, and redacted error information.

The CLI provides equivalent trusted-operator commands for validating and executing inline plans and named workflow revisions. CLI authorization remains explicit and must not weaken target verification or execution limits.

## Evidence and journaling

Every plan and execution records an append-only, credential-redacted journal entry containing:

- Target and target-verification result
- Canonical plan hash and workflow revision
- Requested and normalized action counts
- Approval and expiry metadata
- Start/end timestamps and duration
- Per-step status
- Cleanup status
- Final result or bounded failure reason

Screenshots and OCR used for assertions may be attached as evidence, but credentials, tokens, authenticated URLs, and arbitrary screen secrets must not be written to logs or generated artifacts.

## Error handling

Failures are terminal for the sequence. The result must distinguish at least:

- Invalid plan
- Authorization missing, expired, or mismatched
- Target mismatch
- Device busy
- Screen assertion failure
- HID/device failure
- Timeout
- Cancellation
- Cleanup failure

Cleanup failure must be visible and must prevent a success result. The executor should attempt `release_all` even when an earlier step fails; it must not silently continue with later actions.

## Testing strategy

Tests should exercise the public semantic, MCP, and CLI interfaces rather than private implementation details. Coverage should include:

- Canonicalization and stable plan hashes
- Inline and named workflow parity
- Target-bound approval acceptance and mismatch rejection
- Workflow revision mismatch rejection
- Action-count and duration limits
- Invalid key, mouse, wait, and assertion arguments
- Abort behavior for unexpected screen states
- Per-device lock conflicts
- Cancellation and held-key cleanup
- Stream cleanup and cleanup-failure reporting
- Structured journal entries with credential redaction
- MCP/CLI schema and behavior parity
- Existing single-action keyboard/mouse behavior remaining compatible

A hardware smoke test may validate a read-only status/snapshot path and a manually supervised HID action, but unattended tests must use mocked transport and must never send destructive target commands.

## Non-goals

This design does not add:

- Arbitrary shell commands through the attached keyboard
- General-purpose macros with loops or conditionals
- Automatic login or credential entry
- Automatic recovery from unexpected screens
- Cross-target workflow reuse without explicit target-independent declaration
- Power control or device administration beyond existing operations

## Acceptance criteria

The implementation is ready when:

1. Inline and named workflows compile to the same canonical plan type.
2. Approval is target- and plan-hash-bound and expires.
3. The executor enforces the action and duration limits.
4. Unexpected state always aborts by default.
5. Held keys and streams are cleaned up on every exit path.
6. Concurrent mutation is serialized or rejected without HID interleaving.
7. MCP and CLI remain thin, behaviorally equivalent shims.
8. Journal and evidence output is deterministic and credential-redacted.
9. Focused tests and the full repository test/build gates pass.
