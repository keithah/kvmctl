# Named host probe hardening — review disposition

## Findings addressed

- **Typed runner/result and status codes:** Added `RunnerResult(return_code, stdout)` and normalization for typed results, legacy `(return_code, stdout)` tuples, and legacy text runners. Named probe calls enforce the profile timeout, pass `timeout=` to runners that declare it, and fail closed on non-zero output commands or unsupported status codes.
- **Graphics parser fail-closed behavior:** Valid `lspci -nnk` PCI records and recognized indented detail records remain accepted; malformed PCI-looking lines and unknown non-empty records now raise `ProbeError`.
- **Profile configuration:** Added immutable `HostProbeProfile` (`HostProfile` compatibility alias) for service name, DRM node, and timeout. `HostAdapter`, `run_probe`, `SemanticSurface`, and MCP context propagate the profile. Callers still cannot supply arbitrary commands.

## Compatibility

Existing argv-only text runners and `(return_code, stdout)` tuple runners remain supported. Default service/node/timeout preserve prior behavior. Reboot lifecycle and journal behavior were not changed.

## Verification

- Focused probe tests: `12 passed`
- Full suite with declared dev + MCP extras: `138 passed, 3 skipped`
