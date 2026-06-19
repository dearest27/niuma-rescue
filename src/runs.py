#!/usr/bin/env python3
"""SQLite-backed execution claims for dispatcher records.

This is the local source of truth for "who is working on this record right now".
Feishu remains the human-facing board; this file keeps idempotency and retry
metadata out of the visible table columns.
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any

import config as C
import health

DB_PATH = health.STATE_DIR / "runs.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS record_runs (
    record_id TEXT PRIMARY KEY,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    state TEXT NOT NULL,
    run_id TEXT NOT NULL,
    owner_pid INTEGER NOT NULL,
    owner_host TEXT NOT NULL,
    title TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    next_retry_at REAL,
    claimed_at REAL NOT NULL,
    heartbeat_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_record_runs_state_retry
    ON record_runs(state, next_retry_at, heartbeat_at);

CREATE TABLE IF NOT EXISTS run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    event TEXT NOT NULL,
    status_from TEXT,
    status_to TEXT,
    message TEXT,
    payload_json TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_events_record_id
    ON run_events(record_id, id);
"""


@dataclass(frozen=True)
class Claim:
    ok: bool
    run_id: str = ""
    reason: str = ""
    attempts: int = 0
    next_retry_at: float | None = None


def _now() -> float:
    return time.time()


def _connect() -> sqlite3.Connection:
    health.STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(SCHEMA)
    return conn


def _event(
    conn: sqlite3.Connection,
    run_id: str,
    record_id: str,
    stage: str,
    event: str,
    *,
    status_from: str | None = None,
    status_to: str | None = None,
    message: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO run_events
            (run_id, record_id, stage, event, status_from, status_to, message, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            record_id,
            stage,
            event,
            status_from,
            status_to,
            message,
            json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
            _now(),
        ),
    )


def claim(record_id: str, stage: str, status: str, title: str = "") -> Claim:
    """Try to claim one record/stage for execution.

    A live processing row blocks duplicate work. Failed rows can be retried once
    their next_retry_at has passed. Stale processing rows are reclaimed.
    """
    now = _now()
    stale_before = now - C.EXECUTION_STALE_AFTER
    owner_pid = os.getpid()
    owner_host = socket.gethostname()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM record_runs WHERE record_id = ?",
            (record_id,),
        ).fetchone()
        if row:
            if row["state"] == "processing" and float(row["heartbeat_at"]) >= stale_before:
                return Claim(False, reason="busy", run_id=str(row["run_id"]), attempts=int(row["attempts"]))
            if (
                row["state"] == "failed"
                and row["next_retry_at"] is not None
                and float(row["next_retry_at"]) > now
                and row["stage"] == stage
                and row["status"] == status
            ):
                return Claim(
                    False,
                    reason="retry_wait",
                    run_id=str(row["run_id"]),
                    attempts=int(row["attempts"]),
                    next_retry_at=float(row["next_retry_at"]),
                )
            attempts = int(row["attempts"]) + 1
        else:
            attempts = 1

        run_id = f"{record_id}-{stage}-{int(now)}-{uuid.uuid4().hex[:8]}"
        conn.execute(
            """
            INSERT INTO record_runs
                (record_id, stage, status, state, run_id, owner_pid, owner_host, title,
                 attempts, last_error, next_retry_at, claimed_at, heartbeat_at, updated_at)
            VALUES (?, ?, ?, 'processing', ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)
            ON CONFLICT(record_id) DO UPDATE SET
                stage=excluded.stage,
                status=excluded.status,
                state='processing',
                run_id=excluded.run_id,
                owner_pid=excluded.owner_pid,
                owner_host=excluded.owner_host,
                title=excluded.title,
                attempts=excluded.attempts,
                last_error=NULL,
                next_retry_at=NULL,
                claimed_at=excluded.claimed_at,
                heartbeat_at=excluded.heartbeat_at,
                updated_at=excluded.updated_at
            """,
            (
                record_id,
                stage,
                status,
                run_id,
                owner_pid,
                owner_host,
                title,
                attempts,
                now,
                now,
                now,
            ),
        )
        _event(conn, run_id, record_id, stage, "claimed", status_from=status, message=title)
    return Claim(True, run_id=run_id, attempts=attempts)


def heartbeat(run_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE record_runs SET heartbeat_at = ?, updated_at = ? WHERE run_id = ?",
            (_now(), _now(), run_id),
        )


def complete(run_id: str, status_from: str, status_to: str, message: str = "") -> None:
    now = _now()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM record_runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            return
        conn.execute(
            """
            UPDATE record_runs
            SET state = 'done', status = ?, last_error = NULL, next_retry_at = NULL,
                heartbeat_at = ?, updated_at = ?
            WHERE run_id = ?
            """,
            (status_to, now, now, run_id),
        )
        _event(
            conn,
            run_id,
            str(row["record_id"]),
            str(row["stage"]),
            "done",
            status_from=status_from,
            status_to=status_to,
            message=message,
        )


def fail(run_id: str, error: str, retry_delay: int | None = None) -> None:
    now = _now()
    next_retry = now + retry_delay if retry_delay and retry_delay > 0 else None
    with _connect() as conn:
        row = conn.execute("SELECT * FROM record_runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            return
        conn.execute(
            """
            UPDATE record_runs
            SET state = 'failed', last_error = ?, next_retry_at = ?,
                heartbeat_at = ?, updated_at = ?
            WHERE run_id = ?
            """,
            (error[:1000], next_retry, now, now, run_id),
        )
        _event(
            conn,
            run_id,
            str(row["record_id"]),
            str(row["stage"]),
            "failed",
            status_from=str(row["status"]),
            message=error[:1000],
            payload={"next_retry_at": next_retry},
        )


def clear(record_id: str, reason: str = "manual clear") -> bool:
    """Release local execution metadata for a record.

    This is intentionally local-only. The caller decides what Feishu status
    should become after clearing the lock/retry row.
    """
    with _connect() as conn:
        row = conn.execute("SELECT * FROM record_runs WHERE record_id = ?", (record_id,)).fetchone()
        if not row:
            return False
        _event(
            conn,
            str(row["run_id"]),
            record_id,
            str(row["stage"]),
            "cleared",
            status_from=str(row["status"]),
            message=reason,
        )
        conn.execute("DELETE FROM record_runs WHERE record_id = ?", (record_id,))
    return True


def retry_now(record_id: str, reason: str = "manual retry") -> bool:
    """Clear retry wait / processing state so dispatcher may claim this record again."""
    return clear(record_id, reason)


def list_runs(limit: int = 20, state: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM record_runs"
    args: tuple[Any, ...]
    if state:
        sql += " WHERE state = ? ORDER BY updated_at DESC LIMIT ?"
        args = (state, limit)
    else:
        sql += " ORDER BY updated_at DESC LIMIT ?"
        args = (limit,)
    with _connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(row) for row in rows]


def events(record_id: str | None = None, limit: int = 40) -> list[dict[str, Any]]:
    sql = "SELECT * FROM run_events"
    args: tuple[Any, ...]
    if record_id:
        sql += " WHERE record_id = ? ORDER BY id DESC LIMIT ?"
        args = (record_id, limit)
    else:
        sql += " ORDER BY id DESC LIMIT ?"
        args = (limit,)
    with _connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
        except Exception:
            item["payload"] = {}
        result.append(item)
    return result
