#!/usr/bin/env python3
"""飞书交互卡片构造：人工卡点用卡片 + 按钮，点按钮即推进状态。

按钮的 value 里带 {record_id, action}，回调时 message_router.handle_card_action 据此操作。
"""
from __future__ import annotations

import config as C


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


def confirm_card(rec: dict) -> dict:
    """待确认卡片：PRD + 「确认开发」按钮。"""
    f = rec["fields"]
    rid = rec["record_id"]
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"📋 待确认：{f.get(C.F_TITLE) or ''}"},
            "template": "blue",
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": _trunc(f.get(C.F_PRD) or "（无 PRD）")}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": "确认后即开始开发；要改需求直接回我文字。"}},
            {"tag": "action", "actions": [_button("✅ 确认开发", "confirm", rid)]},
        ],
    }


def merge_card(rec: dict) -> dict:
    """待合并卡片：PR/分支链接 + 「已合并/完成」按钮。"""
    f = rec["fields"]
    rid = rec["record_id"]
    link = f.get(C.F_LINK) or "（无链接）"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"🔀 待合并：{f.get(C.F_TITLE) or ''}"},
            "template": "green",
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"Review 已通过。PR / 分支：\n{link}"}},
            {"tag": "div", "text": {"tag": "lark_md", "content": "你 merge 后点下面按钮收尾。"}},
            {"tag": "action", "actions": [_button("✅ 已合并 / 完成", "done", rid)]},
        ],
    }


def done_toast_card(title: str, note: str, template: str = "grey") -> dict:
    """点完按钮后用来"替换"原卡片，把按钮去掉、显示结果。"""
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": template},
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": note}}],
    }
