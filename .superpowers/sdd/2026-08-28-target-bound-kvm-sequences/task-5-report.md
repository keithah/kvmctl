# Task 5 report: MCP dispatcher and server tools

## Outcome

Implemented the Task 5 MCP dispatcher and FastMCP adapters for target-bound sequences and named workflows. Task 6 was not started.

## Changed files

- `kvmctl/mcp_surface.py`
  - Added dispatcher branches for `kvm_sequence_plan`, `kvm_sequence_authorize`, `kvm_sequence_execute`, `kvm_workflow_list`, `kvm_workflow_inspect`, and `kvm_workflow_execute`.
  - Added strict base64/JSON plan decoding and support for direct inline plan mappings.
  - Passed shared workflow repository, sequence executor, journal, and session context into `SemanticSurface`.
  - Preserved structured JSON operation envelopes for sequence/workflow errors.
  - Extended exported MCP `TOOL_SPEC` while retaining all existing tools.
- `kvmctl/mcp_server.py`
  - Registered all six FastMCP tools.
  - Added injectable workflow repository, sequence executor, journal, and persistent session wiring.
- `tests/test_sequence_mcp.py`
  - Added TDD coverage for stable envelopes, approval/write gating, invalid base64, redacted workflow inspection, and inline/named execution parity.
- `tests/test_mcp_server.py`
  - Updated registered-tool expectations.
- `tests/test_cli_mcp.py`
  - Updated MCP registry metadata/name expectations.

## TDD evidence

Initial focused RED run:

```text
.venv/bin/python -m pytest tests/test_sequence_mcp.py tests/test_mcp_server.py -q
```

Failed during collection because the environment lacked optional `mcp` (`ModuleNotFoundError: No module named 'mcp'`). After installing the declared optional dependency with `uv sync --extra dev --extra mcp` (and removing the generated `uv.lock`), the implementation tests were run.

Focused MCP tests:

```text
.venv/bin/python -m pytest tests/test_sequence_mcp.py tests/test_mcp_server.py -q
...............                                                          [100%]
15 passed in 1.19s
```

MCP parity tests:

```text
.venv/bin/python -m pytest tests/test_sequence_mcp.py tests/test_mcp_server.py tests/test_cli_mcp.py -q
..........................                                               [100%]
26 passed in 2.57s
```

Full suite:

```text
.venv/bin/python -m pytest -q
..........................................................s............. [ 31%]
.....................................s.................................. [ 62%]
............................................s........................... [ 93%]
...............                                                          [100%]
228 passed, 3 skipped in 23.15s
```

Additional verification: `git diff --check` passed.

## Commit

`514b993 feat: expose target-bound sequences through MCP`

## Concerns

- The repository's declared `mcp` dependency is optional; the pre-existing `.venv` did not contain it, so it was installed locally with `uv sync --extra dev --extra mcp`. No dependency or lockfile changes were committed.
- The dispatcher accepts both `{ "plan": {...} }` and a direct inline plan mapping, plus strict `plan_b64` JSON input. Named workflows remain repository-injected; the default repository is empty.

## Task 5 fix round 1 verification

Fixed all MCP review findings: explicit per-tool argument allowlists reject unknown fields; direct dispatch validates booleans and finite numeric TTLs without coercion; MCP no longer infers workflow inspection targets; and regression coverage now includes invalid action data, target/revision mismatch propagation, and server dispatch parity.

Exact commands and results:

```text
.venv/bin/python -m pytest tests/test_sequence_mcp.py -q
10 failed, 6 passed in 0.12s (expected RED for new regression tests)

.venv/bin/python -m pytest tests/test_sequence_mcp.py tests/test_mcp_server.py tests/test_cli_mcp.py -q
39 passed in 2.43s

.venv/bin/python -m pytest -q
241 passed, 3 skipped in 23.18s

.venv/bin/python -m compileall -q kvmctl tests && git diff --check
passed (no output)
```
