# Final-review remediation report (2026-08-28)

Implemented one fix wave for authorization, screen assertions, and journal evidence.

## Capability validation-before-consumption remediation

`FileAuthorizationStore.take()` now validates the decoded plan, target binding, canonical plan hash, and finite expiry before marking a capability used or rewriting the store. MAC-valid malformed capabilities remain unchanged and valid one-time capabilities retain their existing consume-once behavior.

## Verification

- `.venv/bin/python -m pytest tests/test_persistence_hardening.py -q` — `9 passed`
- `.venv/bin/python -m pytest tests -q` — `308 passed, 3 skipped`
- `.venv/bin/python -m compileall -q kvmctl tests` — exit 0
- `git diff --check` — exit 0

## Persisted capability schema and TTL hardening (2026-08-29)

`FileAuthorizationStore` now validates the exact persisted capability schema, including boolean use state, token/binding/session/workflow field types, canonical plan and hash, expiry finiteness, expiration, and the configured maximum TTL before any capability can be consumed or rewritten. Every persisted record is validated first, and MAC-valid malformed records remain byte-for-byte unchanged. Valid capabilities retain single-use consumption.

Regression coverage includes falsey non-boolean use states, invalid workflow/binding/session types, arbitrary finite distant expiry, and preservation checks.

Verification:

- `.venv/bin/python -m pytest tests/test_persistence_hardening.py tests/test_final_safety_gaps.py -q` — `42 passed`
- `.venv/bin/python -m pytest tests -q` — `315 passed, 3 skipped`

The pre-existing suite has legacy MCP calls that submit raw plans with `approved=true`; those are intentionally rejected by the new token-bound execution contract and require migration to authorize-then-token-execute.
