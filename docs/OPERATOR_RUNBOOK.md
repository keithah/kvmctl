# kvmctl operator runbook: GLKVM and HDMI/KVM switches

This runbook covers safe, non-destructive operation of a GLKVM and a profile-driven HDMI/KVM switch. The included four-port profile is tested with a Terived TH41-3; the workflow is not limited to that model.

## Rack map

| Switch port | Target | Address / identity | Status |
|---|---|---|---|
| 1 | pve1 | `192.168.42.3:8006`, console prompt `pve1 login:` | Live-verified |
| 2 | pve2 | `192.168.42.4:8006`, console prompt `pve2 login:` | Currently selected and live-verified |
| 3 | Mac mini Kodi build box | M1 Mac | Mapped; switching visually verified |
| 4 | pve3 | `192.168.42.5` | Mapped; switching visually verified |

For the tested profile, the KVM's USB cable must be connected to the switch's keyboard-marked port and its physical hotkey control must be on. Other switch profiles may have different wiring requirements.

## Credentials and common variables

Retrieve the GLKVM URL, virtual host, username, and password from the configured secret manager at runtime. Never put credentials in this repository, shell history, logs, screenshots, or review comments.

```sh
export KVM_URL='https://<glkvm-address>'
export KVM_HOST='glkvm.local'
export KVM_USER='admin'
# Inject this from a secret manager; do not commit or print it.
export KVM_PASSWORD='***'
```

The device uses an authenticated stream WebSocket while snapshots are captured. A self-signed device certificate requires `--insecure` only when the device is explicitly trusted.

## Scenario 1: safe read-only checks

```sh
kvmctl --url "$KVM_URL" --host "$KVM_HOST" \
  --user "$KVM_USER" --password "$KVM_PASSWORD" \
  --insecure capabilities
kvmctl --url "$KVM_URL" --host "$KVM_HOST" \
  --user "$KVM_USER" --password "$KVM_PASSWORD" \
  --insecure verify pve2
```

A snapshot needs an authenticated `stream=1` WebSocket to remain open. A 503 without that held stream is expected and does not by itself indicate a dead camera.

## Scenario 2: select and verify a target

Selection changes KVM state and requires both `--yes` and explicit `--transport kvm`:

```sh
kvmctl --url "$KVM_URL" --host "$KVM_HOST" \
  --user "$KVM_USER" --password "$KVM_PASSWORD" \
  --insecure --yes --transport kvm select pve2
```

The reusable implementation performs the verified sequence:

1. Re-arm the Comet OTG gadget when enabled: storage functions on for about 8 s, then off for about 12 s.
2. Hold and release `ControlRight`, twice; send the target `Digit<N>`; send `Enter`. Each hold is about 120 ms with a 150 ms gap.
3. Settle about 5 s, recover the stream if needed, capture a fresh snapshot, and verify the target prompt or identity.

Expected sanitized result:

```json
{"operation":"select","transport":"kvm","read_only":false,"ok":true,"evidence":{"machine":"pve2","port":2,"verified":true,"state":"verified","detail":"prompt pattern match"}}
```

## Scenario 3: streamer recovery

After OTG re-arm, one initial snapshot HTTP 503 may be normal. The recovery path nudges the encoder, retries the snapshot, and reopens the authenticated stream WebSocket. Do not bypass verification or enter a target password while the stream is recovering.

## Scenario 4: failed selection

Inspect a fresh snapshot first. If the HID path is stuck, re-arm OTG and repeat the selection only after confirming the physical wiring:

```sh
kvmctl --url "$KVM_URL" --host "$KVM_HOST" \
  --user "$KVM_USER" --password "$KVM_PASSWORD" \
  --insecure --yes rearm-otg
```

A failed hotkey prefix can leak the digit and Enter to the focused console. Clean up only with a non-destructive `Ctrl-C` if necessary, then verify that no password prompt or pending command remains.

## Scenario 5: HID-only recovery

Use this when the HID path is stuck and no target selection is needed:

```sh
kvmctl --url "$KVM_URL" --host "$KVM_HOST" \
  --user "$KVM_USER" --password "$KVM_PASSWORD" \
  --insecure --yes hid-reset
```

This resets the Comet HID subsystem; it does not power-cycle a target or replace a TH41-3 switch reset.

## Scenario 6: named host operations

Named host operations use a configured argv-only runner and do not accept arbitrary commands:

```sh
kvmctl --url "$KVM_URL" --host "$KVM_HOST" \
  --user "$KVM_USER" --password "$KVM_PASSWORD" \
  --insecure host-identity-inspect
kvmctl --url "$KVM_URL" --host "$KVM_HOST" \
  --user "$KVM_USER" --password "$KVM_PASSWORD" \
  --insecure host-graphics-inspect
kvmctl --url "$KVM_URL" --host "$KVM_HOST" \
  --user "$KVM_USER" --password "$KVM_PASSWORD" \
  --insecure service-render-access-inspect
```

Host reboot is separately authorized and requires a target-bound confirmation token. Do not test it during KVM switching validation.

## Keyboard pass-through status

After a target is selected, the GLKVM HID connection can deliver keyboard events to that target. The low-level Python API and the guarded CLI/MCP controls are available:

```python
client.press_key("Enter")
client.type_text("example")
```

The CLI now provides `send-text`, `send-keys`, `hold-key`, `release-all`, `mouse-move`, `mouse-move-pct`, `mouse-click`, `mouse-scroll`, `ocr-screenshot`, and `ocr-click`. All input actions require `--yes`; `ocr-screenshot` is read-only. `exec-command` runs an allowlisted SSH command on a host; it does not type the command through the attached keyboard.

## Safety boundaries

- Do not issue power, shutdown, reboot, firmware, storage, factory-reset, or arbitrary-console commands during KVM verification.
- Leave the Comet storage gadget off after any re-arm.
- Leave the console at a clean login prompt; do not submit credentials.
- Use `ControlRight`, never `ControlLeft`, for the TH41-3 protocol.
- Unknown or disabled machine names must not generate HID traffic.

## Target-bound sequence operations

Use the same three-stage safety boundary for every declarative sequence: plan it, explicitly authorize it, then execute it only after the target is selected and visually verified.

```sh
kvmctl --url "$KVM_URL" --host "$KVM_HOST" --user "$KVM_USER" --password "$KVM_PASSWORD" \
  --insecure sequence-plan --plan ./plan.json
kvmctl --url "$KVM_URL" --host "$KVM_HOST" --user "$KVM_USER" --password "$KVM_PASSWORD" \
  --insecure --yes sequence-authorize --plan ./plan.json --ttl 30
kvmctl --url "$KVM_URL" --host "$KVM_HOST" --user "$KVM_USER" --password "$KVM_PASSWORD" \
  --insecure --yes sequence-execute --plan ./plan.json --ttl 30
```

Plans are limited to 10 actions, 30 seconds total, and 5 seconds per held key. Use only validated key names/chords and bounded mouse/text actions. Named workflows use `workflow-list`, `workflow-inspect`, and `workflow-execute`; always copy the current revision from inspection and provide the expected target. A target-independent workflow must still be bound to an explicit target at invocation.

The executor aborts on a target/session mismatch, changed plan hash, expired authorization, deadline, or unexpected state. A lock-conflict rejection occurs before this invocation owns the device lock or any keys, so it records the abort and returns without performing ownership cleanup; all paths after lock ownership release keys, close streams they own, and release the device lock. Check `ok`, `completed_steps`, `cleanup_ok`, and `cleanup_errors` in the JSON result before retrying. Do not put passwords, tokens, cookies, authorization headers, or secrets in plans, logs, or screenshots; journal, result, and inspection output is redacted.

KVM sequence keyboard actions are HID events delivered to the selected machine. `exec-command` is not a keyboard shortcut: it is a separate SSH operation restricted to the configured base-command allowlist, explicit `transport=ssh`, and write authorization. Never use it as a way to bypass the KVM target boundary.

## Verification record

- Full repository suite: **256 passed, 3 skipped**.
- Live reusable CLI selection: **pve2 selected and verified**; visible evidence showed `https://192.168.42.4:8006/` and `pve2 login:`.
- The device-specific port mapping and protocol timings are recorded in [`../PROBE_NOTES.md`](../PROBE_NOTES.md).
