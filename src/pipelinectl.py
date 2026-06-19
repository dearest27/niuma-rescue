#!/usr/bin/env python3
"""Small operations CLI for agent-pipeline."""
from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path

import config as C
import health
import inbox
import lark
import message_router
import ops
import runs
import workspaces

SRC_DIR = Path(__file__).resolve().parent
PIPELINE_DIR = SRC_DIR.parent
LOG_DIR = PIPELINE_DIR / "logs"
PY = PIPELINE_DIR / ".venv" / "bin" / "python"
if not PY.exists():
    PY = Path(sys.executable)

SERVICES = ("listener", "dispatcher")


def _run(argv: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=str(PIPELINE_DIR), text=True, capture_output=True, check=check)


def _launchctl_service(service: str) -> str:
    return f"gui/{os.getuid()}/com.agentpipeline.{service}"


def _launchctl_plist(service: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"com.agentpipeline.{service}.plist"


def service_info(service: str) -> dict[str, str]:
    if platform.system() != "Darwin":
        return {"state": "unknown", "pid": ""}
    r = _run(["launchctl", "print", _launchctl_service(service)])
    if r.returncode != 0:
        return {"state": "not-loaded", "pid": "", "error": (r.stderr or r.stdout).strip()}
    info = {"state": "unknown", "pid": ""}
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith("state ="):
            info["state"] = line.split("=", 1)[1].strip()
        elif line.startswith("pid ="):
            info["pid"] = line.split("=", 1)[1].strip()
    return info


def cmd_status(_: argparse.Namespace) -> int:
    print("agent-pipeline status")
    print(f"  dir: {PIPELINE_DIR}")
    print()
    latest = health.read_all()
    for service in SERVICES:
        svc = service_info(service)
        item = latest.get(service) or {}
        print(f"{service}:")
        suffix = f" pid={svc.get('pid')}" if svc.get("pid") else ""
        print(f"  service: {svc.get('state', 'unknown')}{suffix}")
        if item:
            print(f"  health:  {item.get('event')} ({health.age_text(item.get('ts'))})")
            for key in ("chat_id", "text", "handled", "records", "actionable", "record_id", "status", "stage", "error"):
                if key in item:
                    value = str(item[key]).replace("\n", " ")
                    print(f"  {key}: {value[:180]}")
        else:
            print("  health:  no local heartbeat yet")
        print()
    events = health.tail_events(8)
    if events:
        print("recent events:")
        for e in events:
            msg = f"  {e.get('time')} {e.get('component')} {e.get('event')}"
            if e.get("record_id"):
                msg += f" record={e.get('record_id')}"
            if e.get("status"):
                msg += f" status={e.get('status')}"
            if e.get("error"):
                msg += f" error={str(e.get('error'))[:120]}"
            print(msg)
    return 0


def _fmt_time(ts: float | int | None) -> str:
    return health.age_text(ts)


def cmd_inbox(args: argparse.Namespace) -> int:
    counts = inbox.stats()
    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "empty"
    print(f"inbox: {summary}")
    rows = inbox.list_rows(status=args.status, limit=args.limit)
    for row in rows:
        msg = row["message"]
        text = str(msg.get("content") or "").replace("\n", " ")
        handled = row.get("handled")
        handled_text = "" if handled is None else f" handled={bool(handled)}"
        print(
            f"#{row['id']} {row['status']} attempts={row['attempts']}{handled_text} "
            f"updated={_fmt_time(row['updated_at'])} chat={msg.get('chat_id')} text={text[:120]}"
        )
        if row.get("last_error"):
            print(f"    error: {str(row['last_error'])[:180]}")
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    if args.id is not None:
        inbox.requeue(args.id)
    elif args.failed:
        for row in inbox.list_rows(status="failed", limit=args.limit):
            inbox.requeue(int(row["id"]))
    processed = inbox.process_pending(message_router.handle_message, lambda: None, limit=args.limit)
    print(f"replayed {processed} inbox message(s)")
    return 0


def cmd_workspaces(_: argparse.Namespace) -> int:
    for ws in workspaces.list_workspaces():
        marker = "*" if ws.key == workspaces.get(None).key else " "
        print(f"{marker} {ws.key}: path={ws.path} scm={ws.scm} base={ws.base_ref} test={ws.test_cmd or '-'}")
    return 0


def cmd_runs(args: argparse.Namespace) -> int:
    rows = runs.list_runs(limit=args.limit, state=args.state)
    if not rows:
        print("runs: empty")
        return 0
    for row in rows:
        retry = ""
        if row.get("next_retry_at"):
            retry = f" next_retry={_fmt_time(float(row['next_retry_at']))}"
        print(
            f"{row['state']} record={row['record_id']} stage={row['stage']} "
            f"status={row['status']} attempts={row['attempts']} run={row['run_id']}{retry}"
        )
        if row.get("title"):
            print(f"    title: {str(row['title'])[:160]}")
        if row.get("last_error"):
            print(f"    error: {str(row['last_error'])[:220]}")
    return 0


def cmd_run_events(args: argparse.Namespace) -> int:
    rows = runs.events(record_id=args.record_id, limit=args.limit)
    if not rows:
        print("run events: empty")
        return 0
    for row in rows:
        arrow = ""
        if row.get("status_from") or row.get("status_to"):
            arrow = f" {row.get('status_from') or '-'}->{row.get('status_to') or '-'}"
        print(
            f"#{row['id']} {health.age_text(row['created_at'])} "
            f"{row['event']} record={row['record_id']} stage={row['stage']}{arrow}"
        )
        if row.get("message"):
            print(f"    {str(row['message'])[:220]}")
    return 0


def _load_record(record_id: str) -> dict:
    rec = ops.find_by_record_id(lark.list_records(), record_id)
    if not rec:
        raise SystemExit(f"record not found: {record_id}")
    return rec


def _print_op_result(result: ops.OpResult) -> int:
    print(("ok: " if result.ok else "failed: ") + result.message)
    return 0 if result.ok else 1


def _dispatch_if_requested(args: argparse.Namespace, result: ops.OpResult) -> None:
    if getattr(args, "dispatch", False) and result.ok and result.dispatch:
        print("dispatch: running dispatcher --once")
        code = cmd_once(argparse.Namespace())
        if code != 0:
            raise SystemExit(code)


def cmd_retry_run(args: argparse.Namespace) -> int:
    result = ops.retry_record(_load_record(args.record_id))
    _dispatch_if_requested(args, result)
    return _print_op_result(result)


def cmd_clear_lock(args: argparse.Namespace) -> int:
    return _print_op_result(ops.clear_lock(_load_record(args.record_id)))


def cmd_unblock(args: argparse.Namespace) -> int:
    result = ops.unblock_record(_load_record(args.record_id), args.status)
    _dispatch_if_requested(args, result)
    return _print_op_result(result)


def cmd_mark_done(args: argparse.Namespace) -> int:
    return _print_op_result(ops.mark_done(_load_record(args.record_id)))


def cmd_set_status(args: argparse.Namespace) -> int:
    result = ops.set_status(_load_record(args.record_id), args.status)
    _dispatch_if_requested(args, result)
    return _print_op_result(result)


def cmd_set_agent(args: argparse.Namespace) -> int:
    return _print_op_result(ops.set_agent(_load_record(args.record_id), args.agent, stage=args.stage))


def cmd_set_workspace(args: argparse.Namespace) -> int:
    workspaces.get(args.workspace)
    return _print_op_result(ops.set_workspace(_load_record(args.record_id), args.workspace))


def cmd_logs(args: argparse.Namespace) -> int:
    targets = SERVICES if args.service == "all" else (args.service,)
    for service in targets:
        path = LOG_DIR / f"{service}.log"
        print(f"==> {path}")
        if not path.exists():
            print("(missing)")
            continue
        r = _run(["tail", "-n", str(args.lines), str(path)])
        print(r.stdout, end="")
        if r.stderr:
            print(r.stderr, file=sys.stderr, end="")
    return 0


def cmd_once(_: argparse.Namespace) -> int:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    r = subprocess.run([str(PY), "-B", str(SRC_DIR / "dispatcher.py"), "--once"], cwd=str(SRC_DIR), env=env)
    return r.returncode


def cmd_doctor(_: argparse.Namespace) -> int:
    r = subprocess.run([str(PY), "-B", str(SRC_DIR / "doctor.py")], cwd=str(SRC_DIR))
    return r.returncode


def _service_action(action: str) -> int:
    if platform.system() != "Darwin":
        print(f"{action} currently supports macOS launchd only; use your process manager on this OS.", file=sys.stderr)
        return 2
    code = 0
    for service in SERVICES:
        label = _launchctl_service(service)
        state = service_info(service).get("state")
        if action in {"start", "restart"} and state == "not-loaded":
            plist = _launchctl_plist(service)
            argv = ["launchctl", "load", "-w", str(plist)]
        elif action == "restart":
            argv = ["launchctl", "kickstart", "-k", label]
        elif action == "stop":
            argv = ["launchctl", "kill", "TERM", label]
        else:
            argv = ["launchctl", "kickstart", label]
        r = _run(argv)
        if r.returncode == 0:
            print(f"{service}: {action} ok")
        else:
            code = r.returncode
            print(f"{service}: {action} failed: {(r.stderr or r.stdout).strip()}", file=sys.stderr)
    return code


def cmd_restart(_: argparse.Namespace) -> int:
    return _service_action("restart")


def cmd_stop(_: argparse.Namespace) -> int:
    return _service_action("stop")


def cmd_start(_: argparse.Namespace) -> int:
    return _service_action("start")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Operate local agent-pipeline services")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("workspaces").set_defaults(func=cmd_workspaces)
    p_runs = sub.add_parser("runs")
    p_runs.add_argument("--state", choices=("processing", "failed", "done"))
    p_runs.add_argument("-n", "--limit", type=int, default=20)
    p_runs.set_defaults(func=cmd_runs)
    p_run_events = sub.add_parser("run-events")
    p_run_events.add_argument("record_id", nargs="?")
    p_run_events.add_argument("-n", "--limit", type=int, default=40)
    p_run_events.set_defaults(func=cmd_run_events)
    p_retry = sub.add_parser("retry-run")
    p_retry.add_argument("record_id")
    p_retry.add_argument("--dispatch", action="store_true", help="run dispatcher --once after recovery when actionable")
    p_retry.set_defaults(func=cmd_retry_run)
    p_clear = sub.add_parser("clear-lock")
    p_clear.add_argument("record_id")
    p_clear.set_defaults(func=cmd_clear_lock)
    p_unblock = sub.add_parser("unblock")
    p_unblock.add_argument("record_id")
    p_unblock.add_argument("--status", choices=sorted(C.ACTIONABLE), default=C.S_DEV)
    p_unblock.add_argument("--dispatch", action="store_true", help="run dispatcher --once after unblocking")
    p_unblock.set_defaults(func=cmd_unblock)
    p_done = sub.add_parser("mark-done")
    p_done.add_argument("record_id")
    p_done.set_defaults(func=cmd_mark_done)
    p_set_status = sub.add_parser("set-status")
    p_set_status.add_argument("record_id")
    p_set_status.add_argument("status", choices=(C.S_CLARIFY, C.S_ANSWER, C.S_CONFIRM, C.S_DEV, C.S_REVIEW, C.S_MERGE, C.S_DONE, C.S_BLOCKED))
    p_set_status.add_argument("--dispatch", action="store_true", help="run dispatcher --once when new status is actionable")
    p_set_status.set_defaults(func=cmd_set_status)
    p_set_agent = sub.add_parser("set-agent")
    p_set_agent.add_argument("record_id")
    p_set_agent.add_argument("agent", choices=sorted(C.AGENT_CMDS))
    p_set_agent.add_argument("--stage", choices=("clarify", "code", "review"))
    p_set_agent.set_defaults(func=cmd_set_agent)
    p_set_workspace = sub.add_parser("set-workspace")
    p_set_workspace.add_argument("record_id")
    p_set_workspace.add_argument("workspace")
    p_set_workspace.set_defaults(func=cmd_set_workspace)
    p_logs = sub.add_parser("logs")
    p_logs.add_argument("service", nargs="?", choices=("all", "listener", "dispatcher"), default="all")
    p_logs.add_argument("-n", "--lines", type=int, default=80)
    p_logs.set_defaults(func=cmd_logs)
    p_inbox = sub.add_parser("inbox")
    p_inbox.add_argument("--status", choices=("pending", "processing", "done", "failed"))
    p_inbox.add_argument("-n", "--limit", type=int, default=20)
    p_inbox.set_defaults(func=cmd_inbox)
    p_replay = sub.add_parser("replay")
    p_replay.add_argument("id", nargs="?", type=int)
    p_replay.add_argument("--failed", action="store_true")
    p_replay.add_argument("-n", "--limit", type=int, default=20)
    p_replay.set_defaults(func=cmd_replay)
    sub.add_parser("once").set_defaults(func=cmd_once)
    sub.add_parser("doctor").set_defaults(func=cmd_doctor)
    sub.add_parser("restart").set_defaults(func=cmd_restart)
    sub.add_parser("stop").set_defaults(func=cmd_stop)
    sub.add_parser("start").set_defaults(func=cmd_start)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
