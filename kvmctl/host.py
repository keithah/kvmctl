"""Safety-bounded, named host inspection probes.

The runner receives argv sequences only; this module never evaluates a shell
command and does not accept caller-supplied command arguments.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import queue
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence, cast

from kvmctl.results import operation_result
from kvmctl.policy import PolicyError
from kvmctl.journal import Journal


class ProbeError(ValueError):
    """Raised when a named probe cannot produce trustworthy evidence."""


@dataclass(frozen=True)
class RunnerResult:
    return_code: int
    stdout: str | bytes


@dataclass(frozen=True)
class HostProbeProfile:
    service_name: str = "kvm-render"
    drm_node: str = "/dev/dri/renderD128"
    timeout_seconds: float = 10.0


HostProfile = HostProbeProfile


class ArgvRunner(Protocol):
    def __call__(self, argv: Sequence[str], *, timeout: float) -> RunnerResult | str | bytes | tuple[int, str | bytes]: ...


_HOSTNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
_DRM_NODE = re.compile(r"^(?:card\d+|renderD\d+|controlD\d+)$")
_PCI = re.compile(
    r"^(?P<address>[0-9a-fA-F:.]+) (?P<class>[^\[]+) \[[0-9a-fA-F]{4}\]: (?P<description>.+?) \[[0-9a-fA-F]{4}:[0-9a-fA-F]{4}\]$"
)
_PCI_LOOKING = re.compile(r"^[0-9a-fA-F:.]+\s+[^[]+\[[0-9a-fA-F]{4}\]:")


def _invoke(runner: ArgvRunner, argv: tuple[str, ...], timeout: float) -> RunnerResult:
    def call():
        try:
            accepts_timeout = "timeout" in inspect.signature(runner).parameters
        except (TypeError, ValueError):
            accepts_timeout = False
        return runner(argv, timeout=timeout) if accepts_timeout else runner(argv)  # type: ignore[call-arg]

    result_queue: queue.Queue[object] = queue.Queue(maxsize=1)
    thread = threading.Thread(target=lambda: _queue_call(call, result_queue), daemon=True)
    thread.start()
    try:
        value = result_queue.get(timeout=timeout)
    except queue.Empty as exc:
        raise ProbeError(f"probe command timed out: {argv[0]}") from exc
    if isinstance(value, Exception):
        raise ProbeError(f"probe command failed: {argv[0]}") from value
    if isinstance(value, RunnerResult):
        result = value
    elif isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], int) and not isinstance(value[0], bool):
        result = RunnerResult(value[0], value[1])
    else:
        result = RunnerResult(0, value)  # legacy text runner
    if not isinstance(result.return_code, int) or isinstance(result.return_code, bool):
        raise ProbeError("malformed probe output: invalid runner result")
    return result


def _queue_call(call, result_queue: queue.Queue[object]) -> None:
    try:
        result_queue.put(call())
    except Exception as exc:
        result_queue.put(exc)


def _output(runner: ArgvRunner, argv: tuple[str, ...], limit: int, timeout: float = 10.0) -> str:
    try:
        result = _invoke(runner, argv, timeout)
        if result.return_code != 0:
            raise ProbeError(f"probe command failed: {argv[0]}")
        value = result.stdout
    except ProbeError:
        raise
    if isinstance(value, bytes):
        if len(value) > limit:
            raise ProbeError("malformed probe output: output exceeds bound")
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProbeError("malformed probe output: invalid UTF-8") from exc
    elif isinstance(value, str):
        if len(value.encode("utf-8")) > limit:
            raise ProbeError("malformed probe output: output exceeds bound")
    else:
        raise ProbeError("malformed probe output: runner returned non-text")
    if "\x00" in value or any(ord(c) < 9 and c not in "\r" for c in value):
        raise ProbeError("malformed probe output: unsafe characters")
    if re.search(r"(?i)(?:password|passwd|token|secret|private[_ -]?key)\s*[=:]", value):
        raise ProbeError("malformed probe output: sensitive value")
    return value


def _identity(runner: ArgvRunner, limit: int, timeout: float = 10.0) -> dict:
    hostname = _output(runner, ("hostname",), limit, timeout).strip()
    release = _output(runner, ("cat", "/etc/os-release"), limit, timeout)
    if not _HOSTNAME.fullmatch(hostname):
        raise ProbeError("malformed identity output")
    fields: dict[str, str] = {}
    for line in release.splitlines():
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ProbeError("malformed identity output")
        fields[key] = value.strip().strip('"')
    required = ("NAME", "VERSION_ID", "PRETTY_NAME")
    if any(not fields.get(k) for k in required):
        raise ProbeError("malformed identity output")
    return {"probe": "host.identity.inspect", "hostname": hostname,
            "os": {"name": fields["NAME"], "version_id": fields["VERSION_ID"],
                   "pretty_name": fields["PRETTY_NAME"]}}


def _graphics(runner: ArgvRunner, limit: int, timeout: float = 10.0) -> dict:
    pci = _output(runner, ("lspci", "-nnk"), limit, timeout)
    drm = _output(runner, ("find", "/dev/dri", "-maxdepth", "1", "-type", "c", "-printf", "%f\\n"), limit, timeout)
    devices = []
    current = None
    for line in pci.splitlines():
        stripped = line.strip()
        if _PCI_LOOKING.match(stripped) and not _PCI.fullmatch(stripped):
            raise ProbeError("malformed graphics output")
        match = _PCI.fullmatch(stripped)
        if match:
            cls = match.group("class").strip()
            if cls not in {"VGA compatible controller", "3D controller", "Display controller"}:
                current = None
                continue
            current = {"address": match.group("address"), "class": cls,
                       "description": match.group("description").strip(), "driver": None}
            devices.append(current)
        elif current and line.startswith(("\t", " ")) and line.strip().startswith("Kernel driver in use:"):
            driver = line.split(":", 1)[1].strip()
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", driver):
                raise ProbeError("malformed graphics output")
            current["driver"] = driver
        elif line.strip():
            if not line.startswith(("\t", " ")):
                raise ProbeError("malformed graphics output")
            if not re.match(r"^\s*(?:Subsystem:|Flags:|Kernel modules:|IOMMU group |NUMA node:|Memory at |Capabilities:|Physical Slot:|Rev:|Latency:|Region )", line):
                raise ProbeError("malformed graphics output")
    nodes = [line.strip() for line in drm.splitlines() if line.strip()]
    if any(not _DRM_NODE.fullmatch(node) for node in nodes):
        raise ProbeError("malformed graphics output")
    return {"probe": "host.graphics.inspect", "devices": devices, "drm_nodes": nodes}


def _status(runner: ArgvRunner, argv: tuple[str, ...], limit: int, *, timeout: float = 10.0, success_codes: set[int], failure_codes: set[int], legacy_values: set[str]) -> bool:
    try:
        result = _invoke(runner, argv, timeout)
    except ProbeError:
        raise
    if result.stdout in ("", b""):
        if result.return_code not in success_codes and result.return_code not in failure_codes:
            raise ProbeError("malformed render access output")
        return result.return_code in success_codes
    if result.return_code != 0:
        raise ProbeError("malformed render access output")
    value = result.stdout
    if not isinstance(value, (str, bytes)):
        raise ProbeError("malformed render access output")
    try:
        value = value.decode() if isinstance(value, bytes) else value
    except UnicodeDecodeError as exc:
        raise ProbeError("malformed render access output") from exc
    value = value.strip()
    if value not in legacy_values:
        raise ProbeError("malformed render access output")
    return value in {"active", "readable", "writable"}


def _render_access(runner: ArgvRunner, limit: int, profile: HostProbeProfile, timeout: float) -> dict:
    service = profile.service_name
    node = profile.drm_node
    active = _status(runner, ("systemctl", "is-active", "--quiet", service), limit, timeout=timeout,
                     success_codes={0}, failure_codes={3}, legacy_values={"active", "inactive"})
    readable = _status(runner, ("test", "-r", node), limit, timeout=timeout,
                       success_codes={0}, failure_codes={1}, legacy_values={"readable", "not-readable"})
    writable = _status(runner, ("test", "-w", node), limit, timeout=timeout,
                       success_codes={0}, failure_codes={1}, legacy_values={"writable", "not-writable"})
    return {"probe": "service.render_access.inspect", "service": service,
            "active": active, "node": node,
            "readable": readable, "writable": writable}


_PROBES = {"host.identity.inspect": _identity, "host.graphics.inspect": _graphics,
           "service.render_access.inspect": _render_access}


def run_probe(name: str, runner: ArgvRunner, *, max_output_bytes: int = 65536,
              profile: HostProbeProfile | None = None) -> dict:
    if name not in _PROBES:
        raise ProbeError(f"unknown probe: {name}")
    if not isinstance(max_output_bytes, int) or max_output_bytes <= 0:
        raise ProbeError("invalid output bound")
    profile = profile or HostProbeProfile()
    if not isinstance(profile, HostProbeProfile) or not isinstance(profile.service_name, str) or not re.fullmatch(r"[A-Za-z0-9_.@-]+", profile.service_name):
        raise ProbeError("invalid host probe profile")
    if not isinstance(profile.drm_node, str) or not re.fullmatch(r"/dev/dri/(?:card\d+|renderD\d+|controlD\d+)", profile.drm_node):
        raise ProbeError("invalid host probe profile")
    if not isinstance(profile.timeout_seconds, (int, float)) or isinstance(profile.timeout_seconds, bool) or profile.timeout_seconds <= 0:
        raise ProbeError("invalid host probe profile")
    if name == "service.render_access.inspect":
        return _render_access(runner, max_output_bytes, profile, float(profile.timeout_seconds))
    return _PROBES[name](runner, max_output_bytes, float(profile.timeout_seconds))


probe = run_probe
NAMED_PROBES = frozenset(_PROBES)


def reboot_confirmation(target: str, operation: str = "host.reboot") -> str:
    """Return the normalized confirmation token for one reboot target."""
    plan = json.dumps({"operation": operation, "target": target},
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(plan.encode("utf-8")).hexdigest()


class HostRebootError(RuntimeError):
    """Internal lifecycle failure; callers receive stable result codes."""


class HostAdapter:
    """Named host operations over an argv-only runner."""

    def __init__(self, runner: ArgvRunner, *, max_output_bytes: int = 65536,
                 journal: Journal | None = None, profile: HostProbeProfile | None = None):
        self.runner = runner
        self.max_output_bytes = max_output_bytes
        self.journal = journal
        self.profile = profile or HostProbeProfile()

    def _checkpoint(self, transition: str, *, target: str,
                    **details: object) -> None:
        if self.journal is None:
            return
        try:
            record = {"operation": "host.reboot", "target": target,
                      "transition": transition, **details}
            checkpoint = getattr(self.journal, "checkpoint", None)
            if checkpoint is not None:
                checkpoint(**record)
            else:
                self.journal.append(record)
        except Exception:
            # Observability must never change the authorized operation's result.
            return

    def identity(self) -> dict:
        return run_probe("host.identity.inspect", self.runner,
                         max_output_bytes=self.max_output_bytes, profile=self.profile)

    def _ready_identity(self) -> dict:
        return self.identity()

    def reboot(self, target: str, confirmation: str, *, write_enabled: bool = False,
               attempts: int = 5, delay: float = 1.0,
               sleep: Callable[[float], None] = time.sleep) -> dict:
        if not write_enabled:
            raise PolicyError("policy refused: host.reboot requires write authorization")
        if confirmation != reboot_confirmation(target):
            raise PolicyError("host.reboot requires explicit confirmation bound to target and operation")
        if not isinstance(attempts, int) or attempts < 1:
            raise ValueError("attempts must be a positive integer")
        preflight = self.identity()
        self._checkpoint("preflight", target=target,
                         hostname=preflight.get("hostname"))
        if preflight.get("hostname") != target:
            return operation_result(operation="host.reboot", target=target,
                                    transport="host", read_only=False, ok=False,
                                    changed=False, state="mismatch",
                                    evidence={"preflight": {"hostname": preflight.get("hostname")}},
                                    error={"code": "host_identity_mismatch",
                                           "retryable": False, "requires_human": True})
        try:
            result = self.runner(("systemctl", "reboot"))
        except Exception:
            self._checkpoint("reboot_failed", target=target)
            return operation_result(operation="host.reboot", target=target,
                                    transport="host", read_only=False, ok=False,
                                    evidence={"preflight": {"hostname": target}},
                                    error={"code": "host_reboot_failed", "retryable": True,
                                           "requires_human": False})
        if isinstance(result, tuple) and result and result[0] != 0:
            self._checkpoint("reboot_failed", target=target,
                             return_code=result[0])
            return operation_result(operation="host.reboot", target=target,
                                    transport="host", read_only=False, ok=False,
                                    evidence={"preflight": {"hostname": target}},
                                    error={"code": "host_reboot_failed", "retryable": True,
                                           "requires_human": False})

        self._checkpoint("reboot_requested", target=target)
        disappeared = False
        returned = None
        for index in range(attempts):
            if index:
                sleep(delay)
            try:
                returned = self._ready_identity()
            except ProbeError:
                if not disappeared:
                    disappeared = True
                    self._checkpoint("disappeared", target=target)
                continue
            if disappeared:
                if returned.get("hostname") != target:
                    self._checkpoint("mismatch", target=target,
                                     hostname=returned.get("hostname"))
                    return operation_result(
                        operation="host.reboot", target=target, transport="host",
                        read_only=False, ok=False, changed=True, state="mismatch",
                        evidence={"preflight": {"hostname": target},
                                  "post_return": {"hostname": returned.get("hostname")}},
                        error={"code": "host_identity_mismatch", "retryable": False,
                               "requires_human": True})
                self._checkpoint("ready", target=target, hostname=target)
                return operation_result(
                    operation="host.reboot", target=target, transport="host",
                    read_only=False, ok=True, changed=True, state="ready",
                    evidence={"preflight": {"hostname": target}, "disappeared": True,
                              "post_return": {"hostname": target}})
        self._checkpoint("timeout", target=target, disappeared=disappeared)
        return operation_result(operation="host.reboot", target=target,
                                transport="host", read_only=False, ok=False,
                                changed=disappeared, state="timeout",
                                evidence={"preflight": {"hostname": target},
                                          "disappeared": disappeared},
                                error={"code": "host_reboot_timeout", "retryable": True,
                                       "requires_human": False})
