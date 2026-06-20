#!/usr/bin/env python3
"""Local health/state helpers for agent-pipeline.

Runtime health is stored outside Feishu Base so local diagnosis still works
when Feishu, network, or credentials are broken.
"""
from __future__ import annotations

import json
import os
import socket
import tempfile
import time
from pathlib import Path
from typing import Any

PIPELINE_DIR = Path(__file__).resolve().parent          # src/
_ROOT = PIPELINE_DIR.parent                             # 项目根
STATE_DIR = Path(os.getenv("PIPELINE_STATE_DIR", str(_ROOT / "state")))   # 运行时状态放根目录
EVENTS_FILE = STATE_DIR / "events.jsonl"


def _json_default(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _now() -> float:
    return time.time()


def _base_payload(component: str, event: str, fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "ts": _now(),
        "time": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "component": component,
        "event": event,
        "pid": os.getpid(),
        "host": socket.gethostname(),
        **fields,
    }


def emit(component: str, event: str, **fields: Any) -> None:
    """Write latest component state and append an event line.

    Best-effort by design: health recording must never break the pipeline.
    """
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        payload = _base_payload(component, event, fields)
        latest = STATE_DIR / f"{component}.json"
        fd, tmp_name = tempfile.mkstemp(prefix=latest.name + ".", dir=str(STATE_DIR))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=_json_default)
            f.write("\n")
        os.replace(tmp_name, latest)
        with EVENTS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, default=_json_default) + "\n")
    except Exception:
        return


def read_all() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not STATE_DIR.exists():
        return result
    for path in sorted(STATE_DIR.glob("*.json")):
        try:
            result[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return result


def tail_events(limit: int = 20) -> list[dict[str, Any]]:
    try:
        lines = EVENTS_FILE.read_text(encoding="utf-8").splitlines()[-limit:]
    except Exception:
        return []
    events: list[dict[str, Any]] = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except Exception:
            pass
    return events


def _read_events(max_lines: int = 20000) -> list[dict[str, Any]]:
    """读取 events.jsonl（尾部 max_lines 行，防止文件过大）。"""
    try:
        lines = EVENTS_FILE.read_text(encoding="utf-8").splitlines()[-max_lines:]
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def summary(hours: float = 24.0, events: list[dict] | None = None, now: float | None = None) -> dict:
    """从埋点聚合一段时间内的运行情况：agent 调用/耗时、卡死自愈、验收门、状态流转。"""
    now = _now() if now is None else now
    cutoff = now - hours * 3600
    evs = _read_events() if events is None else events
    evs = [e for e in evs if float(e.get("ts", 0) or 0) >= cutoff]

    done = [e for e in evs if e.get("event") == "agent_done"]
    durs = [float(e.get("duration", 0) or 0) for e in done]
    by_engine: dict[str, int] = {}
    for e in done:
        eng = e.get("engine") or "?"
        by_engine[eng] = by_engine.get(eng, 0) + 1
    gate = [e for e in evs if e.get("event") == "gate_done"]
    gate_ok = sum(1 for e in gate if e.get("ok"))
    trans: dict[str, int] = {}
    for e in evs:
        if e.get("event") == "transition":
            to = e.get("to") or "?"
            trans[to] = trans.get(to, 0) + 1
    return {
        "hours": hours,
        "agent_calls": len(done),
        "by_engine": by_engine,
        "avg_duration": round(sum(durs) / len(durs), 1) if durs else 0.0,
        "total_duration": round(sum(durs)),
        "inactive_kills": sum(1 for e in evs if e.get("event") == "agent_inactive_kill"),
        "timeouts": sum(1 for e in evs if e.get("event") == "agent_timeout"),
        "gate_ok": gate_ok,
        "gate_fail": len(gate) - gate_ok,
        "transitions": trans,
    }


def summary_text(hours: float = 24.0, events: list[dict] | None = None, now: float | None = None) -> str:
    """把 summary() 渲染成飞书可读的纯文本报表。"""
    s = summary(hours, events=events, now=now)
    hrs = int(s["hours"])
    tr = s["transitions"]
    eng = "、".join(f"{k} {v}" for k, v in s["by_engine"].items()) or "无"
    mins = s["total_duration"] // 60
    lines = [
        f"📊 最近 {hrs}h 运行报表",
        f"· agent 调用 {s['agent_calls']} 次（{eng}）",
        f"· 平均耗时 {s['avg_duration']}s · 累计 {mins}m",
        f"· 卡死自愈 {s['inactive_kills']} 次 · 超时 {s['timeouts']} 次",
        f"· 验收门 通过 {s['gate_ok']} / 失败 {s['gate_fail']}",
    ]
    if tr:
        done = tr.get("完成", 0)
        blocked = tr.get("已阻塞", 0)
        review = tr.get("Review中", 0)
        lines.append(f"· 流转：完成 {done} · 阻塞 {blocked} · 进入Review {review}")
    return "\n".join(lines)


def age_text(ts: float | int | None) -> str:
    if not ts:
        return "unknown"
    seconds = max(0, int(_now() - float(ts)))
    if seconds < 60:
        return f"{seconds}s ago"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{sec:02d}s ago"
    hours, minute = divmod(minutes, 60)
    return f"{hours}h{minute:02d}m ago"
