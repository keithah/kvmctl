# kvmctl

A small, safety-first command-line tool for controlling KVMD-compatible KVM devices such as PiKVM and GLKVM.

Use `kvmctl` to inspect a device, capture its screen, verify a machine, and—when you explicitly authorize it—select a connected machine or run a limited command.

> **Status:** Experimental and hardware-tested. The client targets KVMD-compatible devices; switch profiles may need adjustment for your hardware.

## What you can do

- Discover device capabilities
- Capture a JPEG snapshot of the current screen
- Verify a named machine from the captured screen
- Select a named machine through a configured KVM switch
- Reset HID or re-arm the USB/OTG connection when needed
- Run explicitly allowlisted SSH commands through a configured integration
- Consume the same semantic operations from the Python API or MCP surface

## Install

```sh
python -m venv .venv
.venv/bin/pip install .
```

For development:

```sh
.venv/bin/pip install -e '.[dev]'
```

The package includes the WebSocket client used to keep the stream alive and the Python OCR adapter used when device OCR is unavailable. Local OCR also requires the `tesseract` executable on `PATH`.

## Authentication and read-only checks

Keep credentials out of shell history and source control. Use an existing KVMD token when available:

```sh
export KVMCTL_TOKEN='short-lived-device-token'
kvmctl --url https://kvm.example.test --token "$KVMCTL_TOKEN" capabilities
```

Login credentials are also supported. Prefer a secret manager or an environment injection mechanism rather than typing passwords into command history:

```sh
kvmctl --url https://kvm.example.test \
  --user "$KVMCTL_USER" --password "$KVMCTL_PASSWORD" capabilities
```

Use a trusted CA bundle where possible. `--insecure` is available for explicitly trusted devices using self-signed certificates.

Capture and verify the current screen:

```sh
kvmctl --url https://kvm.example.test --token "$KVMCTL_TOKEN" \
  snapshot --out /tmp/kvm-screen.jpg
kvmctl --url https://kvm.example.test --token "$KVMCTL_TOKEN" \
  verify workstation
```

A virtual-hosted device can be addressed with `--host` (or `KVMCTL_HOST` for MCP and library configuration):

```sh
kvmctl --url https://kvm.example.test --host kvm.example.test \
  --token "$KVMCTL_TOKEN" capabilities
```

## Scenario: select and verify a machine

State-changing operations require two deliberate confirmations: `--yes` and an explicit transport. This example is redacted but matches the live-tested GLKVM/TH41-3 workflow:

```sh
export KVM_URL='https://<glkvm-address>'
export KVM_HOST='glkvm.local'
export KVM_USER='admin'
# Retrieve KVM_PASSWORD from your secret manager at runtime.
export KVM_PASSWORD='***'

kvmctl \
  --url "$KVM_URL" --host "$KVM_HOST" \
  --user "$KVM_USER" --password "$KVM_PASSWORD" \
  --insecure --yes --transport kvm select pve2
```

The command re-arms the USB/OTG gadget when needed, sends the profile's held-key sequence, retains the authenticated stream WebSocket, and verifies the resulting screen. A successful result is structured JSON, for example:

```json
{
  "operation": "select",
  "transport": "kvm",
  "read_only": false,
  "ok": true,
  "evidence": {
    "machine": "pve2",
    "port": 2,
    "verified": true,
    "state": "verified",
    "detail": "prompt pattern match"
  }
}
```

The verified TH41-3 recipe is `Right Ctrl` twice, the port digit, then `Enter`; each key is sent sequentially and held briefly. See [`docs/TH41-3.md`](docs/TH41-3.md) and [`docs/OPERATOR_RUNBOOK.md`](docs/OPERATOR_RUNBOOK.md) for physical prerequisites and recovery behavior.

If you omit either safety gate, `kvmctl` refuses to perform the operation. Unknown or disabled machine names never generate HID traffic.

## Recovery scenarios

When the HID path is stuck but no target selection is needed:

```sh
kvmctl --url "$KVM_URL" --host "$KVM_HOST" \
  --user "$KVM_USER" --password "$KVM_PASSWORD" \
  --insecure --yes hid-reset
```

To re-arm the USB gadget after a failed selection:

```sh
kvmctl --url "$KVM_URL" --host "$KVM_HOST" \
  --user "$KVM_USER" --password "$KVM_PASSWORD" \
  --insecure --yes rearm-otg
```

A temporary snapshot HTTP 503 after OTG re-arm can be expected. The recovery path nudges the encoder, reopens the authenticated stream, retries the snapshot, and only then verifies the target. Do not enter target credentials during recovery.

## Optional live smoke test

The live test is read-only: it discovers capabilities and requests one snapshot. It does not select ports, send HID events, or change OTG state.

```sh
KVMCTL_LIVE_URL=https://kvm.example.test \
KVMCTL_LIVE_TOKEN="$KVMCTL_TOKEN" \
KVMCTL_LIVE_HOST=kvm.example.test \
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_live_hardware.py
```

## MCP integration

Install the optional MCP adapter with `.venv/bin/pip install -e '.[mcp]'`, then configure an MCP client to launch `kvmctl-mcp`. It uses `KVMCTL_URL` and `KVMCTL_TOKEN` (or the optional login variables), is read-only by default, and returns snapshots as native MCP image content. Writes require `KVMCTL_WRITE_ENABLED=1` plus each operation's transport and policy requirements. See [`docs/MCP.md`](docs/MCP.md).

## Development

```sh
.venv/bin/python -m pytest tests -q
```

Device interactions in the regular test suite use mocked transports. Live hardware tests are opt-in and skipped unless their environment variables are configured.

## License

See the repository license file for project licensing information.
