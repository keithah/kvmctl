# Typed operation result helpers — task report

## Status

Implemented the typed, JSON-compatible operation result builder and legacy evidence adapter.

## Files changed

- `kvmctl/results.py`
  - Adds `OperationError` and `OperationResult` TypedDicts.
  - Adds `operation_result(...)` with stable fields: `operation`, `target`, `transport`, `read_only`, `ok`, `changed`, `state`, `evidence`, `warnings`, `error`, and `next_actions`.
  - Normalizes string/mapping errors with stable `code`, `retryable`, and `requires_human` fields.
  - Adds `operation_result.from_legacy(...)` for existing `semantics._evidence`-style dictionaries.
  - Exposes `build_result` as an explicit alias.
- `tests/test_results.py`
  - Covers success shape, structured failure shape, JSON serialization, and legacy evidence compatibility.

Existing semantic operations were not changed, preserving their current public result shape.

## Verification

Strict TDD cycle completed:

1. Added tests before implementation.
2. Focused RED run: collection failed as expected with `ModuleNotFoundError: No module named 'kvmctl.results'`.
3. Added minimal implementation.
4. Focused GREEN run: `3 passed`.
5. Full suite: `116 passed, 3 skipped`.
6. `git diff --check`: clean.

## Concerns

- The compatibility adapter is intentionally opt-in; existing `_evidence` callers continue returning the legacy shape until a future migration updates those call sites.
- `evidence` values are accepted as JSON-compatible mappings but are not recursively validated; this keeps the helper small and preserves arbitrary structured evidence.
- Existing untracked orchestration files were left untouched. Host probe commit `252e15a` was not modified or reverted.
