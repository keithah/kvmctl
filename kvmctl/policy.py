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
from typing import Callable, Optional, Sequence


class PolicyError(RuntimeError):
    """An operation was refused by the safety policy."""


TRANSPORTS = ("kvm", "ssh")

READ_ONLY_OPERATIONS = frozenset({
    "capabilities", "snapshot", "ocr", "verify", "kvm_sequence_plan",
    "kvm_workflow_list", "kvm_workflow_inspect",
})

WRITE_OPERATIONS = frozenset({
    "select", "hid_reset", "exec_command", "rearm_otg", "host.reboot",
    "kvm_send_text", "kvm_send_keys", "kvm_hold_key", "kvm_release_all",
    "kvm_mouse_move", "kvm_mouse_move_pct", "kvm_mouse_click", "kvm_mouse_scroll",
    "kvm_ocr_click",
    "kvm_sequence_authorize", "kvm_sequence_execute", "kvm_workflow_authorize", "kvm_workflow_execute",
})


def parse_allowlisted_command(command: str, allowlist: tuple[str, ...]) -> tuple[list[str], str]:
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
    return words, base


def validate_command(command: str, allowlist: tuple[str, ...]) -> str:
    """Return the allowlisted base command, preserving the public API."""
    return parse_allowlisted_command(command, allowlist)[1]


class TransportPolicy:
    """Explicit transport + write gate holder."""

    def __init__(
        self,
        *,
        write_enabled: bool = False,
        ssh_allowlist: tuple[str, ...] = (),
        ssh_runner: Optional[Callable[[Sequence[str]], dict]] = None,
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
        words, base = parse_allowlisted_command(command, self.ssh_allowlist)
        # The runner receives argv, not a shell string. Implementations should
        # invoke SSH/subprocess with shell=False (or equivalent).
        result = self.ssh_runner(words)
        return {"rc": result.get("rc"), "stdout": result.get("stdout", ""),
                "stderr": result.get("stderr", ""), "command_base": base}
