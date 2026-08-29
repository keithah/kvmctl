# Final-review remediation report (2026-08-28)

Implemented one fix wave for authorization, screen assertions, and journal evidence.

## Verification

- `.venv/bin/python -m pytest tests/test_final_review_remediation.py tests/test_screen_assertion.py -q` — `3 passed`
- `.venv/bin/python -m pytest tests/test_final_review_remediation.py tests/test_screen_assertion.py tests/test_sequence_semantics.py -q` — `12 passed`
- `.venv/bin/python -m compileall -q kvmctl tests` — exit 0
- `git diff --check` — exit 0

The pre-existing suite has legacy MCP calls that submit raw plans with `approved=true`; those are intentionally rejected by the new token-bound execution contract and require migration to authorize-then-token-execute.
