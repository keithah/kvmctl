# kvmctl

Safe, capability-driven orchestration for KVMD-compatible PiKVM/GLKVM devices and external HDMI/KVM switches.

This project provides:

- A Python client for GLKVM/KVMD authentication, capabilities, HID, snapshots, OCR, and streamer recovery.
- A verified Terived TH41-3 HDMI-switch driver using the switch's required held-key timing and USB re-arm sequence.
- Named machine profiles with explicit selection state and screen verification.
- A CLI and semantic MCP surface with read/write policy gates and machine-readable evidence.
- Extensible machine profiles with configurable port limits; the included rack is the
  verified four-port setup, while other switch profiles can be added without changing
  the client or semantic surface.

## Hardware verified

- GL.iNet GLKVM Comet, KVMD 4.82
- Terived TH41-3 four-port HDMI/KVM switch
- Rack mapping: port 1 pve1, port 2 pve2, port 3 Kodi build box/M1 Mac mini, port 4 pve3

The TH41-3 requires the Comet USB cable in its keyboard-marked port, the physical Hot key switch enabled, storage gadgets off, and the following sequence:

1. OTG gadget bounce to trigger USB re-enumeration.
2. Right Ctrl twice, port digit, Enter; each key is held for 120 ms with 150 ms gaps.
3. Reopen the authenticated stream and verify the selected screen.

See [`PROBE_NOTES.md`](PROBE_NOTES.md) and [`docs/OPERATOR_RUNBOOK.md`](docs/OPERATOR_RUNBOOK.md) for the complete evidence and recovery procedures.

## Credentials and configuration

Credentials are intentionally not part of this repository. Store them in a password manager or inject them through a secure process environment. The local device API uses its own device credential; the GLKVM website account is separate. Never commit passwords, tokens, screenshots containing secrets, or shell history containing credentials.

Typical read-only usage:

```sh
kvmctl --url https://<glkvm-host> --insecure --token "$KVMCTL_TOKEN" capabilities
kvmctl --url https://<glkvm-host> --insecure --token "$KVMCTL_TOKEN" snapshot --out /tmp/glkvm.jpg
```

Mutating operations require explicit authorization and transport selection:

```sh
kvmctl --url https://<glkvm-host> --insecure --token "$KVMCTL_TOKEN" \
  --yes --transport kvm select pve1
```

For unattended or shared environments, prefer a CA bundle instead of `--insecure`.

### MCP integration

Install the optional MCP adapter with `.venv/bin/pip install -e '.[mcp]'`, then
configure an MCP client to launch `kvmctl-mcp`. It uses `KVMCTL_URL` and
`KVMCTL_TOKEN` (or the optional login variables), is read-only by default, and
returns snapshots as native MCP image content. Writes require the explicit
`KVMCTL_WRITE_ENABLED=1` environment gate plus each operation's transport and
policy requirements. See [`docs/MCP.md`](docs/MCP.md) for the complete tool and
environment reference.

## Development

```sh
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
PYTHONPATH=. .venv/bin/pytest -q
```

The test suite uses mocked transports for device interactions. Live hardware operations are intentionally not part of automated tests.

### Optional live hardware smoke test

The read-only smoke test requires explicit environment variables and performs only
capability discovery and one snapshot; it never selects ports, sends HID events, or
changes OTG state:

```sh
KVMCTL_LIVE_URL=https://glkvm.local \
KVMCTL_LIVE_TOKEN="$KVMCTL_TOKEN" \
KVMCTL_LIVE_INSECURE=1 \
PYTHONPATH=. .venv/bin/pytest -q tests/test_live_hardware.py
```

## Safety boundaries

The semantic surface does not provide arbitrary API passthrough. Power, reboot, firmware, factory-reset, and arbitrary console operations are outside the safe selection workflow. Review the runbook before operating real hardware.

## Status

Experimental, hardware-tested tooling. The core behavior is currently tailored to the verified GLKVM Comet and TH41-3 setup, while the client and switch abstractions are designed for extension to other KVMD-compatible hardware.
