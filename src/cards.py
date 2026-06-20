#!/usr/bin/env python3
"""飞书交互卡片构造：人工卡点用卡片 + 按钮，点按钮即推进状态。

按钮的 value 里带 {record_id, action}，回调时 message_router.handle_card_action 据此操作。
"""
from __future__ import annotations

import config as C


TEMPLATE_STATUS = {
    C.S_CLARIFY: "wathet",
    C.S_ANSWER: "yellow",
    C.S_CONFIRM: "blue",
    C.S_DEV: "purple",
    C.S_REVIEW: "indigo",
    C.S_MERGE: "green",
    C.S_DONE: "green",
    C.S_BLOCKED: "red",
}


def _button(text: str, action: str, record_id: str, btn_type: str = "primary") -> dict:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": btn_type,
        "value": {"record_id": record_id, "action": action},
    }


def _trunc(s: str, n: int = 1800) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + "\n…（略）"


def _title(rec_or_fields: dict) -> str:
    fields = rec_or_fields.get("fields", rec_or_fields)
    return fields.get(C.F_TITLE) or fields.get(C.F_DESC) or rec_or_fields.get("record_id", "")


def _template(status: str | None, default: str = "blue") -> str:
    return TEMPLATE_STATUS.get(status or "", default)


def _header(title: str, status: str | None = None, template: str | None = None) -> dict:
    return {
        "title": {"tag": "plain_text", "content": title[:80]},
        "template": template or _template(status),
    }


def _md(content: str) -> dict:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def _kv(label: str, value: object) -> dict:
    text = str(value or "-")
    return {"is_short": True, "text": {"tag": "lark_md", "content": f"**{label}**\n{text}"}}


def _fields(*items: tuple[str, object]) -> dict:
    return {"tag": "div", "fields": [_kv(label, value) for label, value in items]}


def _link_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    if text.startswith(("http://", "https://")):
        return f"[打开链接]({text})"
    return text


def _record_fields(rec: dict) -> dict:
    return rec.get("fields", rec)


def intake_card(fields: dict) -> dict:
    """新需求收取后的回执卡片。"""
    title = fields.get(C.F_TITLE) or fields.get(C.F_DESC) or "新需求"
    agent = fields.get(C.F_AGENT_CLARIFY) or fields.get(C.F_AGENT) or "默认"
    workspace = fields.get(C.F_WORKSPACE) or "默认"
    return {
        "config": {"wide_screen_mode": True},
        "header": _header(f"需求已进入流水线：{title}", C.S_CLARIFY, "wathet"),
        "elements": [
            _fields(("状态", C.S_CLARIFY), ("工作区", workspace), ("澄清 Agent", agent)),
            _md("我会先做需求澄清；如果信息不足，会继续向你提问。"),
        ],
    }


def status_card(rec: dict) -> dict:
    """当前需求状态卡片。"""
    f = _record_fields(rec)
    status = f.get(C.F_STATUS)
    return {
        "config": {"wide_screen_mode": True},
        "header": _header(f"当前需求：{_title(rec)}", status),
        "elements": [
            _fields(
                ("状态", status or "-"),
                ("工作区", f.get(C.F_WORKSPACE) or "默认"),
                ("执行 Agent", f.get(C.F_AGENT) or "默认"),
                ("失败次数", f.get(C.F_FAILS) or 0),
            ),
            _md(f"**链接**\n{_link_text(f.get(C.F_LINK))}"),
        ],
    }


def confirm_card(rec: dict) -> dict:
    """待确认卡片：PRD + 「确认开发」按钮。"""
    f = rec["fields"]
    rid = rec["record_id"]
    return {
        "config": {"wide_screen_mode": True},
        "header": _header(f"待确认：{f.get(C.F_TITLE) or ''}", C.S_CONFIRM, "blue"),
        "elements": [
            _fields(
                ("状态", C.S_CONFIRM),
                ("工作区", f.get(C.F_WORKSPACE) or "默认"),
                ("开发 Agent", f.get(C.F_AGENT_CODE) or f.get(C.F_AGENT) or "默认"),
            ),
            _md(_trunc(f.get(C.F_PRD) or "（无 PRD）")),
            {"tag": "hr"},
            _md("确认后会进入开发；如果要补充或修改，直接回复文字即可。"),
            {"tag": "action", "actions": [_button("确认开发", "confirm", rid)]},
        ],
    }


def merge_card(rec: dict) -> dict:
    """待合并卡片：PR/分支链接 + 「已合并/完成」按钮。"""
    f = rec["fields"]
    rid = rec["record_id"]
    link = f.get(C.F_LINK) or "（无链接）"
    return {
        "config": {"wide_screen_mode": True},
        "header": _header(f"待合并：{f.get(C.F_TITLE) or ''}", C.S_MERGE, "green"),
        "elements": [
            _fields(
                ("状态", C.S_MERGE),
                ("工作区", f.get(C.F_WORKSPACE) or "默认"),
                ("Review Agent", f.get(C.F_AGENT_REVIEW) or f.get(C.F_AGENT) or "默认"),
            ),
            _md(f"**Review 已通过**\n\nPR / MR / 分支：\n{_link_text(link)}"),
            _md("合并或确认提交后，点击下面按钮收尾。"),
            {"tag": "action", "actions": [_button("已合并 / 完成", "done", rid)]},
        ],
    }


def done_toast_card(title: str, note: str, template: str = "grey") -> dict:
    """点完按钮后用来"替换"原卡片，把按钮去掉、显示结果。"""
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title[:80]}, "template": template},
        "elements": [_md(note)],
    }
