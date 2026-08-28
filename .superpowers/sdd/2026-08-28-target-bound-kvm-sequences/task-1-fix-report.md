# Task 1 Review Fix Report

## Outcome

`validate_plan()` now structurally validates directly constructed `SequencePlan` instances before `canonicalize_plan()` and `plan_hash()` consume them. Typed actions are checked by their canonical mapping against the existing action schemas, which enforces allowed fields, action kinds, and all existing bounds.

## Files

- `kvmctl/sequences.py` — added typed-plan structural validation and wired it into `validate_plan()`.
- `tests/test_sequences.py` — added regression coverage for invalid typed plans through both canonicalization and hashing.

## Verification

- TDD red: `python3 -m pytest -q tests/test_sequences.py` — **8 failed, 15 passed**. The new typed-plan cases were accepted or raised an unhandled `AttributeError`, demonstrating the finding before the implementation change.
- Focused green: `python3 -m pytest -q tests/test_sequences.py` — **31 passed**.
- Existing non-MCP suite: `python3 -m pytest -q --ignore=tests/test_control_surface.py --ignore=tests/test_mcp_server.py --ignore=tests/test_task7_frontends.py` — **157 passed, 3 skipped**.
- Full suite attempted: `python3 -m pytest -q` — collection blocked by missing optional dependency `mcp` in three MCP-related test modules (`ModuleNotFoundError: No module named 'mcp'`).
- `git diff --check` — passed.

## Commit

`COMMIT_PENDING` (will be replaced with the final commit ID after amend).

## Concerns

The complete suite cannot collect three MCP test modules because the current Python environment lacks the optional `mcp` dependency. No MCP files were modified; all non-MCP tests pass.
