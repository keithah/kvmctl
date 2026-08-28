"""kvmctl command-line interface.

Read-only by default. Mutating commands require ``--yes`` and, for
``select``, an explicit ``--transport kvm``. Output is a single JSON
evidence document on stdout.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Callable, Optional

from kvmctl.client import KvmClient
from kvmctl.machines import SessionState
from kvmctl.policy import PolicyError
from kvmctl.semantics import SemanticSurface
from kvmctl.host import ArgvRunner


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kvmctl",
        description="Semantic KVM control (read-only unless --yes).",
    )
    p.add_argument("--url", required=True, help="KVMD base URL")
    p.add_argument("--host", default=None, help="HTTP Host / virtual host header")
    p.add_argument("--token", default=None, help="auth token (else KVMCTL_TOKEN env)")
    p.add_argument("--user", default=None, help="login user (else KVMCTL_USER env)")
    p.add_argument("--password", default=None, help="login password (else KVMCTL_PASSWORD env)")
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

    sub.add_parser("host-identity-inspect")
    sub.add_parser("host-graphics-inspect")
    sub.add_parser("service-render-access-inspect")
    rb = sub.add_parser("host-reboot")
    rb.add_argument("target")
    rb.add_argument("--confirmation", required=True,
                     help="confirmation token bound to this target and host.reboot")

    sel = sub.add_parser("select")
    sel.add_argument("machine")
    sel.add_argument("--verify-policy", default=None)
    sel.add_argument("--no-rearm", action="store_true")
    sel.add_argument("--settle", type=float, default=5.0)

    sub.add_parser("hid-reset")
    sub.add_parser("rearm-otg")

    kt = sub.add_parser("send-text", help="type text through the selected target")
    kt.add_argument("text")
    kt.add_argument("--interval", type=float, default=0.01)
    kk = sub.add_parser("send-keys", help="send a key chord through the selected target")
    kk.add_argument("combo")
    hk = sub.add_parser("hold-key", help="hold one key, then release it")
    hk.add_argument("key")
    hk.add_argument("duration_ms", type=int)
    sub.add_parser("release-all")
    mm = sub.add_parser("mouse-move")
    mm.add_argument("x", type=int)
    mm.add_argument("y", type=int)
    mp = sub.add_parser("mouse-move-pct")
    mp.add_argument("x_pct", type=float)
    mp.add_argument("y_pct", type=float)
    mc = sub.add_parser("mouse-click")
    mc.add_argument("--button", default="left")
    mc.add_argument("--count", type=int, default=1)
    ms = sub.add_parser("mouse-scroll")
    ms.add_argument("--dx", type=int, default=0)
    ms.add_argument("--dy", type=int, default=0)
    osr = sub.add_parser("ocr-screenshot")
    osr.add_argument("--search-text", default="")
    oc = sub.add_parser("ocr-click")
    oc.add_argument("text")
    oc.add_argument("--button", default="left")
    oc.add_argument("--count", type=int, default=1)

    ex = sub.add_parser("exec-command")
    ex.add_argument("cmd")
    return p


def main(argv: Optional[list] = None, *, client: Optional[KvmClient] = None,
         sleep: Optional[Callable[[float], None]] = None,
         host_runner: Optional[ArgvRunner] = None) -> int:
    args = build_parser().parse_args(argv)
    if client is None:
        verify: bool | str = True
        if args.insecure:
            verify = False
        elif args.ca_bundle:
            verify = args.ca_bundle
        client = KvmClient(args.url, verify=verify, host=args.host)
        env = __import__("os").environ
        token = args.token or env.get("KVMCTL_TOKEN")
        user = args.user or env.get("KVMCTL_USER")
        password = args.password or env.get("KVMCTL_PASSWORD")
        if token:
            client.set_token(token)
        elif user and password:
            client.login(user, password)
        else:
            print(json.dumps({"ok": False, "error": "provide --token/KVMCTL_TOKEN or --user+--password/KVMCTL_USER+KVMCTL_PASSWORD"}))
            return 2

    surf = SemanticSurface(client, session=SessionState(), host_runner=host_runner)
    surf.write_enabled = args.yes
    sleep = sleep or _real_sleep

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
        elif args.command == "host-identity-inspect":
            out = surf.host_identity_inspect()
        elif args.command == "host-graphics-inspect":
            out = surf.host_graphics_inspect()
        elif args.command == "service-render-access-inspect":
            out = surf.service_render_access_inspect()
        elif args.command == "host-reboot":
            need_write()
            out = surf.host_reboot(args.target, args.confirmation, sleep=sleep)
        elif args.command == "select":
            need_write()
            need_transport("select")
            if args.transport != "kvm":
                raise SystemExit("'select' requires --transport kvm")
            live_stream = None
            if client._transport is None and not args.no_rearm:
                live_stream = client.open_stream()
            try:
                out = surf.select(args.machine, verify_policy=args.verify_policy,
                                  rearm=not args.no_rearm, settle_s=args.settle,
                                  sleep=sleep)
            finally:
                if live_stream is not None:
                    client.close_stream()
        elif args.command == "hid-reset":
            need_write()
            out = surf.hid_reset()
        elif args.command == "rearm-otg":
            need_write()
            out = surf.rearm_otg()
        elif args.command == "send-text":
            need_write()
            out = surf.kvm_send_text(args.text, sleep=sleep, interval_s=args.interval)
        elif args.command == "send-keys":
            need_write()
            out = surf.kvm_send_keys(args.combo)
        elif args.command == "hold-key":
            need_write()
            out = surf.kvm_hold_key(args.key, args.duration_ms, sleep=sleep)
        elif args.command == "release-all":
            need_write()
            out = surf.kvm_release_all()
        elif args.command == "mouse-move":
            need_write()
            out = surf.kvm_mouse_move(args.x, args.y)
        elif args.command == "mouse-move-pct":
            need_write()
            out = surf.kvm_mouse_move_pct(args.x_pct, args.y_pct)
        elif args.command == "mouse-click":
            need_write()
            out = surf.kvm_mouse_click(args.button, args.count, sleep=sleep)
        elif args.command == "mouse-scroll":
            need_write()
            out = surf.kvm_mouse_scroll(args.dx, args.dy)
        elif args.command == "ocr-screenshot":
            out = surf.kvm_ocr_screenshot(args.search_text)
        elif args.command == "ocr-click":
            need_write()
            out = surf.kvm_ocr_click(args.text, args.button, args.count, sleep=sleep)
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


def _real_sleep(s):
    import time
    time.sleep(s)
