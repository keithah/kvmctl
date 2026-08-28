# Task 2 report — named host probes

## Files changed

- `kvmctl/host.py`: added argv-only `ArgvRunner` protocol, bounded/sanitized output handling, named probe registry, fail-closed `ProbeError`, and parsers for host identity, PCI graphics/driver plus DRM nodes, and render-service access.
- `tests/test_host.py`: added deterministic tests for valid probe evidence, unknown probes, malformed/sensitive/bounded output, and JSON-compatible result values.

## Tests run

- RED: `.venv/bin/python -m pytest tests/test_host.py -q` — after a minimal importable stub, 5 tests failed with the expected `NotImplementedError`/missing behavior.
- Focused GREEN: `.venv/bin/python -m pytest tests/test_host.py -q` — **6 passed**.
- Full suite: `.venv/bin/python -m pytest tests -q` — **113 passed, 3 skipped**.
- `git diff --check` — passed.

## Concerns

- The render-access profile intentionally uses the fixed service `kvm-render` and DRM node `/dev/dri/renderD128`; future host adapters may need capability-specific profiles, but arbitrary command input is deliberately not exposed.
- No CLI/MCP behavior was changed in this slice.
