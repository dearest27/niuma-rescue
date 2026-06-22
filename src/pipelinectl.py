#!/usr/bin/env python3
"""Small operations CLI for agent-pipeline."""
from __future__ import annotations

import argparse
import os
import platform
import shutil
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


def _mark(ok: bool) -> str:
    return "OK" if ok else "WARN"


def cmd_diagnose(args: argparse.Namespace) -> int:
    print("agent-pipeline diagnose")
    print(f"  dir: {PIPELINE_DIR}")
    print(f"  state: {health.STATE_DIR}")
    print()

    if args.record_id:
        _print_record_diagnosis(args.record_id, args.limit)
        print()

    print("config:")
    for key, value in (
        ("FEISHU_APP_ID", bool(os.getenv("FEISHU_APP_ID"))),
        ("FEISHU_APP_SECRET", bool(os.getenv("FEISHU_APP_SECRET"))),
        ("PIPELINE_BASE_TOKEN", bool(C.BASE_TOKEN)),
        ("PIPELINE_TABLE_ID", bool(C.TABLE_ID)),
        ("PIPELINE_REPO_PATH", bool(os.getenv("PIPELINE_REPO_PATH"))),
    ):
        print(f"  {_mark(value)} {key}")
    for key, path in (
        ("fields", C.FIELDS_FILE),
        ("workspaces", C.WORKSPACES_FILE),
        ("agents", C.AGENTS_FILE),
    ):
        status = "custom" if path.exists() else "default"
        print(f"  OK {key}_file: {path} ({status})")
    print()

    print("services:")
    latest = health.read_all()
    for service in SERVICES:
        svc = service_info(service)
        item = latest.get(service) or {}
        service_ok = svc.get("state") not in {"not-loaded", "unknown"} or bool(item)
        age = health.age_text(item.get("ts")) if item else "no heartbeat"
        print(f"  {_mark(service_ok)} {service}: service={svc.get('state', 'unknown')} health={item.get('event', '-')}/{age}")
    print()

    print("workspaces:")
    try:
        for ws in workspaces.list_workspaces():
            exists = ws.path.exists()
            review = "-"
            if ws.scm == "git" and ws.pr_enabled:
                review = f"{ws.pr_provider}->{ws.target_branch or '-'}"
            elif ws.scm == "svn" and ws.push_enabled:
                review = "svn commit"
            print(f"  {_mark(exists)} {ws.key}: scm={ws.scm} path={ws.path} base={ws.base_ref} review={review}")
    except Exception as exc:
        print(f"  WARN workspace config failed: {exc}")
    print()

    print("agent cli:")
    for engine in sorted({C.ENGINE_CLARIFY, C.ENGINE_CODE, C.ENGINE_REVIEW}):
        binary = C.AGENT_CMDS.get(engine, [engine])[0]
        found = shutil.which(binary)
        print(f"  {_mark(bool(found))} {engine}: {binary} {found or 'not found'}")
    print()

    print("inbox:")
    counts = inbox.stats()
    if counts:
        print("  " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    else:
        print("  empty")
    failed_inbox = inbox.list_rows(status="failed", limit=args.limit)
    for row in failed_inbox:
        print(f"  WARN inbox#{row['id']} failed attempts={row['attempts']} error={str(row.get('last_error') or '')[:180]}")
    print()

    print("runs:")
    active = runs.list_runs(limit=args.limit, state="processing")
    failed = runs.list_runs(limit=args.limit, state="failed")
    if not active and not failed:
        print("  no processing/failed runs")
    for row in active:
        print(f"  WARN processing record={row['record_id']} stage={row['stage']} age={health.age_text(row['updated_at'])}")
    for row in failed:
        retry = f" next_retry={health.age_text(row['next_retry_at'])}" if row.get("next_retry_at") else ""
        print(f"  WARN failed record={row['record_id']} stage={row['stage']}{retry} error={str(row.get('last_error') or '')[:180]}")
    print()

    print("recent errors:")
    shown = 0
    for event in reversed(health.tail_events(args.limit * 3)):
        if event.get("error") or "failed" in str(event.get("event", "")):
            print(f"  {event.get('time')} {event.get('component')} {event.get('event')} {str(event.get('error') or '')[:180]}")
            shown += 1
            if shown >= args.limit:
                break
    if not shown:
        print("  none")
    print()

    if args.doctor:
        print("doctor:")
        return cmd_doctor(argparse.Namespace())
    return 0


def _field_text(value: object) -> str:
    if value is None:
        return "-"
    text = str(value).replace("\n", " ").strip()
    return text or "-"


def _print_record_diagnosis(record_id: str, limit: int) -> None:
    print(f"record: {record_id}")
    try:
        rec = _load_record(record_id)
    except SystemExit as exc:
        print(f"  WARN {exc}")
        return
    fields = rec["fields"]
    for label, key in (
        ("title", C.F_TITLE),
        ("status", C.F_STATUS),
        ("workspace", C.F_WORKSPACE),
        ("fails", C.F_FAILS),
        ("link", C.F_LINK),
        ("chat", C.F_CHAT),
    ):
        print(f"  {label}: {_field_text(fields.get(key))}")
    agent = fields.get(C.F_AGENT) or "-"
    print(
        "  agents: "
        f"default={agent} clarify={fields.get(C.F_AGENT_CLARIFY) or '-'} "
        f"code={fields.get(C.F_AGENT_CODE) or '-'} review={fields.get(C.F_AGENT_REVIEW) or '-'}"
    )
    log_lines = [line.strip() for line in (fields.get(C.F_LOG) or "").splitlines() if line.strip()]
    if log_lines:
        print("  recent log:")
        for line in log_lines[-min(limit, 5):]:
            print(f"    {line[:220]}")
    run_rows = [row for row in runs.list_runs(limit=200) if row.get("record_id") == record_id]
    if run_rows:
        print("  local run:")
        for row in run_rows[:limit]:
            retry = f" next_retry={health.age_text(row['next_retry_at'])}" if row.get("next_retry_at") else ""
            print(
                f"    {row['state']} stage={row['stage']} status={row['status']} "
                f"attempts={row['attempts']} updated={health.age_text(row['updated_at'])}{retry}"
            )
            if row.get("last_error"):
                print(f"      error: {str(row['last_error'])[:220]}")
    else:
        print("  local run: none")
    event_rows = runs.events(record_id=record_id, limit=limit)
    if event_rows:
        print("  run events:")
        for row in event_rows:
            arrow = ""
            if row.get("status_from") or row.get("status_to"):
                arrow = f" {row.get('status_from') or '-'}->{row.get('status_to') or '-'}"
            print(f"    #{row['id']} {health.age_text(row['created_at'])} {row['event']}{arrow}")
            if row.get("message"):
                print(f"      {str(row['message'])[:220]}")
    else:
        print("  run events: none")


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
        review = "-"
        if ws.scm == "git" and ws.pr_enabled:
            review = f"{ws.pr_provider}->{ws.target_branch or '-'}"
        elif ws.scm == "svn" and ws.push_enabled:
            review = "svn commit"
        print(
            f"{marker} {ws.key}: path={ws.path} scm={ws.scm} mode={ws.work_mode} "
            f"base={ws.base_ref} review={review} test={ws.test_cmd or '-'}"
        )
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
    p_diagnose = sub.add_parser("diagnose")
    p_diagnose.add_argument("record_id", nargs="?")
    p_diagnose.add_argument("-n", "--limit", type=int, default=5)
    p_diagnose.add_argument("--doctor", action="store_true", help="also run doctor.py")
    p_diagnose.set_defaults(func=cmd_diagnose)
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
