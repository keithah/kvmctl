# Task 3 report

## Changed files
- `kvmctl/sequence_executor.py`: added immutable plan/authorization records, target/session checks, target/hash/expiry-bound authorization, nonblocking per-device locking, explicit action dispatch, deadlines, cancellation/error handling, cleanup, workflow execution, and redacted journal checkpoints.
- `kvmctl/machines.py`: added process-wide per-device mutation lock registry (`device_lock`).
- `kvmctl/journal.py`: made checkpoint identity fields authoritative so detail keys cannot spoof operation, target, or transition.
- `tests/test_sequence_executor.py`: added focused executor tests for planning/authorization/execution, verification and expiry, failure short-circuiting, cleanup failure, and workflow execution.

## Verification
- RED command: `.venv/bin/python -m pytest tests/test_sequence_executor.py -q`
- RED output: collection failed as expected with `ModuleNotFoundError: No module named 'kvmctl.sequence_executor'`.
- Focused command: `.venv/bin/python -m pytest tests/test_sequence_executor.py tests/test_journal.py tests/test_machines.py -q`
- Focused output: `26 passed in 20.29s`.
- Full command: `.venv/bin/python -m pytest -q`
- Full output: collection blocked by missing optional `mcp` dependency in three existing test modules (`tests/test_control_surface.py`, `tests/test_mcp_server.py`, `tests/test_task7_frontends.py`).
- `git diff --check`: clean.

## Commit
- `HEAD` — `feat: execute target-bound KVM sequences safely`

## Concerns
- Full repository suite could not collect because the worktree environment lacks the `mcp` package; the Task 3-focused suite passes.
- Stream cleanup is controlled by the constructor's `stream_owned=True` flag; the executor does not open streams implicitly.

## Task 3 review fix round 1
- Enforced `authorization.plan.target == authorization.target` at authorization construction and again at the execution boundary; changed plan hashes are rejected at execution.
- Made cancellation and all `BaseException` cleanup paths safe: `release_all`, `close_stream`, and lock release are attempted independently, with cleanup failures forcing `ok == False`.
- Replaced exception text in execution results and journal reasons with generic redacted categories (`action failed`, `cancelled`, `deadline exceeded`, and cleanup labels).
- Added deadline checks before, after every action, and before final completion.
- Journal `aborted` for lock conflicts and all execution preflight failures; workflow revision mismatch is journaled before raising.
- Replaced conditional action-kind branching with an explicit dispatch table.
- Added acceptance tests for changed hash, authorization target mismatch, lock conflict, post-action deadline timeout, cancellation cleanup/redaction, and workflow revision mismatch/journaling.
- Improved default lock identity to derive from explicit client endpoint identity when available, otherwise client instance identity; explicit `device_id` remains supported.

### Fix-round verification (exact outputs)
- `.venv/bin/python -m pytest tests/test_sequence_executor.py tests/test_journal.py tests/test_machines.py -q && git diff --check` → `30 passed in 20.45s`; diff check clean; exit 0.
- `.venv/bin/python -m pytest -q` → collection blocked by `ModuleNotFoundError: No module named 'mcp'` in `tests/test_control_surface.py`, `tests/test_mcp_server.py`, and `tests/test_task7_frontends.py`; exit 2.
