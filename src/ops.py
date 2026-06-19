#!/usr/bin/env python3
"""Shared manual operations for Feishu commands and pipelinectl."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import config as C
import lark
import runs


@dataclass(frozen=True)
class OpResult:
    ok: bool
    message: str
    dispatch: bool = False


def _append_log(fields: dict[str, Any], line: str) -> str:
    return ((fields.get(C.F_LOG) or "") + line + "\n").strip() + "\n"


def _title(rec: dict[str, Any]) -> str:
    return rec["fields"].get(C.F_TITLE) or rec["record_id"]


def update_with_log(rec: dict[str, Any], fields: dict[str, Any], log_line: str | None = None) -> None:
    payload = dict(fields)
    if log_line:
        payload[C.F_LOG] = _append_log(rec["fields"], log_line)
    lark.update(rec["record_id"], payload)
    rec["fields"].update(payload)


def find_by_record_id(records: list[dict[str, Any]], record_id: str) -> dict[str, Any] | None:
    for rec in records:
        if rec.get("record_id") == record_id:
            return rec
    return None


def find_latest_for_chat(records: list[dict[str, Any]], chat_id: str, statuses: set[str] | None = None) -> dict[str, Any] | None:
    candidates = []
    for rec in records:
        fields = rec["fields"]
        if fields.get(C.F_CHAT) != chat_id:
            continue
        if statuses and fields.get(C.F_STATUS) not in statuses:
            continue
        candidates.append(rec)
    return candidates[-1] if candidates else None


def retry_record(rec: dict[str, Any]) -> OpResult:
    status = rec["fields"].get(C.F_STATUS)
    if status == C.S_DONE:
        return OpResult(False, f"「{_title(rec)}」已完成，不能重试。")
    if status == C.S_MERGE:
        return OpResult(False, f"「{_title(rec)}」已到待合并，不需要重试；如需重做请先重新澄清或改回开发中。")
    if status == C.S_BLOCKED:
        return OpResult(False, f"「{_title(rec)}」当前已阻塞，请先执行解除阻塞。")
    runs.retry_now(rec["record_id"], "manual retry")
    update_with_log(rec, {C.F_FAILS: 0}, "[manual] 清理执行锁/重试等待，准备立即重试")
    dispatch = status in C.ACTIONABLE
    return OpResult(True, f"已安排「{_title(rec)}」立即重试。", dispatch=dispatch)


def clear_lock(rec: dict[str, Any]) -> OpResult:
    cleared = runs.clear(rec["record_id"], "manual clear lock")
    suffix = "已清理" if cleared else "没有本地锁需要清理"
    update_with_log(rec, {}, f"[manual] {suffix}")
    return OpResult(True, f"{suffix}：{_title(rec)}")


def unblock_record(rec: dict[str, Any], status: str = C.S_DEV) -> OpResult:
    if status not in C.ACTIONABLE:
        allowed = " / ".join(sorted(C.ACTIONABLE))
        return OpResult(False, f"解除阻塞目标状态必须是：{allowed}")
    runs.clear(rec["record_id"], "manual unblock")
    update_with_log(
        rec,
        {C.F_STATUS: status, C.F_FAILS: 0},
        f"[manual] 解除阻塞，回到{status}",
    )
    return OpResult(True, f"已解除阻塞并回到「{status}」：{_title(rec)}", dispatch=True)


def mark_done(rec: dict[str, Any]) -> OpResult:
    runs.clear(rec["record_id"], "manual done")
    update_with_log(
        rec,
        {C.F_STATUS: C.S_DONE},
        "[manual] 人工标记完成",
    )
    return OpResult(True, f"已标记完成：{_title(rec)}")


def restart_clarify(rec: dict[str, Any]) -> OpResult:
    if rec["fields"].get(C.F_STATUS) == C.S_DONE:
        return OpResult(False, f"「{_title(rec)}」已完成，不能重新澄清。")
    runs.clear(rec["record_id"], "manual restart clarify")
    update_with_log(
        rec,
        {C.F_STATUS: C.S_CLARIFY, C.F_FAILS: 0},
        "[manual] 重新进入澄清",
    )
    return OpResult(True, f"已重新进入澄清：{_title(rec)}", dispatch=True)


def set_agent(rec: dict[str, Any], agent: str, stage: str | None = None) -> OpResult:
    engine = C.AGENT_ALIASES.get(agent) or C.AGENT_ALIASES.get(agent.lower()) or agent.lower()
    if engine not in C.AGENT_CMDS:
        return OpResult(False, f"未知 Agent：{agent}。可选：{', '.join(sorted(C.AGENT_CMDS))}")
    field = {
        "clarify": C.F_AGENT_CLARIFY,
        "code": C.F_AGENT_CODE,
        "review": C.F_AGENT_REVIEW,
    }.get(stage or "", C.F_AGENT)
    update_with_log(rec, {field: engine}, f"[manual] 设置{field}={engine}")
    return OpResult(True, f"已设置 {field}={engine}：{_title(rec)}")


def set_workspace(rec: dict[str, Any], workspace_key: str) -> OpResult:
    update_with_log(rec, {C.F_WORKSPACE: workspace_key}, f"[manual] 设置工作区={workspace_key}")
    return OpResult(True, f"已设置工作区={workspace_key}：{_title(rec)}")


def set_status(rec: dict[str, Any], status: str) -> OpResult:
    valid = {
        C.S_CLARIFY, C.S_ANSWER, C.S_CONFIRM, C.S_DEV,
        C.S_REVIEW, C.S_MERGE, C.S_DONE, C.S_BLOCKED,
    }
    if status not in valid:
        return OpResult(False, f"未知状态：{status}")
    runs.clear(rec["record_id"], f"manual set status {status}")
    update_with_log(rec, {C.F_STATUS: status}, f"[manual] 设置状态={status}")
    return OpResult(True, f"已设置状态={status}：{_title(rec)}", dispatch=status in C.ACTIONABLE)

