"""kvmctl command-line interface.

Read-only by default. Mutating commands require ``--yes`` and, for
``select``, an explicit ``--transport kvm``. Output is a single JSON
evidence document on stdout.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from kvmctl.client import KvmClient
from kvmctl.machines import SessionState
from kvmctl.policy import PolicyError
from kvmctl.semantics import SemanticSurface


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kvmctl",
        description="Semantic KVM control (read-only unless --yes).",
    )
    p.add_argument("--url", required=True, help="KVMD base URL")
    p.add_argument("--token", default=None, help="auth token (else KVMCTL_TOKEN env)")
    p.add_argument("--insecure", action="store_true", help="disable TLS verification")
    p.add_argument("--ca-bundle", default=None)
    p.add_argument("--yes", action="store_true",
                   help="authorize state-changing operations (required gate)")
    p.add_argument("--transport", choices=("kvm", "ssh"), default=None,
                   help="explicit transport; required for select/exec-command")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("capabilities")

    sp = sub.add_parser("snapshot")
    sp.add_argument("--out", required=True, help="write JPEG to this path")

    op = sub.add_parser("ocr")
    op.add_argument("--image", default=None, help="image file (default: snapshot)")

    vp = sub.add_parser("verify")
    vp.add_argument("machine")
    vp.add_argument("--policy", default=None)

    sel = sub.add_parser("select")
    sel.add_argument("machine")
    sel.add_argument("--verify-policy", default=None)
    sel.add_argument("--no-rearm", action="store_true")
    sel.add_argument("--settle", type=float, default=5.0)

    sub.add_parser("hid-reset")
    sub.add_parser("rearm-otg")

    ex = sub.add_parser("exec-command")
    ex.add_argument("cmd")
    return p


def _verify_arg(v: bool | str) -> bool | str:
    return v


def main(argv: Optional[list] = None, *, client: Optional[KvmClient] = None) -> int:
    args = build_parser().parse_args(argv)
    if client is None:
        verify: bool | str = True
        if args.insecure:
            verify = False
        elif args.ca_bundle:
            verify = args.ca_bundle
        client = KvmClient(args.url, verify=verify)
        token = args.token or __import__("os").environ.get("KVMCTL_TOKEN")
        if not token:
            print(json.dumps({"ok": False, "error": "no token (--token or KVMCTL_TOKEN)"}))
            return 2
        client.set_token(token)

    surf = SemanticSurface(client, session=SessionState())
    surf.write_enabled = args.yes
    sleep = _fast_sleep if client._transport is not None else _real_sleep

    def need_write():
        if not args.yes:
            raise SystemExit(
                f"'{args.command}' changes device state; re-run with --yes to authorize"
            )

    def need_transport(op):
        if args.transport is None:
            raise SystemExit(f"'{op}' requires an explicit --transport (kvm|ssh)")

    try:
        if args.command == "capabilities":
            out = surf.capabilities()
        elif args.command == "snapshot":
            out = surf.snapshot(path=args.out)  # read-only: no --yes gate
        elif args.command == "ocr":
            data = open(args.image, "rb").read() if args.image else None
            out = surf.ocr(data)
        elif args.command == "verify":
            out = surf.verify(args.machine, policy=args.policy, sleep=sleep)
        elif args.command == "select":
            need_write()
            need_transport("select")
            out = surf.select(args.machine, verify_policy=args.verify_policy,
                              rearm=not args.no_rearm, settle_s=args.settle,
                              sleep=_real_sleep if client._transport is None else _no_sleep_select)
        elif args.command == "hid-reset":
            need_write()
            out = surf.hid_reset()
        elif args.command == "rearm-otg":
            need_write()
            out = surf.rearm_otg()
        elif args.command == "exec-command":
            need_write()
            need_transport("exec-command")
            out = surf.exec_command(args.cmd, transport=args.transport)
        else:  # pragma: no cover
            raise SystemExit(f"unknown command {args.command!r}")
    except PolicyError as exc:
        print(json.dumps({"ok": False, "error": f"policy refused: {exc}"}))
        return 3
    except SystemExit as exc:
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            raise SystemExit(2)
        raise
    print(json.dumps(out))
    return 0 if out.get("ok", True) else 1


# sleep strategies -----------------------------------------------------------

def _real_sleep(s):
    import time
    time.sleep(s)


def _fast_sleep(s):
    pass  # tests inject a mock transport; don't actually wait


def _no_sleep_select(s):
    pass


def _exec_cmd_of(args):
    # exec-command positional stored under dest "cmd"? argparse maps it to
    # the subparser attribute name we set below.
    return args.cmd
