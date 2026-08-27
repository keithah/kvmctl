"""Transport selection and read/write policy for the semantic surface.

Rules:
  - read-only is the default; mutating operations require an explicit
    ``write_enabled=True`` gate.
  - every operation names its transport explicitly: ``kvm`` (KVMD HTTP API)
    or ``ssh`` (gated command execution). There is no default transport for
    execution: callers must pass ``transport="ssh"`` and the command must be
    on the allowlist.
"""
from __future__ import annotations

import shlex
from typing import Callable, Optional


class PolicyError(RuntimeError):
    """An operation was refused by the safety policy."""


TRANSPORTS = ("kvm", "ssh")

READ_ONLY_OPERATIONS = frozenset({
    "capabilities", "snapshot", "ocr", "verify",
})

WRITE_OPERATIONS = frozenset({
    "select", "hid_reset", "exec_command", "rearm_otg",
})


def validate_command(command: str, allowlist: tuple[str, ...]) -> str:
    """Return the allowlisted base command, or raise PolicyError.

    Matching is on the first shell word (argv[0]) so ``uptime -p`` matches
    allowlist entry ``uptime`` but ``rm -rf /`` never matches anything.
    """
    try:
        words = shlex.split(command)
    except ValueError as exc:
        raise PolicyError(f"unparseable command: {exc}") from exc
    if not words:
        raise PolicyError("empty command")
    # The runner historically accepts a shell string.  Reject shell syntax
    # before it reaches that runner; checking argv[0] alone is not sufficient
    # because `uptime; rm -rf /` still has argv[0] == "uptime".
    if any(ch in command for ch in ";|&$`()<>\n\r"):
        raise PolicyError("shell operators and command substitution are not allowed")
    base = words[0]
    if base not in allowlist:
        raise PolicyError(
            f"command {base!r} not in SSH allowlist {sorted(allowlist)}"
        )
    return base


class TransportPolicy:
    """Explicit transport + write gate holder."""

    def __init__(
        self,
        *,
        write_enabled: bool = False,
        ssh_allowlist: tuple[str, ...] = (),
        ssh_runner: Optional[Callable[[str], dict]] = None,
    ):
        self.write_enabled = write_enabled
        self.ssh_allowlist = tuple(ssh_allowlist)
        self.ssh_runner = ssh_runner

    def require_write(self, operation: str) -> None:
        if operation not in WRITE_OPERATIONS:
            return  # read-only op, always allowed
        if not self.write_enabled:
            raise PolicyError(
                f"policy refused: operation {operation!r} mutates device state; "
                f"set write_enabled=True to authorize it"
            )

    def run_ssh(self, command: str) -> dict:
        if self.ssh_runner is None:
            raise PolicyError("no SSH runner configured")
        base = validate_command(command, self.ssh_allowlist)
        result = self.ssh_runner(command)
        return {"rc": result.get("rc"), "stdout": result.get("stdout", ""),
                "stderr": result.get("stderr", ""), "command_base": base}
