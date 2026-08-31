# Target-Bound KVM Sequences Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe inline and named target-bound KVM sequences that share one canonical plan, authorization model, executor, journal format, and abort-on-unexpected-state policy across Python, MCP, and CLI surfaces.

**Architecture:** Keep `KvmClient` limited to validated transport primitives. Add a focused sequence domain module for typed actions, canonicalization, validation, plan hashing, and execution; add a workflow repository module for immutable named definitions; and extend `SemanticSurface` as the only composition boundary used by both MCP and CLI. Sequence execution owns the per-device lock, target verification, deadlines, cleanup, and journal transitions.

**Tech Stack:** Python 3, dataclasses/enums, existing `KvmClient`, `SemanticSurface`, `SessionState`, `TransportPolicy`, `Journal`, pytest, stdio MCP adapter, argparse CLI.

**Spec:** `docs/superpowers/specs/2026-08-28-target-bound-kvm-sequences-design.md`

## Global Constraints

- At most 10 actions per sequence.
- At most 30 seconds total execution duration.
- One target per sequence.
- Abort on any unexpected screen state.
- No loops, branches, nested workflows, arbitrary code, or arbitrary shell commands.
- Automatic key release and stream cleanup on success, failure, or cancellation.
- One mutating sequence per KVM device at a time.
- Approval binds to target identity, exact canonical plan, workflow revision when applicable, and expiry.
- MCP and CLI remain thin shims over shared semantic logic.
- Journal and evidence output must be deterministic and credential-redacted.
- Automated tests use mocked transport and never send destructive target commands.

---

### Task 1: Define the canonical sequence domain model

**Files:**
- Create: `kvmctl/sequences.py`
- Test: `tests/test_sequences.py`

**Interfaces:**
- Consumes: validated primitives already exposed by `SemanticSurface` and `KvmClient` (`kvm_send_text`, `kvm_send_keys`, `kvm_hold_key`, `kvm_release_all`, `kvm_mouse_move`, `kvm_mouse_move_pct`, `kvm_mouse_click`, `kvm_mouse_scroll`).
- Produces: `Action`, `SequencePlan`, `SequenceLimits`, `UnexpectedScreenPolicy`, `canonicalize_plan()`, `plan_hash()`, `validate_plan()`.

- [ ] **Step 1: Write failing public-interface tests**

Add tests that construct a plan from JSON-like dictionaries and assert that:

```python
plan = SequencePlan.from_mapping({
    "target": "pve2",
    "actions": [
        {"type": "text", "value": "hostname"},
        {"type": "key", "value": "Enter"},
    ],
    "max_duration_ms": 5000,
    "unexpected_screen_policy": "abort",
})
assert plan.target == "pve2"
assert plan.actions[0].kind == "text"
assert plan.unexpected_screen_policy is UnexpectedScreenPolicy.ABORT
assert plan_hash(plan) == plan_hash(plan.to_mapping())
```

Cover stable key ordering, normalized aliases, rejection of unknown action types, empty plans, more than 10 actions, durations outside 1–30,000 ms, more than one target, invalid mouse ranges, invalid hold durations, and policies other than `abort`.

- [ ] **Step 2: Run the focused tests and verify the expected RED state**

Run:

```bash
.venv/bin/python -m pytest tests/test_sequences.py -q
```

Expected: collection or assertion failures because `kvmctl.sequences` and its public types do not yet exist.

- [ ] **Step 3: Implement the minimal canonical model**

Use frozen dataclasses and explicit action kinds. `SequencePlan.from_mapping()` must:

1. Require a non-empty string `target`.
2. Normalize action dictionaries into typed immutable actions.
3. Resolve keyboard names through `kvmctl.input.resolve_key` / `parse_combo`.
4. Normalize numeric values to integers or finite floats.
5. Default `max_duration_ms` to 30,000 and policy to `abort`.
6. Reject every unsupported field instead of ignoring it.

`to_mapping()` must emit deterministic JSON-compatible data with sorted object keys. `plan_hash()` must hash canonical JSON using UTF-8 SHA-256 and return the lowercase `sha256:<hex>` form.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_sequences.py -q
```

Expected: all sequence-model tests pass.

- [ ] **Step 5: Commit the domain model**

```bash
git add kvmctl/sequences.py tests/test_sequences.py
git commit -m "feat: add canonical KVM sequence plans"
```

---

### Task 2: Add immutable named workflow definitions

**Files:**
- Create: `kvmctl/workflows.py`
- Create: `tests/test_workflows.py`
- Modify: `kvmctl/sequences.py`

**Interfaces:**
- Consumes: `SequencePlan`, `canonicalize_plan()`, and `plan_hash()` from Task 1.
- Produces: `WorkflowDefinition`, `WorkflowRepository`, `WorkflowError`, `list_workflows()`, `inspect_workflow()`, `resolve_workflow()`.

- [ ] **Step 1: Write failing repository tests**

Test an in-memory repository with a workflow mapping:

```python
repo = WorkflowRepository.from_mappings([{
    "name": "open-terminal-and-identify",
    "target": "pve2",
    "max_duration_ms": 5000,
    "unexpected_screen_policy": "abort",
    "steps": [
        {"type": "key", "value": "ControlAltT"},
        {"type": "text", "value": "hostname"},
        {"type": "key", "value": "Enter"},
    ],
}])
workflow = repo.resolve("open-terminal-and-identify", repo.list()[0].revision)
assert workflow.plan.target == "pve2"
assert workflow.revision.startswith("sha256:")
```

Cover duplicate names, invalid names, missing steps, revision mismatch, target mismatch, deterministic listing order, target-independent declaration, and redacted inspection output.

- [ ] **Step 2: Run the focused tests and verify RED**

```bash
.venv/bin/python -m pytest tests/test_workflows.py -q
```

Expected: failures because the workflow repository is not implemented.

- [ ] **Step 3: Implement immutable workflow loading and resolution**

`WorkflowDefinition` must store the canonical plan and derive its revision from the canonical workflow mapping, including the workflow name and target scope. The repository must reject duplicate names and malformed definitions at construction time. `resolve_workflow(name, revision, target)` must reject revision or target mismatches before execution.

Use a repository object injected into `SemanticSurface`; do not add filesystem loading or runtime discovery in this task. Named definitions must be declarative and must not contain shell commands, raw HID payloads, loops, branches, or nested workflow references.

- [ ] **Step 4: Run the focused tests and verify GREEN**

```bash
.venv/bin/python -m pytest tests/test_workflows.py tests/test_sequences.py -q
```

Expected: all model and workflow tests pass.

- [ ] **Step 5: Commit the workflow repository**

```bash
git add kvmctl/sequences.py kvmctl/workflows.py tests/test_sequences.py tests/test_workflows.py
git commit -m "feat: add immutable named KVM workflows"
```

---

### Task 3: Implement target-bound authorization and the sequence executor

**Files:**
- Create: `kvmctl/sequence_executor.py`
- Create: `tests/test_sequence_executor.py`
- Modify: `kvmctl/machines.py`
- Modify: `kvmctl/journal.py`

**Interfaces:**
- Consumes: `SequencePlan`, `WorkflowDefinition`, existing `SessionState`, `KvmClient`, `Journal`, and injected clock/sleep functions.
- Produces: `SequenceAuthorization`, `SequenceExecutionResult`, `SequenceExecutor.plan()`, `SequenceExecutor.execute()`, `SequenceExecutor.execute_workflow()`.

- [ ] **Step 1: Write failing executor tests**

Use a fake client recording calls and a fake clock. Assert that:

```python
planned = executor.plan(plan)
assert planned.target == "pve2"
assert planned.plan_hash.startswith("sha256:")
authorized = executor.authorize(planned, approved=True, ttl_s=30)
result = executor.execute(authorized)
assert result.ok is True
assert result.cleanup_ok is True
```

Cover target mismatch, expired authorization, changed plan hash, unverified session, device-lock conflict, action failure stopping later steps, deadline timeout, cancellation cleanup, workflow revision mismatch, and cleanup failure producing `ok == False`.

- [ ] **Step 2: Run the focused tests and verify RED**

```bash
.venv/bin/python -m pytest tests/test_sequence_executor.py -q
```

Expected: failures because the executor and authorization types do not yet exist.

- [ ] **Step 3: Implement plan, authorization, and execution**

`SequenceExecutor.plan()` validates and canonicalizes the plan, verifies target identity from `SessionState.current`, and returns a plan record containing target, plan hash, action count, maximum duration, and expiry metadata. `authorize()` must reject unapproved plans and create an authorization bound to the exact plan hash, target, workflow revision, and expiry.

`execute()` must acquire a per-device lock before any mutating operation, re-check authorization expiry and target identity, execute actions in order through existing semantic/client primitives, and stop immediately on the first error. Use a monotonic deadline for duration enforcement. The executor must never call arbitrary methods based on caller-supplied names; dispatch only over an explicit action-kind table.

Always run cleanup in a `finally` path: release tracked keys and close a stream owned by the sequence. Cleanup errors must be included in the structured result and must make the result unsuccessful. Journal `planned`, `authorized`, `started`, per-step transitions, `aborted`, `completed`, and `cleanup_failed` using `Journal.checkpoint()` with only redacted values.

- [ ] **Step 4: Run focused executor and existing state/journal tests**

```bash
.venv/bin/python -m pytest tests/test_sequence_executor.py tests/test_journal.py tests/test_machines.py -q
```

Expected: all tests pass, including cleanup and redaction assertions.

- [ ] **Step 5: Commit the executor**

```bash
git add kvmctl/sequence_executor.py kvmctl/machines.py kvmctl/journal.py tests/test_sequence_executor.py
git commit -m "feat: execute target-bound KVM sequences safely"
```

---

### Task 4: Integrate the shared executor into the semantic surface

**Files:**
- Modify: `kvmctl/semantics.py`
- Modify: `kvmctl/policy.py`
- Modify: `kvmctl/operations.py`
- Create: `tests/test_sequence_semantics.py`

**Interfaces:**
- Consumes: `SequenceExecutor`, `WorkflowRepository`, and existing `TransportPolicy`.
- Produces: semantic methods `kvm_sequence_plan()`, `kvm_sequence_authorize()`, `kvm_sequence_execute()`, `kvm_workflow_list()`, `kvm_workflow_inspect()`, and `kvm_workflow_execute()`.

- [ ] **Step 1: Write failing semantic tests**

Assert that read-only planning works with writes disabled, while authorization and execution require the existing write gate. Verify both inline and named workflow calls reach the same fake executor and return the same result envelope for equivalent plans.

- [ ] **Step 2: Run the focused tests and verify RED**

```bash
.venv/bin/python -m pytest tests/test_sequence_semantics.py -q
```

Expected: failures because the semantic methods and operation entries do not exist.

- [ ] **Step 3: Add the injected repository/executor and semantic methods**

Extend `SemanticSurface.__init__()` with optional `workflow_repository`, `sequence_executor`, and `journal` dependencies while preserving existing call compatibility. Instantiate safe defaults when omitted. Keep policy checks in the semantic layer, not in the MCP or CLI adapters.

Add operation catalog entries with explicit read/write metadata. Planning, listing, and inspection are read-only. Authorization and execution are write-gated. Return a consistent envelope containing operation, target, plan hash, action count, elapsed time when applicable, execution status, cleanup status, and redacted errors.

- [ ] **Step 4: Run semantic and full existing tests**

```bash
.venv/bin/python -m pytest tests/test_sequence_semantics.py tests/test_semantics.py tests/test_control_surface.py -q
```

Expected: all selected tests pass and existing single-action behavior remains unchanged.

- [ ] **Step 5: Commit semantic integration**

```bash
git add kvmctl/semantics.py kvmctl/policy.py kvmctl/operations.py tests/test_sequence_semantics.py
git commit -m "feat: expose KVM sequences through semantic operations"
```

---

### Task 5: Add MCP dispatcher and server tools

**Files:**
- Modify: `kvmctl/mcp_surface.py`
- Modify: `kvmctl/mcp_server.py`
- Create: `tests/test_sequence_mcp.py`
- Modify: `tests/test_mcp_server.py`
- Modify: `tests/test_cli_mcp.py`

**Interfaces:**
- Consumes: semantic methods from Task 4.
- Produces: JSON dispatcher support and MCP tools `kvm_sequence_plan`, `kvm_sequence_authorize`, `kvm_sequence_execute`, `kvm_workflow_list`, `kvm_workflow_inspect`, and `kvm_workflow_execute`.

- [ ] **Step 1: Write failing MCP tests**

Dispatch an inline plan through `dispatch_tool()` and assert the JSON result has the stable envelope fields. Test unknown fields, invalid base64 or action data, missing approval, write-disabled execution, target mismatch, workflow revision mismatch, and equivalent inline/named plans. Inspect registered FastMCP tool names without invoking live transport.

- [ ] **Step 2: Run the focused tests and verify RED**

```bash
.venv/bin/python -m pytest tests/test_sequence_mcp.py tests/test_mcp_server.py -q
```

Expected: failures because the new dispatcher branches and server decorators do not exist.

- [ ] **Step 3: Implement thin MCP adapters**

Add only argument decoding, type conversion, semantic method calls, and JSON serialization to the MCP modules. Preserve structured error responses rather than raising device/policy errors through the stdio protocol. Do not duplicate plan validation, authorization hashing, target checks, or cleanup logic in the adapters.

Pass a shared session, workflow repository, executor, and journal through the existing context construction path. Ensure workflow inspection never exposes credentials or secret-bearing action fields.

- [ ] **Step 4: Run MCP parity tests**

```bash
.venv/bin/python -m pytest tests/test_sequence_mcp.py tests/test_mcp_server.py tests/test_cli_mcp.py -q
```

Expected: all focused MCP tests pass and the existing tool registry expectations are updated without removing prior tools.

- [ ] **Step 5: Commit MCP integration**

```bash
git add kvmctl/mcp_surface.py kvmctl/mcp_server.py tests/test_sequence_mcp.py tests/test_mcp_server.py tests/test_cli_mcp.py
git commit -m "feat: expose target-bound sequences through MCP"
```

---

### Task 6: Add equivalent CLI commands

**Files:**
- Modify: `kvmctl/cli.py`
- Create: `tests/test_sequence_cli.py`

**Interfaces:**
- Consumes: semantic methods from Task 4.
- Produces: CLI commands `sequence-plan`, `sequence-authorize`, `sequence-execute`, `workflow-list`, `workflow-inspect`, and `workflow-execute`.

- [ ] **Step 1: Write failing CLI tests**

Invoke `main()` with an injected fake client and JSON plan file/string. Assert read-only planning works without `--yes`, execution refuses without `--yes`, invalid plans return a non-zero status, and equivalent inline/named operations produce equivalent JSON envelopes.

- [ ] **Step 2: Run the focused tests and verify RED**

```bash
.venv/bin/python -m pytest tests/test_sequence_cli.py -q
```

Expected: failures because the new parser commands and dispatch branches do not exist.

- [ ] **Step 3: Implement CLI parsing and delegation**

Add arguments for plan input, workflow name, revision/hash, target, approval token, and optional output path using argparse. Read plan definitions from a specified file or standard input without logging their contents. Require `--yes` for authorization and execution. Delegate all validation and execution to `SemanticSurface`; keep CLI output as one JSON document on stdout and errors on stderr.

- [ ] **Step 4: Run CLI and existing parser tests**

```bash
.venv/bin/python -m pytest tests/test_sequence_cli.py tests/test_cli_mcp.py -q
```

Expected: all CLI tests pass and all existing commands remain compatible.

- [ ] **Step 5: Commit CLI integration**

```bash
git add kvmctl/cli.py tests/test_sequence_cli.py
git commit -m "feat: add target-bound KVM sequence CLI"
```

---

### Task 7: Document, audit, and run the complete verification gate

**Files:**
- Modify: `README.md`
- Modify: `docs/MCP.md`
- Modify: `docs/OPERATOR_RUNBOOK.md`
- Modify: `docs/TH41-3.md`
- Create: `tests/test_sequence_redaction.py`

**Interfaces:**
- Consumes: completed semantic/MCP/CLI behavior from Tasks 1–6.
- Produces: user-facing capability documentation, redaction regression coverage, and a verified release candidate.

- [ ] **Step 1: Write documentation and redaction regression tests**

Add tests that submit plans containing fields named `token`, `password`, `secret`, `authorization`, and `cookie` through journal/result paths and assert those values never appear in serialized output. Add deterministic ordering assertions for workflow listing and journal records.

- [ ] **Step 2: Implement documentation updates**

Correct the stale MCP statement that currently says keyboard and mouse passthrough is not provided. Document inline planning/authorization/execution, named workflow revisions, target binding, action and duration limits, abort-on-unexpected-state behavior, cleanup guarantees, and the distinction between KVM keyboard input and allowlisted SSH `exec-command`.

- [ ] **Step 3: Run focused redaction and documentation checks**

```bash
.venv/bin/python -m pytest tests/test_sequence_redaction.py tests/test_sequences.py tests/test_workflows.py tests/test_sequence_executor.py tests/test_sequence_semantics.py tests/test_sequence_mcp.py tests/test_sequence_cli.py -q
```

Expected: all new tests pass.

- [ ] **Step 4: Run the full repository verification gate**

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/python -m compileall -q kvmctl tests
.venv/bin/python -m build --wheel --no-isolation

git diff --check
git status --short --branch
```

Expected: all tests pass, compilation succeeds, the wheel builds, diff checking succeeds, and only intentionally excluded local artifacts remain untracked.

- [ ] **Step 5: Review the complete diff and commit documentation**

```bash
git diff HEAD~6 -- README.md docs kvmctl tests

git add README.md docs/MCP.md docs/OPERATOR_RUNBOOK.md docs/TH41-3.md tests/test_sequence_redaction.py
git commit -m "docs: document target-bound KVM workflows"
```

- [ ] **Step 6: Verify the final commit and repository state**

```bash
git log -8 --oneline --decorate
git status --short --branch
```

Expected: the implementation commits are present, the branch is at the intended final commit, and no generated secrets or unintended files are staged.
