# MCP server

`kvmctl-mcp` is an MCP stdio server around the same `SemanticSurface` used by the CLI and internal JSON dispatcher. It exposes semantic operations only; it does not provide arbitrary KVMD API, keyboard, mouse, or shell passthrough.

The GLKVM can pass keyboard input through to the currently selected target, and the low-level Python `KvmClient` exposes `key_down()`, `key_up()`, `press_key()`, and `type_text()`. The MCP server now exposes guarded equivalents as `kvm_send_text`, `kvm_send_keys`, `kvm_hold_key`, and `kvm_release_all`, plus mouse and OCR-targeting tools listed below. Its `select` tool still sends only the configured switch-selection sequence.

## Install and configure

Install the optional MCP dependency:

```sh
.venv/bin/pip install -e '.[mcp]'
```

The server reads configuration from its process environment:

| Variable | Meaning |
|---|---|
| `KVMCTL_URL` | Required KVMD base URL |
| `KVMCTL_TOKEN` | Existing KVMD token, preferred for stdio integrations |
| `KVMCTL_USER` / `KVMCTL_PASSWORD` | Login credentials when no token is supplied |
| `KVMCTL_HOST` | Optional HTTP virtual-host header; also used for stream origin |
| `KVMCTL_CA_BUNDLE` | Optional CA bundle path |
| `KVMCTL_INSECURE` | Set `1` only for explicitly trusted self-signed devices |
| `KVMCTL_WRITE_ENABLED` | Set `1` to authorize write operations |
| `KVMCTL_SSH_ALLOWLIST` | Comma-separated SSH base commands for compatibility `exec_command` |

Do not put credentials in an MCP JSON configuration file committed to a repository. Use the client application's environment injection or a secret manager.

Example client entry (some clients expand `${KVMCTL_TOKEN}` themselves; others do not):

```json
{
  "mcpServers": {
    "kvmctl": {
      "command": "/absolute/path/to/kvmctl-mcp",
      "env": {
        "KVMCTL_URL": "https://glkvm.example",
        "KVMCTL_HOST": "glkvm.local",
        "KVMCTL_TOKEN": "${KVMCTL_TOKEN}"
      }
    }
  }
}
```

The placeholder is illustrative, not a guarantee of expansion. If the client does not expand variables, inject the actual value through its documented environment/secret-manager mechanism. The server must receive the real token, never the literal `${KVMCTL_TOKEN}` string.

The process performs no device request at startup beyond configuration/authentication setup; device calls occur when a tool is invoked. Diagnostics must not be sent to stdout because stdout is the MCP protocol stream.

## Tools and safety

The registered tools are:

- `capabilities` — read device capabilities.
- `snapshot` — capture the current screen; returns native MCP `ImageContent` with `image/jpeg` data.
- `ocr` — OCR a supplied image or a fresh snapshot.
- `verify` — verify a named machine using the configured verification policy.
- `select` — select and verify a named machine through the KVM switch.
- `kvm_send_text` — type mapped text through the selected target.
- `kvm_send_keys` — send a validated key chord.
- `kvm_hold_key` — hold one key for a bounded duration, then release it.
- `kvm_release_all` — release keys tracked as held.
- `kvm_mouse_move` / `kvm_mouse_move_pct` — move the target mouse.
- `kvm_mouse_click` — click a mouse button.
- `kvm_mouse_scroll` — scroll the mouse wheel.
- `kvm_status` — report authentication, stream, and held-key state.
- `kvm_screenshot_to_file` — save a current screenshot locally.
- `kvm_ocr_screenshot` — OCR the current screen and return text coordinates.
- `kvm_ocr_click` — find text with OCR and click the best match; write-gated.
- `hid_reset` — reset the device HID subsystem.
- `rearm_otg` — re-arm the USB/OTG gadget.
- `host.identity.inspect` — inspect named host identity through the configured argv-only runner.
- `host.graphics.inspect` — inspect named host graphics/DRM state.
- `service.render_access.inspect` — inspect service and render-node access.
- `host.reboot` — perform the authorized host reboot lifecycle with checkpoint verification.
- `exec_command` — compatibility surface for explicitly allowlisted SSH commands.

Safety rules:

- Read-only tools are available by default.
- `select`, `hid_reset`, and `rearm_otg` require `KVMCTL_WRITE_ENABLED=1`; `select` also requires `transport="kvm"`.
- `host.reboot` requires write authorization, a configured host runner, and a confirmation token bound to the requested target.
- `exec_command` requires write authorization, explicit `transport="ssh"`, and a command whose base executable is in `KVMCTL_SSH_ALLOWLIST`; shell operators and substitutions are rejected.
- Named host operations use internally generated argv only; MCP callers cannot submit arbitrary host commands through those operations.
- Live hardware tests remain opt-in and are separate from the normal test suite.

The write gate is intentionally process-wide for a stdio server. Run a separate read-only process when integrating with an untrusted or shared client. For a complete operational selection example, see [`OPERATOR_RUNBOOK.md`](OPERATOR_RUNBOOK.md).
