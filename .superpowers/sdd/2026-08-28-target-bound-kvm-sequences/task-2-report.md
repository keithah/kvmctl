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

- Commit: `047dc9fb7516a96589920a16a69935129ee37284`
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

- Target-independent workflows use an internal sentinel target in the stored canonical `SequencePlan`; inspection redacts it to `target: null`. Resolution returns an immutable copy carrying `resolved_target` without mutating the stored definition or its revision.
- Full repository tests were not run because Task 2 acceptance specifies the focused workflow and sequence suites; Task 1 previously documented unrelated missing optional `mcp` collection dependencies.
