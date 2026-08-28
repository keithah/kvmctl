# Task 6 journal implementation report

## Outcome

Implemented an optional append-only checkpoint journal and integrated it with the authorized `HostAdapter.reboot` lifecycle.

## Changes

- Added `kvmctl/journal.py`:
  - JSONL records written with `O_APPEND` and one bounded `os.write` per line.
  - Per-path locking for concurrent in-process writers and `fsync` after each append.
  - Parent directory creation and restrictive `0600` file creation mode.
  - Recursive secret-key exclusion (`token`, passwords, secrets, credentials, API keys, authorization, cookies, private keys).
  - Safe handling of dataclasses, dates, bytes, sets, non-finite floats, unknown objects, and nesting/item bounds.
  - Explicit maximum record size; oversized records are rejected before opening/writing.
- Extended `HostAdapter` with optional `journal=` dependency injection, preserving existing callers.
- Added reboot checkpoints for `preflight`, `reboot_requested`, `disappeared`, `ready`, `mismatch`, `reboot_failed`, and `timeout` transitions.
- Journal failures are best-effort and cannot change reboot result behavior.
- Added focused journal and reboot integration tests.

## Verification

- TDD RED: `pytest tests/test_journal.py` initially failed during collection with `ModuleNotFoundError: kvmctl.journal`.
- Focused GREEN: `python3 -m pytest -q tests/test_journal.py tests/test_reboot.py` — **9 passed**.
- Compile check: `python3 -m compileall -q kvmctl` — passed.
- `git diff --check` — passed.
- Full suite via `uv run --with pytest --with 'mcp>=1.0,<2' pytest -q` — **133 passed, 3 skipped, 1 failed**.

The single full-suite failure is pre-existing/unrelated: `tests/test_cli_mcp.py::test_mcp_spec_declares_readonly_and_gates` calls `tool.get("write_gate")` while asserting `read_only` for read-only tools, producing an assertion failure.

## Concerns

- Atomicity is guaranteed at the single append syscall and serialized among threads in this process; independent processes rely on regular-file `O_APPEND` semantics.
- Journal writes intentionally use best-effort semantics so observability cannot turn a successful or stable reboot result into an exception.
