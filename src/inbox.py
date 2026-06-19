#!/usr/bin/env python3
"""SQLite-backed inbound message inbox.

The listener writes every received Feishu text message here before routing it.
This gives the pipeline a replayable local receipt log instead of relying on
in-memory callbacks only.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Callable

import health

DB_PATH = health.STATE_DIR / "inbox.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS inbox_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    handled INTEGER,
    message_json TEXT NOT NULL,
    last_error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inbox_status_id ON inbox_messages(status, id);
"""


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


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["message"] = json.loads(item.pop("message_json"))
    return item


def enqueue(event_key: str | None, message: dict[str, Any]) -> tuple[int | None, bool]:
    """Insert a message. Returns (id, inserted). Duplicate event_key is ignored."""
    now = _now()
    key = event_key or message.get("message_id")
    payload = json.dumps(message, ensure_ascii=False, sort_keys=True)
    with _connect() as conn:
        if key:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO inbox_messages
                    (event_key, status, attempts, message_json, created_at, updated_at)
                VALUES (?, 'pending', 0, ?, ?, ?)
                """,
                (key, payload, now, now),
            )
            inserted = cur.rowcount == 1
            row = conn.execute("SELECT id FROM inbox_messages WHERE event_key = ?", (key,)).fetchone()
            return (int(row["id"]) if row else None), inserted
        cur = conn.execute(
            """
            INSERT INTO inbox_messages
                (event_key, status, attempts, message_json, created_at, updated_at)
            VALUES (NULL, 'pending', 0, ?, ?, ?)
            """,
            (payload, now, now),
        )
        return int(cur.lastrowid), True


def claim(limit: int = 10, stale_after: int = 600) -> list[dict[str, Any]]:
    """Claim pending rows. Old processing rows are returned to pending first."""
    now = _now()
    with _connect() as conn:
        conn.execute(
            "UPDATE inbox_messages SET status = 'pending', updated_at = ? "
            "WHERE status = 'processing' AND updated_at < ?",
            (now, now - stale_after),
        )
        rows = conn.execute(
            "SELECT * FROM inbox_messages WHERE status = 'pending' ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
        ids = [int(row["id"]) for row in rows]
        if ids:
            q = ",".join("?" for _ in ids)
            conn.execute(
                f"UPDATE inbox_messages SET status = 'processing', attempts = attempts + 1, "
                f"updated_at = ? WHERE id IN ({q})",
                (now, *ids),
            )
    return [row for row_id in ids if (row := get(row_id))]


def get(row_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM inbox_messages WHERE id = ?", (row_id,)).fetchone()
    return _row_to_dict(row) if row else None


def mark_done(row_id: int, handled: bool) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE inbox_messages SET status = 'done', handled = ?, last_error = NULL, "
            "updated_at = ? WHERE id = ?",
            (1 if handled else 0, _now(), row_id),
        )


def mark_failed(row_id: int, error: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE inbox_messages SET status = 'failed', last_error = ?, updated_at = ? WHERE id = ?",
            (error[:1000], _now(), row_id),
        )


def requeue(row_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE inbox_messages SET status = 'pending', last_error = NULL, updated_at = ? WHERE id = ?",
            (_now(), row_id),
        )


def list_rows(status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    sql = "SELECT * FROM inbox_messages"
    if status:
        sql += " WHERE status = ? ORDER BY id DESC LIMIT ?"
        args: tuple[Any, ...] = (status, limit)
    else:
        sql += " ORDER BY id DESC LIMIT ?"
        args = (limit,)
    with _connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [_row_to_dict(row) for row in rows]


def stats() -> dict[str, int]:
    with _connect() as conn:
        rows = conn.execute("SELECT status, COUNT(*) AS count FROM inbox_messages GROUP BY status").fetchall()
    return {str(row["status"]): int(row["count"]) for row in rows}


def process_pending(
    handler: Callable[[dict[str, Any]], bool],
    trigger_dispatch: Callable[[], None],
    limit: int = 10,
) -> int:
    count = 0
    for row in claim(limit=limit):
        row_id = int(row["id"])
        msg = row["message"]
        try:
            handled = handler(msg)
            mark_done(row_id, handled)
            health.emit(
                "listener",
                "inbox_done",
                inbox_id=row_id,
                chat_id=msg.get("chat_id"),
                handled=handled,
            )
            if handled:
                trigger_dispatch()
        except Exception as exc:
            mark_failed(row_id, str(exc))
            health.emit(
                "listener",
                "inbox_failed",
                inbox_id=row_id,
                chat_id=msg.get("chat_id"),
                error=str(exc),
            )
        count += 1
    return count
