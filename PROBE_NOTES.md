# Live GLKVM / TH41-3 probe

Date: 2026-08-25

## Verified

- GLKVM reachable at `https://192.168.42.223` (`glkvm.local` via Host header).
- Login endpoint requires URL-encoded form fields: `user=admin&passwd=...`.
- The supplied machine password `[REDACTED-DEVICE-CREDENTIAL]` authenticated
  successfully for the GLKVM admin account.
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

- Port 1: pve1 (freshly verified working; `pve1 login:` and `https://192.168.42.3:8006/`)
- Port 2: pve2 (working; currently selected/verified)
- Port 3: Kodi build box M1 Mac
- Port 4: pve3

## Historical failed attempts (before physical checks)

Before the physical Hot key and keyboard-port checks were corrected, a reselect of port 2 returned 200 for `ControlRight`, `ControlRight`, `Digit2`, `Enter`, but a subsequent port-3 attempt still showed pve2. That negative result was superseded by the repeatable recipe documented below after the switch was power-cycled and the physical setup was corrected.

The test harness must keep the `stream=1` WebSocket open across the entire select/snapshot/verify cycle.

## Evidence

- `evidence/port2-20260825-165744.jpg` — initial pve2 prompt
- `evidence/glk_before.jpg` / `evidence/glk_after.jpg` — pve2 before/after
- `evidence/glk_port3_mac.jpg` — unexpectedly still pve2; negative evidence
- `evidence/glk_port2_back.jpg` — pve2 after attempted round trip

## Safety

No storage, power, firmware, or destructive operations were used. The machine password is stored only in 1Password and must not be written to config, logs, tests, or this repository.

## TH41-3 manual findings (photo of official manual, 2026-08-25)

- Hotkey grammar: Right Ctrl pressed twice -> Port No. -> Enter. Right Ctrl specifically.
- RCtrl x2 [S] Enter: auto-scan toggle. RCtrl x2 [S] [N] Enter: scan interval 5-999s (default 8s).
- RCtrl x2 [B] Enter: beep toggle. Successful hotkey prefix should beep when beep is on.
- RCtrl x2 [T] Enter: "detection function" toggle, default OFF (purpose not fully documented).
- RCtrl x2 [R] Enter: reset the KVM system to solve keyboard/mouse freeze (switch-side reset).
- RCtrl x2 ESC Enter x3: factory reset (avoid).
- Physical "Hot key on/off" button exists with a green Hotkey On LED.
- Note: keyboard/mouse must be plugged into the switch ports marked with the keyboard/mouse icons for hotkeys and mouse-click switching to work.

## Empirical switching log (all via KVMD send_key, stream ws held)

- Storage gadget ON:  hotkeys never worked (matches listing requirement).
- Immediately after otg_functions start_cdrom/start_flash -> false (USB re-enumeration):
  ONE sequence worked (2 -> 3, visually confirmed pve3 login).
- Every subsequent attempt failed (keystrokes pass through to the focused machine):
  timings 150ms/200ms/300ms/RTT-only, ControlLeft and ControlRight, RCtrl x2 alone,
  after Escape re-arm keys, after POST /api/hid/reset.
- Conclusion: the TH41-3 hotkey engine appears to arm on USB (re)enumeration events
  and disengage afterward. Re-arm recipe to test: bounce the OTG gadget, optionally
  send [T] (detection) or [R] (switch reset) while armed, then send the select sequence.

## Exhausted remote avenues (2026-08-25, all negative)

- ControlLeft variant (manual says Right Ctrl only - expected fail, confirmed).
- Timing: 150ms / 200ms / 300ms / RTT-only (~40ms) inter-key gaps.
- RCtrl x2 alone (no cycle, no switch).
- 9-minute keystroke silence then single sequence (not a flood lockout).
- POST /api/hid/reset then sequence (no re-arm).
- OTG gadget on->off bounce (fresh USB re-enumeration) then sequence (no re-arm).
- Mouse middle-click switch (manual's mouse-click switching): no effect.
  REST mouse endpoints confirmed working: send_mouse_move, send_mouse_button.

## Keystroke pass-through proof

Every "failed" sequence leaked its tail into the focused machine's console
(Digit4/Enter visible at pve3 login). HID typing path is healthy end-to-end;
only the switch's hotkey engine ignores the prefix.

## Current rack state (left clean)

- Console: pve3 login prompt, no pending password prompt (Ctrl-C sent).
- Comet storage gadget: start_cdrom=false, start_flash=false (required state).
- Switch: port 3.

## Blocked on physical checks (needs Keith)

1. TH41-3 physical Hot key on/off button + green Hotkey On LED (manual page XI).
   If LED is off, hotkeys are disabled at the switch.
2. Comet USB cable must terminate in the switch port marked with the KEYBOARD
   icon (dedicated hotkey port), per manual note and product listing.
3. If both check out: power-cycle the TH41-3 (its own [R] reset is a hotkey,
   unusable while the engine is disarmed). Then retest immediately.

## RESOLVED: TH41-3 switching recipe (2026-08-25 late)

After Keith power-cycled the switch (LED on, Comet in keyboard port - both verified good),
the winning repeatable recipe is:

1. OTG gadget bounce (USB attach event arms the hotkey engine):
   POST /api/system/otg_functions?start_cdrom=true&start_flash=true   (wait 8s)
   POST /api/system/otg_functions?start_cdrom=false&start_flash=false (wait 12s)
2. Held-key sequence (taps are filtered by the switch's detector):
   for key in [ControlRight, ControlRight, Digit<N>, Enter]:
     send_key key state=true, hold 120ms, state=false, gap 150ms
3. Wait ~5s, then snapshot verify (stream WS must be re-established after bounce;
   encoder nudge set_params desired_fps=40 quality=80 revives a 503 streamer).

Evidence: 4 -> Mac mini desktop verified visually (Keyboard Setup Assistant visible);
then 4 -> 2 verified (clean pve2 login). Earlier 2 -> 3 also matched this pattern.

Side effects to handle in code:
- OTG bounce kills the streamer: expect one 503, nudge encoder, open fresh stream WS.
- macOS may pop Keyboard Setup Assistant for the ASIX HID device after switching to
  the Mac; dismiss with Quit (mouse click works).

Rack state: console on pve2 (port 2), clean login prompt, storage gadget off.
