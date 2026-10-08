"""laser: command line for the focus guard. The bar panel does the same things with clicks."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import socket
import sys
import time

from laser import config as cfg

DOTS = {"focused": "🟢", "distracted": "🔴", "unsure": "🟡", "checking": "⚪", "neutral": "⚪",
        "break": "☕", "away": "💤", "idle": "⚫"}


def call(req: dict, quiet: bool = False) -> dict:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(15)
            sock.connect(str(cfg.SOCKET_FILE))
            sock.sendall(json.dumps(req).encode() + b"\n")
            data = b""
            while not data.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
    except (FileNotFoundError, ConnectionRefusedError):
        sys.exit("Laser isn't running. It starts with the Omarchy shell (bar plugin yasserdo.laser),\n"
                 "or run it by hand: laser daemon")
    reply = json.loads(data or b"{}")
    if reply.get("error") and not quiet:
        sys.exit(reply["error"])
    return reply


def fmt(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    m = int(seconds // 60)
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m {int(seconds % 60):02d}s"


def money(usd: float) -> str:
    return f"${usd:.4f}" if usd < 1 else f"${usd:.2f}"


def cmd_status(args) -> None:
    st = call({"cmd": "status"})
    if args.json:
        print(json.dumps(st, indent=2))
        return
    if not st["active"]:
        print(f"{DOTS['idle']} No focus session.  laser start \"<task>\"")
    else:
        print(f"{DOTS.get(st['status'], '⚪')} {st['status']}  ·  {st['task']}")
        if st.get("label"):
            p = st.get("p_on_task")
            print(f"   now: {st['label']}" + (f"  (on-task {p:.0%}, {st['category']})" if p is not None else ""))
        ratio = st.get("focus_ratio")
        print(f"   {fmt(st['elapsed_s'])} elapsed · focused {fmt(st['focused_s'])} · distracted {fmt(st['distracted_s'])}"
              + (f" · {ratio:.0%} focus" if ratio is not None else ""))
        print(f"   level: {st['level']} · nudges {st['nudges']} · closed {st['blocks']}"
              f" · {st.get('api_calls', 0)} checks ({money(st.get('cost_usd', 0))})")
        if st.get("remaining_s") is not None:
            print(f"   ends in {fmt(st['remaining_s'])}")
        if st.get("break_remaining_s") is not None:
            print(f"   on break for {fmt(st['break_remaining_s'])}")
    s = st["settings"]
    print(f"   privacy: {s['privacy']} · strictness: {s['strictness']} · look: {s.get('look')} · today {money(st['usage']['today']['cost_usd'])}")
    if st.get("error"):
        print(f"⚠  {st['error']}")


def cmd_start(args) -> None:
    # "-" reads the task from stdin, so it never shows up in a process list (the bar does this).
    task = sys.stdin.readline().strip() if args.task == ["-"] else " ".join(args.task).strip()
    call({"cmd": "start", "task": task, "minutes": args.minutes})
    print(f"🟢 Focusing on: {task}" + (f" for {args.minutes:g} min" if args.minutes else ""))


def cmd_stop(args) -> None:
    s = call({"cmd": "stop"})["summary"]
    ratio = s["focus_ratio"]
    print(f"Session over: {s['task']}")
    print(f"   {fmt(s['duration_s'])} total · focused {fmt(s['focused_s'])} · distracted {fmt(s['distracted_s'])}"
          + (f" · {ratio:.0%} focus" if ratio is not None else ""))
    for label, secs in s["top_distractions"]:
        print(f"   - {label}: {fmt(secs)}")
    print(f"   {s.get('nudges', 0)} nudges · {s.get('blocks', 0)} closed · "
          f"{s.get('api_calls', 0)} Jev checks ({money(s.get('cost_usd', 0))})")


def cmd_pause(args) -> None:
    call({"cmd": "pause", "minutes": args.minutes})
    print(f"☕ Break for {args.minutes:g} min")


def cmd_resume(args) -> None:
    call({"cmd": "resume"})
    print("🟢 Back to work")


def cmd_allow(args) -> None:
    print(f"✓ {call({'cmd': 'allow'})['allowed']} now counts as on-task for this session")


def cmd_history(args) -> None:
    if not cfg.HISTORY_FILE.exists():
        print("No sessions yet.")
        return
    rows = [json.loads(line) for line in cfg.HISTORY_FILE.read_text().splitlines() if line.strip()][-args.n:]
    for s in rows:
        ratio = s["focus_ratio"]
        when = time.strftime("%a %d %b %H:%M", time.localtime(s["started"]))
        bar = "█" * round((ratio or 0) * 20)
        print(f"{when}  {fmt(s['duration_s']):>8}  {bar:<20} {'' if ratio is None else f'{ratio:.0%}':>4}  {s['task']}")


def cmd_usage(args) -> None:
    rep = call({"cmd": "usage"})
    price = rep["price_per_million"] / 1e6
    days = sorted(rep["days"].items())[-args.days:]
    if not days:
        print("No Jev calls yet.")
        return
    print(f"{'day':<12}{'checks':>8}{'tokens':>10}{'cost':>10}")
    for day, d in days:
        print(f"{day:<12}{d['calls']:>8}{d['tokens']:>10}{money(d['tokens'] * price):>10}")
    calls, tokens = sum(d["calls"] for _, d in days), sum(d["tokens"] for _, d in days)
    print(f"{'total':<12}{calls:>8}{tokens:>10}{money(tokens * price):>10}")
    if calls:
        print(f"\n~{tokens // calls} tokens per check at ${rep['price_per_million']:.3f} per million input tokens "
              "(output is free).")


def cmd_audit(args) -> None:
    """Exactly what was sent to Jev, most recent last. Nothing else ever leaves the machine."""
    if not cfg.AUDIT_FILE.exists():
        print("Nothing has been sent yet.")
        return
    for line in cfg.AUDIT_FILE.read_text().splitlines()[-args.n:]:
        entry = json.loads(line)
        when = time.strftime("%H:%M:%S", time.localtime(entry["at"]))
        print(f"{when}  [{entry['privacy']}]  {entry['tokens']} tokens  →  on-task {entry['p_on_task']:.0%}")
        print("   " + json.dumps(entry["state"], ensure_ascii=False))


def cmd_set(args) -> None:
    rep = call({"cmd": "set", args.key: args.value})
    print(f"✓ {args.key} = {rep['settings'][args.key]}")


def cmd_key(args) -> None:
    key = sys.stdin.readline() if not sys.stdin.isatty() else getpass.getpass("TypeSafe API key (hidden): ")
    rep = call({"cmd": "set_key", "key": key.strip()})
    print("✓ Key saved to ~/.config/laser/env (mode 600)" + (f". {rep['warning']}" if rep.get("warning") else " and verified."))


def cmd_daemon(args) -> None:
    from laser import daemon

    daemon.run()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="laser", description="Always-on focus guard for Omarchy, classified by Jev.")
    sub = p.add_subparsers(dest="command")

    s = sub.add_parser("start", help="start a focus session")
    s.add_argument("task", nargs="+", help="what you're working on, in plain words (or - to read it from stdin)")
    s.add_argument("-m", "--minutes", type=float, help="end automatically after this long")
    s.set_defaults(func=cmd_start)
    sub.add_parser("stop", help="end the session and show a summary").set_defaults(func=cmd_stop)
    s = sub.add_parser("pause", help="take a break without being judged")
    s.add_argument("minutes", nargs="?", type=float, default=5)
    s.set_defaults(func=cmd_pause)
    sub.add_parser("resume", help="end a break early").set_defaults(func=cmd_resume)
    sub.add_parser("allow", help="the last flagged site/app is on-task for this session").set_defaults(func=cmd_allow)
    s = sub.add_parser("status", help="show the current session")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_status)
    s = sub.add_parser("history", help="past sessions")
    s.add_argument("-n", type=int, default=15)
    s.set_defaults(func=cmd_history)
    s = sub.add_parser("usage", help="Jev checks, tokens and cost per day")
    s.add_argument("--days", type=int, default=31)
    s.set_defaults(func=cmd_usage)
    s = sub.add_parser("audit", help="show exactly what was sent to Jev")
    s.add_argument("-n", type=int, default=10)
    s.set_defaults(func=cmd_audit)
    s = sub.add_parser("privacy", help="strict | balanced | full")
    s.add_argument("value", choices=["strict", "balanced", "full"])
    s.set_defaults(func=cmd_set, key="privacy")
    s = sub.add_parser("strictness", help="gentle | strict | warn_only")
    s.add_argument("value", choices=["gentle", "strict", "warn_only"])
    s.set_defaults(func=cmd_set, key="strictness")
    s = sub.add_parser("final", help="at the limit: close the tab, or fog it and never close")
    s.add_argument("value", choices=["close", "fog"])
    s.set_defaults(func=cmd_set, key="final_step")
    s = sub.add_parser("look", help="the locked-in screen: full | subtle | off")
    s.add_argument("value", choices=["full", "subtle", "off"])
    s.set_defaults(func=cmd_set, key="look")
    sub.add_parser("key", help="set your TypeSafe API key (reads stdin or prompts)").set_defaults(func=cmd_key)
    sub.add_parser("daemon", help="run the watcher (the bar plugin does this for you)").set_defaults(func=cmd_daemon)

    os.umask(0o077)  # anything the CLI writes (the key, settings) is for this user only
    args = p.parse_args(argv)
    if not args.command:
        args = p.parse_args(["status"])
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
