"""Safety-bounded, named host inspection probes.

The runner receives argv sequences only; this module never evaluates a shell
command and does not accept caller-supplied command arguments.
"""
from __future__ import annotations

import re
from typing import Callable, Protocol, Sequence, cast


class ProbeError(ValueError):
    """Raised when a named probe cannot produce trustworthy evidence."""


class ArgvRunner(Protocol):
    def __call__(self, argv: Sequence[str]) -> str | bytes | tuple[int, str | bytes]: ...


_HOSTNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
_DRM_NODE = re.compile(r"^(?:card\d+|renderD\d+|controlD\d+)$")
_PCI = re.compile(
    r"^(?P<address>[0-9a-fA-F:.]+) (?P<class>[^\[]+) \[[0-9a-fA-F]{4}\]: (?P<description>.+?) \[[0-9a-fA-F]{4}:[0-9a-fA-F]{4}\]$"
)
_PCI_LOOKING = re.compile(r"^[0-9a-fA-F:.]+\s+[^[]+\[[0-9a-fA-F]{4}\]:")


def _output(runner: ArgvRunner, argv: tuple[str, ...], limit: int) -> str:
    try:
        value = runner(argv)
    except Exception as exc:
        raise ProbeError(f"probe command failed: {argv[0]}") from exc
    if isinstance(value, tuple):
        if len(value) != 2 or not isinstance(value[0], int) or isinstance(value[0], bool):
            raise ProbeError("malformed probe output: invalid runner result")
        value = value[1]
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


def _identity(runner: ArgvRunner, limit: int) -> dict:
    hostname = _output(runner, ("hostname",), limit).strip()
    release = _output(runner, ("cat", "/etc/os-release"), limit)
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


def _graphics(runner: ArgvRunner, limit: int) -> dict:
    pci = _output(runner, ("lspci", "-nnk"), limit)
    drm = _output(runner, ("find", "/dev/dri", "-maxdepth", "1", "-type", "c", "-printf", "%f\\n"), limit)
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
        elif current and line.strip().startswith("Kernel driver in use:"):
            driver = line.split(":", 1)[1].strip()
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", driver):
                raise ProbeError("malformed graphics output")
            current["driver"] = driver
        elif line.strip() and not line.startswith(("\t", " ")):
            current = None
    nodes = [line.strip() for line in drm.splitlines() if line.strip()]
    if any(not _DRM_NODE.fullmatch(node) for node in nodes):
        raise ProbeError("malformed graphics output")
    return {"probe": "host.graphics.inspect", "devices": devices, "drm_nodes": nodes}


def _status(runner: ArgvRunner, argv: tuple[str, ...], limit: int, *, success_codes: set[int], failure_codes: set[int], legacy_values: set[str]) -> bool:
    try:
        result = runner(argv)
    except Exception as exc:
        raise ProbeError(f"probe command failed: {argv[0]}") from exc
    if isinstance(result, tuple):
        if len(result) != 2 or not isinstance(result[0], int) or isinstance(result[0], bool):
            raise ProbeError("malformed render access output")
        output = _output(cast(ArgvRunner, lambda _argv: result[1]), argv, limit)
        if output.strip():
            raise ProbeError("malformed render access output")
        if result[0] not in success_codes and result[0] not in failure_codes:
            raise ProbeError("malformed render access output")
        return result[0] in success_codes
    value = _output(cast(ArgvRunner, lambda _argv: result), argv, limit).strip()
    if value not in legacy_values:
        raise ProbeError("malformed render access output")
    return value in {"active", "readable", "writable"}


def _render_access(runner: ArgvRunner, limit: int) -> dict:
    active = _status(runner, ("systemctl", "is-active", "--quiet", "kvm-render"), limit,
                     success_codes={0}, failure_codes={3}, legacy_values={"active", "inactive"})
    readable = _status(runner, ("test", "-r", "/dev/dri/renderD128"), limit,
                       success_codes={0}, failure_codes={1}, legacy_values={"readable", "not-readable"})
    writable = _status(runner, ("test", "-w", "/dev/dri/renderD128"), limit,
                       success_codes={0}, failure_codes={1}, legacy_values={"writable", "not-writable"})
    return {"probe": "service.render_access.inspect", "service": "kvm-render",
            "active": active, "node": "/dev/dri/renderD128",
            "readable": readable, "writable": writable}


_PROBES = {"host.identity.inspect": _identity, "host.graphics.inspect": _graphics,
           "service.render_access.inspect": _render_access}


def run_probe(name: str, runner: ArgvRunner, *, max_output_bytes: int = 65536) -> dict:
    if name not in _PROBES:
        raise ProbeError(f"unknown probe: {name}")
    if not isinstance(max_output_bytes, int) or max_output_bytes <= 0:
        raise ProbeError("invalid output bound")
    return _PROBES[name](runner, max_output_bytes)


probe = run_probe
NAMED_PROBES = frozenset(_PROBES)
