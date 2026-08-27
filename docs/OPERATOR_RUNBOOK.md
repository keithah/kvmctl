# kvmctl operator runbook: GLKVM / TH41-3

This runbook covers safe, non-destructive operation of the GLKVM Comet and the Terived TH41-3 HDMI/KVM switch.

## Rack map

| TH41-3 port | Target | Address / identity | Status |
|---|---|---|---|
| 1 | pve1 | `192.168.42.3:8006`, console prompt `pve1 login:` | Working; freshly verified selected target |
| 2 | pve2 | `192.168.42.4:8006`, console prompt `pve2 login:` | Working; verified selected target |
| 3 | Mac mini Kodi build box | M1 Mac | Mapped; switching has been visually verified |
| 4 | pve3 | `192.168.42.5` | Mapped; switching has been visually verified |

The Comet must be connected to the TH41-3 port marked with the keyboard icon, and the physical Hot key switch must be on (green LED lit).

## Safe read-only checks

Use the configured GLKVM URL and retrieve credentials from 1Password; never put passwords or tokens in this repository, shell history, logs, or screenshots.

```sh
PYTHONPATH=. .venv/bin/pytest -q
```

The client is read-only by default. Safe semantic checks are `capabilities`, `snapshot`, and `verify`:

```sh
kvmctl --url https://<glkvm-host> --insecure capabilities
kvmctl --url https://<glkvm-host> --insecure verify pve2
```

A snapshot requires an authenticated `stream=1` WebSocket to remain open. A 503 without that held stream is expected and does not by itself indicate a dead camera.

## Selecting a target

Selection changes KVM state. It requires both explicit authorization and an explicit transport:

```sh
kvmctl --url https://<glkvm-host> --insecure --yes --transport kvm select pve2
```

The implemented and visually verified recipe is:

1. Re-arm the switch by bouncing the Comet OTG gadget: enable both storage functions, wait about 8 seconds, then disable both and wait about 12 seconds.
2. Send, strictly sequentially, `ControlRight` held for 120 ms and released, then a 150 ms gap; repeat Right Ctrl; send `Digit<N>` with the same hold/gap; send `Enter`.
3. Wait about 5 seconds, reopen the stream WebSocket, and verify the target from a fresh snapshot. After an OTG bounce, one initial snapshot 503 and an encoder nudge are expected streamer recovery behavior.

Do not use `ControlLeft`; the TH41-3 hotkey engine requires Right Ctrl. Do not send factory reset (`Right Ctrl` x2, `Escape`, `Enter` x3).

## Recovery

### HID reset (safe, state-changing)

Use only when the HID path is stuck and no target selection is needed:

```sh
kvmctl --url https://<glkvm-host> --insecure --yes hid-reset
```

This resets the Comet HID subsystem. It does not power-cycle a target and does not replace a TH41-3 switch reset.

### Re-arm after a failed selection

```sh
kvmctl --url https://<glkvm-host> --insecure --yes rearm-otg
```

Then repeat selection and verification. Failed hotkey prefixes can leak the digit and Enter to the focused console; clean up with a non-destructive Ctrl-C if necessary, and verify that no password prompt or pending command remains.

### If the hotkey engine remains inactive

1. Confirm the green Hot key On LED is lit.
2. Confirm the Comet USB cable is in the TH41-3 keyboard-marked port.
3. Power-cycle the TH41-3 itself only if physical intervention is authorized; it returns to its default port (2).
4. Re-arm OTG, select pve2, and verify from a fresh snapshot.

Do not use the manual's factory-reset sequence. The manual's `[R]` switch-side reset is itself a hotkey and is unavailable when the hotkey engine is disarmed.

## Safety boundaries

- Do not issue power, shutdown, reboot, firmware, storage, factory-reset, or arbitrary-console commands during verification.
- Leave the Comet storage gadget off (`start_cdrom=false`, `start_flash=false`) after any re-arm.
- Leave the console at a clean login prompt; do not submit credentials.
- Treat pve1 as a verified, working target; leave it at its clean login prompt after verification.

## Verification record

- Full repository suite: 83 tests passed (`PYTHONPATH=. .venv/bin/pytest -q`).
- Existing visual evidence confirms pve2: `evidence/glk_where_now.jpg` shows `pve2 login:` and `https://192.168.42.4:8006/`.
- Fresh live verification (2026-08-26): authenticated with the known working admin credential, held `/api/ws?stream=1`, re-armed the TH41-3 via the documented OTG bounce, selected port 1 using held Right Ctrl ×2 → Digit1 → Enter, and captured `evidence/pve1-20260826-live.jpg`. Local Tesseract read `https://192.168.42.3:8006/` and `pve1 login:`.
- Existing probe record documents the port 1/2/3/4 mapping and held-key/OTG-rearm recipe: `PROBE_NOTES.md`.
