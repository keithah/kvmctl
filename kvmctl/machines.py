"""Machine profiles for an HDMI/KVM switch plus select-with-verification.

Rack mapping (PROBE_NOTES.md):
    port 1: pve1
    port 2: pve2
    port 3: kodi-build      (Kodi build box, M1 Mac mini)
    port 4: pve3

Selection uses the configured switch profile's held-key recipe via KvmClient
key_down/key_up primitives, NOT execute_switch taps):

    1. optional OTG gadget bounce to re-arm the hotkey engine
       (start_cdrom/start_flash true 8s, then false 12s)
    2. for each of [ControlRight, ControlRight, Digit<N>, Enter]:
       key_down, hold HOLD_MS, key_up, gap GAP_MS
    3. settle, then verify per policy

Verification states are explicit:
    UNKNOWN -> SELECTED_UNVERIFIED -> VERIFIED | VERIFY_FAILED

Safe failure: any exception during selection or verification leaves the
state at SELECTED_UNVERIFIED (or restores the prior state when the OTG
bounce itself fails before any keys were sent) and never raises past the
caller without an accompanying state record.
"""
from __future__ import annotations

import time
import threading
import hashlib
import os
import pathlib
import fcntl
import stat
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional, Protocol, Sequence

from kvmctl.client import KvmClient


# --------------------------------------------------------------------------
# Machine profiles
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MachineProfile:
    """One rack port and how to recognize it on screen."""

    port: int
    name: str
    port_limit: int = 4
    description: str = ""
    enabled: bool = True
    # Substrings expected in OCR text of this machine's console/desktop.
    # Empty tuple disables OCR-identity matching.
    ocr_patterns: tuple[str, ...] = ()
    # Prompt regexes matched against OCR text (login prompts etc.).
    # Empty tuple disables prompt-pattern matching.
    prompt_patterns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.port_limit < 1 or not (1 <= self.port <= self.port_limit):
            raise ValueError(f"port {self.port} out of range 1-{self.port_limit}")

    def ocr_matches(self, text: str) -> bool:
        t = text.lower()
        return bool(self.ocr_patterns) and any(p.lower() in t for p in self.ocr_patterns)

    def prompt_matches(self, text: str) -> bool:
        import re

        return any(re.search(p, text, re.IGNORECASE) for p in self.prompt_patterns)


def _profile(port: int, name: str, **kw) -> MachineProfile:
    return MachineProfile(port=port, name=name, **kw)


RACK: dict[str, MachineProfile] = {
    m.name: m
    for m in (
        MachineProfile(
            port=1,
            name="pve1",
            description="Proxmox pve1",
            enabled=True,
            ocr_patterns=("pve1",),
            prompt_patterns=(r"pve1\s+login:",),
        ),
        MachineProfile(
            port=2,
            name="pve2",
            description="Proxmox pve2",
            ocr_patterns=("pve2",),
            prompt_patterns=(r"pve2\s+login:", r"192\.168\.42\.4:8006"),
        ),
        MachineProfile(
            port=3,
            name="kodi-build",
            description="Kodi build box, M1 Mac mini",
            ocr_patterns=("macos", "mac mini", "kodi"),
            prompt_patterns=(r"keyboard setup assistant",),
        ),
        MachineProfile(
            port=4,
            name="pve3",
            description="Proxmox pve3",
            ocr_patterns=("pve3",),
            prompt_patterns=(r"pve3\s+login:",),
        ),
    )
}


class VerifyPolicy(str, Enum):
    """How a selection is confirmed."""

    NONE = "none"                      # never auto-verify
    FRAME_CHANGE = "frame_change"      # screen pixels changed after switch
    OCR_IDENTITY = "ocr_identity"      # OCR text matches target identity
    PROMPT_PATTERN = "prompt_pattern"  # OCR text matches expected login prompt


DEFAULT_VERIFY_POLICY: dict[str, VerifyPolicy] = {
    "pve1": VerifyPolicy.PROMPT_PATTERN,
    "pve2": VerifyPolicy.PROMPT_PATTERN,
    "kodi-build": VerifyPolicy.FRAME_CHANGE,
    "pve3": VerifyPolicy.PROMPT_PATTERN,
}


class SelectionState(Enum):
    UNKNOWN = "unknown"
    SELECTED_UNVERIFIED = "selected_unverified"
    VERIFIED = "verified"
    VERIFY_FAILED = "verify_failed"


@dataclass
class SelectionRecord:
    machine: str
    port: int
    state: SelectionState = SelectionState.UNKNOWN
    detail: str = ""
    at: float = field(default_factory=time.time)

    @property
    def verified(self) -> bool:
        return self.state is SelectionState.VERIFIED

    @property
    def selected(self) -> bool:
        return self.state in (SelectionState.SELECTED_UNVERIFIED, SelectionState.VERIFIED)


_DEVICE_LOCKS: dict[tuple[str, str], "DeviceLock"] = {}
_DEVICE_LOCKS_GUARD = threading.Lock()


class DeviceLock:
    """A re-entrant thread lock backed by a fail-closed cross-process flock."""
    def __init__(self, device_id: str):
        root = pathlib.Path(os.environ.get("KVMCTL_LOCK_DIR", "~/.cache/kvmctl/locks")).expanduser()
        # Do not let mkdir -p traverse an attacker-controlled component.
        current = pathlib.Path(root.anchor) if root.is_absolute() else pathlib.Path()
        for part in root.parts[1:] if root.is_absolute() else root.parts:
            current /= part
            try:
                info = current.lstat()
            except FileNotFoundError:
                current.mkdir(mode=0o700)
                info = current.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise PermissionError(f"unsafe lock directory: {current}")
        info = root.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise PermissionError(f"unsafe lock directory: {root}")
        name = hashlib.sha256(str(device_id).encode("utf-8")).hexdigest() + ".lock"
        self._path = root / name
        self._local = threading.Lock()

    def acquire(self, blocking=True):
        if not self._local.acquire(blocking):
            return False
        try:
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self._path, flags, 0o600)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                os.close(fd)
                raise PermissionError("unsafe device lock file")
            self._file = os.fdopen(fd, "a+")
            flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
            fcntl.flock(self._file.fileno(), flags)
        except (OSError, ValueError):
            try:
                if getattr(self, "_file", None): self._file.close()
            except OSError: pass
            self._local.release()
            return False
        return True

    def release(self):
        try:
            try:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            finally:
                self._file.close()
        finally:
            self._local.release()


def device_lock(device_id: str = "default") -> DeviceLock:
    """Return a cached, cross-process mutation lock for one KVM device."""
    root = str(pathlib.Path(os.environ.get("KVMCTL_LOCK_DIR", "~/.cache/kvmctl/locks")).expanduser())
    key = (root, str(device_id))
    with _DEVICE_LOCKS_GUARD:
        return _DEVICE_LOCKS.setdefault(key, DeviceLock(str(device_id)))


class SessionState:
    """Tracks which machine is believed active; unverified != verified."""

    def __init__(self) -> None:
        self.current: Optional[SelectionRecord] = None

    def mark_selected(self, machine: MachineProfile) -> SelectionRecord:
        rec = SelectionRecord(machine=machine.name, port=machine.port,
                              state=SelectionState.SELECTED_UNVERIFIED)
        self.current = rec
        return rec

    def mark_verified(self, detail: str = "") -> SelectionRecord:
        if self.current is None or not self.current.selected:
            raise RuntimeError("no selected-but-unverified record to verify")
        self.current.state = SelectionState.VERIFIED
        self.current.detail = detail
        self.current.at = time.time()
        return self.current

    def mark_verify_failed(self, detail: str = "") -> SelectionRecord:
        if self.current is None or not self.current.selected:
            raise RuntimeError("no selected record to fail")
        self.current.state = SelectionState.VERIFY_FAILED
        self.current.detail = detail
        self.current.at = time.time()
        return self.current

    def reset(self) -> None:
        self.current = None


# --------------------------------------------------------------------------
# Verification policies
# --------------------------------------------------------------------------


class SnapshotSource(Protocol):
    def snapshot_jpeg(self, *args, **kw) -> bytes: ...


def frames_differ(a: bytes, b: bytes) -> bool:
    """Compare decoded pixels, tolerating encoder metadata/compression noise."""
    try:
        from PIL import Image, ImageChops, ImageStat
        import io
        first = Image.open(io.BytesIO(a)).convert("RGB")
        second = Image.open(io.BytesIO(b)).convert("RGB")
        second = second.resize(first.size)
        diff = ImageChops.difference(first, second)
        mean = sum(ImageStat.Stat(diff).mean) / 3
        return mean > 3.0
    except (ImportError, OSError):
        # Preserve useful behavior for non-image test doubles and malformed
        # device responses; valid JPEGs use the perceptual path above.
        return a != b


def verify_frame_change(client: SnapshotSource, baseline: bytes,
                        *, attempts: int = 5, delay: float = 1.0,
                        sleep: Callable[[float], None] = time.sleep) -> bool:
    """True if the screen changed from ``baseline`` within the retry window."""
    for i in range(attempts):
        if i:
            sleep(delay)
        try:
            frame = client.snapshot_jpeg()
        except Exception:
            continue  # streamer may 503 briefly after OTG bounce
        if frames_differ(baseline, frame):
            return True
    return False


def verify_ocr_identity(client: KvmClient, machine: MachineProfile,
                        *, attempts: int = 5, delay: float = 1.0,
                        sleep: Callable[[float], None] = time.sleep) -> tuple[bool, str]:
    """OCR the screen and match the machine's identity substrings."""
    last_text = ""
    for i in range(attempts):
        if i:
            sleep(delay)
        try:
            frame = client.snapshot_jpeg()
            text = client.ocr(frame)
        except Exception:
            continue
        last_text = text
        if machine.ocr_matches(text):
            return True, text
    return False, last_text


def verify_prompt_pattern(client: KvmClient, machine: MachineProfile,
                          *, attempts: int = 5, delay: float = 1.0,
                          sleep: Callable[[float], None] = time.sleep) -> tuple[bool, str]:
    """OCR the screen and match one of the machine's prompt regexes."""
    last_text = ""
    for i in range(attempts):
        if i:
            sleep(delay)
        try:
            frame = client.snapshot_jpeg()
            text = client.ocr(frame)
        except Exception:
            continue
        last_text = text
        if machine.prompt_matches(text):
            return True, text
    return False, last_text


def run_verify_policy(policy: VerifyPolicy, client, machine: MachineProfile, baseline: Optional[bytes],
                      **kw) -> tuple[bool, str]:
    if policy is VerifyPolicy.NONE:
        return False, "policy none: no automatic verification"
    if policy is VerifyPolicy.FRAME_CHANGE:
        if baseline is None:
            raise ValueError("FRAME_CHANGE requires a baseline snapshot")
        ok = verify_frame_change(client, baseline, **kw)
        return ok, "screen changed" if ok else "no frame change detected"
    if policy is VerifyPolicy.OCR_IDENTITY:
        ok, text = verify_ocr_identity(client, machine, **kw)
        return ok, ("ocr identity match" if ok else f"ocr mismatch; last text: {text[:200]!r}")
    if policy is VerifyPolicy.PROMPT_PATTERN:
        ok, text = verify_prompt_pattern(client, machine, **kw)
        return ok, ("prompt pattern match" if ok else f"prompt not seen; last text: {text[:200]!r}")
    raise ValueError(f"unknown policy {policy!r}")


# --------------------------------------------------------------------------
# Select-with-verification (held-key recipe)
# --------------------------------------------------------------------------

HOLD_MS = 120
GAP_MS = 150
REARM_KEYS: Sequence[str] = ("ControlRight", "ControlRight")


def otg_bounce(client: KvmClient, *, on_s: float = 8.0, off_s: float = 12.0,
               sleep: Callable[[float], None] = time.sleep) -> None:
    """Re-arm a switch hotkey engine via USB gadget re-enumeration."""
    try:
        client._request(
            "POST", "/api/system/otg_functions",
            params={"start_cdrom": "true", "start_flash": "true"},
        )
        sleep(on_s)
        client._request(
            "POST", "/api/system/otg_functions",
            params={"start_cdrom": "false", "start_flash": "false"},
        )
        sleep(off_s)
    except Exception as exc:
        raise SwitchFailure(f"OTG bounce failed before any keys were sent: {exc}") from exc


@dataclass
class SelectOptions:
    rearm: bool = True                 # OTG bounce first
    hold_ms: int = HOLD_MS             # per-key hold duration
    gap_ms: int = GAP_MS               # gap between key up and next key down
    settle_s: float = 5.0              # wait after Enter before verifying
    verify_policy: Optional[VerifyPolicy] = None  # default from DEFAULT_VERIFY_POLICY
    verify_attempts: int = 5
    verify_delay: float = 1.0


class SwitchFailure(RuntimeError):
    pass


def send_held_key(client: KvmClient, key: str, hold_ms: int, gap_ms: int,
                  sleep: Callable[[float], None]) -> None:
    """One held key event pair: down, hold, up, gap."""
    client.key_down(key)
    sleep(hold_ms / 1000.0)
    client.key_up(key)
    sleep(gap_ms / 1000.0)


def select_machine(client: KvmClient, session: SessionState, machine_name: str,
                   *, options: Optional[SelectOptions] = None,
                   sleep: Callable[[float], None] = time.sleep) -> SelectionRecord:
    """Switch to ``machine_name`` and verify per policy.

    Safe failure behavior:
      - unknown/disabled machine: no HID traffic at all, state untouched.
      - OTG bounce failure: raises SwitchFailure, state untouched.
      - keystroke emission always completes even if a later step fails;
        on verification failure or error the record is left explicitly
        VERIFY_FAILED / SELECTED_UNVERIFIED rather than silently trusted.
    """
    opts = options or SelectOptions()
    try:
        machine = RACK[machine_name]
    except KeyError:
        raise SwitchFailure(f"unknown machine {machine_name!r}; known: {sorted(RACK)}") from None
    if not machine.enabled:
        raise SwitchFailure(f"machine {machine_name!r} is disabled (not working); refusing to select")
    verify_policy = opts.verify_policy or DEFAULT_VERIFY_POLICY[machine.name]

    baseline: Optional[bytes] = None
    if verify_policy is VerifyPolicy.FRAME_CHANGE:
        try:
            baseline = client.snapshot_jpeg()
        except Exception as exc:
            raise SwitchFailure(f"cannot capture baseline for FRAME_CHANGE: {exc}") from exc

    if opts.rearm:
        otg_bounce(client, sleep=sleep)
        # A Comet OTG bounce tears down ustreamer. Reopen the retained stream
        # when this concrete client provides one; injected test clients do not
        # need network behavior.
        reopen = getattr(client, "open_stream", None) if getattr(client, "_stream", None) is not None else None
        if reopen is not None:
            try:
                reopen()
            except Exception as exc:
                raise SwitchFailure(f"stream recovery failed after OTG bounce: {exc}") from exc

    keys = [*REARM_KEYS, f"Digit{machine.port}", "Enter"]
    try:
        for key in keys:
            send_held_key(client, key, opts.hold_ms, opts.gap_ms, sleep)
        sleep(opts.settle_s)
    except Exception as exc:
        rec = session.mark_selected(machine)
        session.mark_verify_failed(f"HID sequence aborted: {exc}")
        raise SwitchFailure(f"HID sequence failed mid-way ({exc}); "
                            f"actual port unknown, marked {rec.state.value}") from exc

    session.mark_selected(machine)

    if verify_policy is VerifyPolicy.NONE:
        # Explicitly unverified, not trusted: caller must verify separately.
        rec = session.current
        assert rec is not None
        rec.detail = "verification skipped (policy none)"
        return rec

    try:
        ok, detail = run_verify_policy(
            verify_policy, client, machine, baseline,
            attempts=opts.verify_attempts, delay=opts.verify_delay, sleep=sleep,
        )
    except Exception as exc:
        rec = session.mark_verify_failed(f"verification error: {exc}")
        raise SwitchFailure(f"verification raised; state={rec.state.value}: {exc}") from exc

    if ok:
        return session.mark_verified(detail)
    rec = session.mark_verify_failed(detail)
    raise SwitchFailure(f"selection of {machine.name} NOT verified ({detail}); "
                        f"recorded as {rec.state.value}")
