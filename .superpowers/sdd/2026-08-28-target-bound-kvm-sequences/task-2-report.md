# Task 2 Report: Immutable Named KVM Workflows

## Status

Implemented Task 2 with strict RED-GREEN TDD and committed the implementation.

## Changed files

- `kvmctl/workflows.py`
  - Added frozen `WorkflowDefinition` and `WorkflowError`.
  - Added deterministic SHA-256 workflow revisions derived from name, target scope, and canonical `SequencePlan` content.
  - Added eager-validating, name-sorted immutable `WorkflowRepository`.
  - Added strict target/revision resolution, explicit target-independent opt-in, defensive canonical inspection, and module-level repository wrappers.
- `tests/test_workflows.py`
  - Added public-interface tests for canonical compilation/parity, deterministic and scope-bound revisions, validation/revision spoofing, ordering, target resolution, inspection redaction/defensive copies, immutability, and wrappers.

No semantic, MCP, CLI, transport, journal, or executor modules were modified.

## Commit

- Commit: `17054fd0fc660290a5121b5143b499800b7ffb66`
- Message: `feat: add immutable named KVM workflows`

## TDD evidence and exact commands/output

### RED

Command:

```bash
.venv/bin/python -m pytest tests/test_workflows.py -q
```

Output:

```text
ModuleNotFoundError: No module named 'kvmctl.workflows'
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.07s
```

### GREEN and focused regression

Command:

```bash
.venv/bin/python -m pytest tests/test_workflows.py tests/test_sequences.py -q
```

Output after implementation and again after commit:

```text
...............................................                          [100%]
47 passed in 0.02s
```

### Compilation

Command:

```bash
.venv/bin/python -m compileall -q kvmctl/workflows.py tests/test_workflows.py
```

Output:

```text
(no output; exit code 0)
```

## Concerns

- Target-independent workflows use an internal sentinel target in the stored canonical `SequencePlan`; inspection redacts it to `target: null`. Resolution now requires an invocation target and returns an immutable target-bound `SequencePlan` copy while preserving the stored definition and revision.
- Secret-like text action values are deterministically emitted as `[REDACTED]` during inspection.

## Task 2 review-fix addendum

### Changed files

- `kvmctl/workflows.py`
  - Removed caller control of the public `revision` constructor field; revisions are derived in `__post_init__`.
  - Added typed-definition validation/revision-integrity checks to direct repository construction.
  - Made target-independent resolution require a non-empty invocation target and return an immutable target-bound plan copy without changing stored identity.
  - Added conservative deterministic redaction for token/password/authorization/cookie/secret-like text actions.
  - Normalized malformed unhashable lookup names to bounded `WorkflowError` messages.
- `tests/test_workflows.py`
  - Added regression coverage for direct construction/repository validation, target-bound and missing-target resolution, secret-like text redaction, and malformed lookup names.

No semantic, MCP, CLI, or executor modules were modified.

### TDD evidence and exact commands/output

RED command:

```bash
python3 -m pytest -q tests/test_workflows.py
```

RED output:

```text
9 failed, 16 passed in 0.09s
```

Focused GREEN command:

```bash
python3 -m pytest -q tests/test_workflows.py
```

GREEN output:

```text
25 passed in 0.02s
```

Full-suite verification command:

```bash
python3.11 -m pytest -q
```

Full-suite output:

```text
201 passed, 3 skipped in 24.86s
```

Fix commit:

- Commit: `703738afa8bd4b1b63c9f028a077f0c0b306edb5`
- Message: `fix: harden named KVM workflow boundaries`
