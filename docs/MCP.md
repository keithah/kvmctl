# MCP server

`kvmctl-mcp` is an official MCP stdio server around the same `SemanticSurface` used by the CLI and internal JSON dispatcher. It exposes semantic operations only; it does not provide arbitrary KVMD API, keyboard, mouse, or shell passthrough.

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
| `KVMCTL_USER` / `KVMCTL_PASSWORD` | Optional login credentials when no token is supplied |
| `KVMCTL_HOST` | Optional HTTP Host header for virtual-hosted devices |
| `KVMCTL_CA_BUNDLE` | Optional CA bundle path |
| `KVMCTL_INSECURE` | Set `1` only for explicitly trusted self-signed devices |
| `KVMCTL_WRITE_ENABLED` | Set `1` to authorize write operations |
| `KVMCTL_SSH_ALLOWLIST` | Comma-separated SSH base commands |

Do not put credentials in an MCP JSON configuration file committed to a repository. Use the client application's environment injection or a secret manager.

Example client entry (some clients expand `${KVMCTL_TOKEN}` themselves; others
do not):

```json
{
  "mcpServers": {
    "kvmctl": {
      "command": "/absolute/path/to/kvmctl-mcp",
      "env": {
        "KVMCTL_URL": "https://glkvm.example",
        "KVMCTL_TOKEN": "${KVMCTL_TOKEN}"
      }
    }
  }
}
```

The placeholder above is illustrative, not a guarantee of expansion. If your
MCP client does not expand variables in its JSON configuration, inject the
actual value through that client's documented process-environment/secret
manager mechanism (or launch wrapper). The server must receive the real token
in `KVMCTL_TOKEN`, never the literal `${KVMCTL_TOKEN}` string.

The process performs no device request at startup beyond configuration/authentication setup; device calls occur when a tool is invoked. Diagnostics must not be sent to stdout because stdout is the MCP protocol stream.

## Tools and safety

The tools are `capabilities`, `snapshot`, `ocr`, `verify`, `select`, `hid_reset`, `rearm_otg`, and `exec_command`.

- Read-only tools are available by default.
- `snapshot` returns native MCP `ImageContent` with `image/jpeg` data.
- `select` requires `KVMCTL_WRITE_ENABLED=1` and explicit `transport="kvm"`.
- `hid_reset` and `rearm_otg` require write authorization.
- `exec_command` requires write authorization, explicit `transport="ssh"`, and a command whose base executable is in `KVMCTL_SSH_ALLOWLIST`; shell operators and substitutions are rejected.
- Live hardware tests remain opt-in and are separate from the normal test suite.

The write gate is intentionally process-wide for a stdio server. Run a separate read-only process when integrating with an untrusted or shared client.
