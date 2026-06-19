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
