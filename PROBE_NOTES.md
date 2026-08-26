# Live GLKVM / TH41-3 probe

Date: 2026-08-25

## Verified

- GLKVM reachable at `https://192.168.42.223` (`glkvm.local` via Host header).
- Login endpoint requires URL-encoded form fields: `user=admin&passwd=...`.
- The supplied machine password `[REDACTED-DEVICE-CREDENTIAL]` authenticated successfully for the GLKVM admin account.
- Authenticated API requests use the lowercase `token:` header. Bearer auth and cookies do not work.
- `/api/auth/check` returns `ok: true` with that header.
- `/api/info` reports KVMD `4.82`, ustreamer `6.13`, ARM/RV1126 platform, and WebRTC/Janus/Pion extras.
- HID is enabled, connected, and not busy.
- OCR is present but disabled and has no available language packs (`tesseract`). Verification will need local image OCR or external vision initially.
- `/api/switch` and `/api/switch/set_active` are present in the Comet web frontend but `/api/switch` returns 404 on this device. Treat native switch control as capability-detected and unavailable here.
- Opening an authenticated WebSocket at `/api/ws?stream=1` returns `101 Switching Protocols` and is required to keep ustreamer alive. Without it, snapshot returns HTTP 503.
- Snapshot endpoint works while the stream WebSocket is held open:
  `/api/streamer/snapshot?preview=true&preview_max_width=1280&preview_quality=70`
- Current port 2 is confirmed visually as Proxmox `pve2 login:` with banner URL `https://192.168.42.4:8006/`.
- KVMD key names are browser-style: `ControlRight`, `Digit2`, `Enter`.
- `POST /api/hid/events/send_key?key=...` returns 200 for valid discrete key taps.

## Switch mapping

- Port 1: pve1 (currently not working)
- Port 2: pve2 (working; currently selected/verified)
- Port 3: Kodi build box M1 Mac
- Port 4: pve3

## Important unresolved behavior

A reselect of port 2 returned 200 for `ControlRight`, `ControlRight`, `Digit2`, `Enter`, but a subsequent port-3 attempt with 120ms inter-key delays still showed pve2. This does **not** prove the sequence is wrong: likely remaining variables are switch timing, key-hold duration, or the exact TH41-3 hotkey mode. Do not claim port switching is complete until a port-3 transition is visually confirmed.

The test harness must keep the `stream=1` WebSocket open across the entire select/snapshot/verify cycle.

## Evidence

- `evidence/port2-20260825-165744.jpg` — initial pve2 prompt
- `evidence/glk_before.jpg` / `evidence/glk_after.jpg` — pve2 before/after
- `evidence/glk_port3_mac.jpg` — unexpectedly still pve2; negative evidence
- `evidence/glk_port2_back.jpg` — pve2 after attempted round trip

## Safety

No storage, power, firmware, or destructive operations were used. The machine password is stored only in 1Password and must not be written to config, logs, tests, or this repository.
