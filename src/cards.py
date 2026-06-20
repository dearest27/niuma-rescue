#!/usr/bin/env python3
"""飞书交互卡片构造：人工卡点用卡片 + 按钮，点按钮即推进状态。

按钮的 value 里带 {record_id, action}，回调时 message_router.handle_card_action 据此操作。
"""
from __future__ import annotations

import config as C


TEMPLATE_STATUS = {
    C.S_SETUP: "turquoise",
    C.S_CLARIFY: "wathet",
    C.S_ANSWER: "yellow",
    C.S_CONFIRM: "blue",
    C.S_DEV: "purple",
    C.S_REVIEW: "indigo",
    C.S_MERGE: "green",
    C.S_DONE: "green",
    C.S_BLOCKED: "red",
}

STATUS_EMOJI = {
    C.S_SETUP: "🎛", C.S_CLARIFY: "🔍", C.S_ANSWER: "💬", C.S_CONFIRM: "📋",
    C.S_DEV: "🔧", C.S_REVIEW: "🔎", C.S_MERGE: "🚀",
    C.S_DONE: "✔️", C.S_BLOCKED: "🚫",
}

# 看板排序：需要你处理的（阻塞 / 待确认 / 待合并 / 待回答）排前面，机器在跑的靠后。
_BOARD_ORDER = {
    C.S_BLOCKED: 0, C.S_SETUP: 1, C.S_CONFIRM: 2, C.S_MERGE: 3, C.S_ANSWER: 4,
    C.S_CLARIFY: 5, C.S_DEV: 6, C.S_REVIEW: 7,
}


def _button(text: str, action: str, record_id: str, btn_type: str = "primary", **extra) -> dict:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": btn_type,
        "value": {"record_id": record_id, "action": action, **extra},
    }


def _action_row(actions: list[dict]) -> dict:
    return {"tag": "action", "actions": actions}


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
    rid = rec.get("record_id", "")
    actions: list[dict] = []
    if rid:
        if status in C.ACTIONABLE:
            actions.extend([
                _button("重试", "retry", rid),
                _button("清锁", "clear_lock", rid, "default"),
            ])
        elif status == C.S_BLOCKED:
            actions.extend([
                _button("解除阻塞", "unblock_dev", rid),
                _button("重新澄清", "restart_clarify", rid, "default"),
                _button("清锁", "clear_lock", rid, "default"),
            ])
        elif status == C.S_CONFIRM:
            actions.extend([
                _button("确认开发", "confirm", rid),
                _button("重新澄清", "restart_clarify", rid, "default"),
            ])
        elif status == C.S_ANSWER:
            actions.append(_button("重新澄清", "restart_clarify", rid, "default"))
        elif status == C.S_MERGE:
            actions.extend([
                _button("已合并 / 完成", "done", rid),
                _button("重新澄清", "restart_clarify", rid, "default"),
            ])
    elements = [
        _fields(
            ("状态", status or "-"),
            ("工作区", f.get(C.F_WORKSPACE) or "默认"),
            ("执行 Agent", f.get(C.F_AGENT) or "默认"),
            ("失败次数", f.get(C.F_FAILS) or 0),
        ),
        _md(f"**链接**\n{_link_text(f.get(C.F_LINK))}"),
    ]
    tail = _log_tail(f, 3)
    if tail:
        elements.append(_md(f"**最近日志**\n<font color='grey'>{_trunc(tail, 400)}</font>"))
    if actions:
        elements.append(_action_row(actions))
    return {
        "config": {"wide_screen_mode": True},
        "header": _header(f"当前需求：{_title(rec)}", status),
        "elements": elements,
    }


def blocked_card(rec: dict, reason: str = "") -> dict:
    """已阻塞主动告警卡片：原因 + 最近日志 + 一键恢复按钮。"""
    f = _record_fields(rec)
    rid = rec.get("record_id", "")
    elements = [
        _fields(("状态", C.S_BLOCKED), ("失败次数", f.get(C.F_FAILS) or 0),
                ("工作区", f.get(C.F_WORKSPACE) or "默认")),
        _md(f"**阻塞原因**\n{_trunc(reason or _last_log(f) or '（无）', 600)}"),
    ]
    tail = _log_tail(f, 4)
    if tail:
        elements.append(_md(f"**最近日志**\n<font color='grey'>{_trunc(tail, 500)}</font>"))
    if rid:
        elements.append(_action_row([
            _button("解除阻塞", "unblock_dev", rid),
            _button("重新澄清", "restart_clarify", rid, "default"),
            _button("清锁", "clear_lock", rid, "default"),
        ]))
    return {
        "config": {"wide_screen_mode": True},
        "header": _header(f"🚫 需求已阻塞：{_title(rec)}", C.S_BLOCKED, "red"),
        "elements": elements,
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
            _md("确认后会进入开发；想换开发 Agent / 工作区就点「⚙️ 配置」，要补充改需求直接回文字。"),
            _action_row([
                _button("确认开发", "confirm", rid),
                _button("⚙️ 配置 Agent/工作区", "open_settings", rid, "default"),
            ]),
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
            _action_row([_button("已合并 / 完成", "done", rid)]),
        ],
    }


def progress_card(title: str, stage: str, engine: str, stats: dict, status: str | None = None) -> dict:
    """运行中实时进度卡片（原地更新，不刷屏）。stats 来自 agent 流式回调。"""
    elapsed = stats.get("elapsed", 0)
    mins, secs = divmod(int(elapsed), 60)
    used = f"{mins}m{secs:02d}s" if mins else f"{secs}s"
    line = f"⏱ 已用 {used}　·　🛠 {stats.get('tool_calls', 0)} 次工具调用"
    if stats.get("thinking"):
        line += f"　·　💭 思考 {stats['thinking']}"
    return {
        "config": {"wide_screen_mode": True},
        "header": _header(f"运行中：{title}", status, "turquoise"),
        "elements": [
            _fields(("阶段", stage), ("Agent", engine)),
            _md(line),
            _md("_实时进度，完成后会自动更新结果。_"),
        ],
    }


def _last_log(f: dict) -> str:
    """执行日志的最后一行 = 这条需求"最后发生了什么"。"""
    log = (f.get(C.F_LOG) or "").strip()
    return log.splitlines()[-1].strip() if log else ""


def _log_tail(f: dict, n: int = 3) -> str:
    """执行日志最后 n 个非空行，用于"失败可解释"展示。"""
    log = (f.get(C.F_LOG) or "").strip()
    if not log:
        return ""
    lines = [ln for ln in log.splitlines() if ln.strip()]
    return "\n".join(lines[-n:])


def _board_actions(status: str, rid: str) -> list[dict]:
    """看板里"需要你处理"的状态带上按钮，按钮自带 record_id → 可操作任意指定那条。"""
    if status == C.S_BLOCKED:
        return [_button("解除阻塞", "unblock_dev", rid),
                _button("重试", "retry", rid, "default"),
                _button("清锁", "clear_lock", rid, "default")]
    if status == C.S_CONFIRM:
        return [_button("确认开发", "confirm", rid)]
    if status == C.S_MERGE:
        return [_button("已合并 / 完成", "done", rid)]
    return []


def _in_flight(records: list[dict]) -> list[dict]:
    flight = [r for r in records if _record_fields(r).get(C.F_STATUS) != C.S_DONE]
    return sorted(flight, key=lambda r: _BOARD_ORDER.get(_record_fields(r).get(C.F_STATUS), 9))


def board_text(records: list[dict]) -> str:
    """看板的纯文本兜底（卡片发送失败时用）。"""
    flight = _in_flight(records)
    if not flight:
        return "需求看板：当前没有进行中的需求。"
    lines = [f"需求看板 · {len(flight)} 条在途："]
    for r in flight:
        f = _record_fields(r)
        status = f.get(C.F_STATUS) or "-"
        title = f.get(C.F_TITLE) or f.get(C.F_DESC) or r.get("record_id", "")
        fails = int(f.get(C.F_FAILS) or 0)
        mark = f"（失败{fails}）" if fails else ""
        lines.append(f"· {STATUS_EMOJI.get(status, '•')} {status}{mark} | {title}")
    return "\n".join(lines)


def board_card(records: list[dict]) -> dict:
    """需求看板：所有在途需求一览（状态/失败次数/最后日志），需处理项带操作按钮。"""
    flight = _in_flight(records)
    if not flight:
        return {
            "config": {"wide_screen_mode": True},
            "header": _header("需求看板 · 无在途需求", template="grey"),
            "elements": [_md("当前没有进行中的需求。发「需求@cursor：…」开一条。")],
        }
    counts: dict[str, int] = {}
    for r in flight:
        s = _record_fields(r).get(C.F_STATUS) or "-"
        counts[s] = counts.get(s, 0) + 1
    summary = "　·　".join(f"{STATUS_EMOJI.get(s, '')}{s} {n}" for s, n in counts.items())
    elements: list[dict] = [_md(summary), {"tag": "hr"}]
    for r in flight:
        f = _record_fields(r)
        rid = r.get("record_id", "")
        status = f.get(C.F_STATUS) or "-"
        title = f.get(C.F_TITLE) or f.get(C.F_DESC) or rid
        fails = int(f.get(C.F_FAILS) or 0)
        mark = f"　·　❌ 失败 {fails}" if fails else ""
        row = f"{STATUS_EMOJI.get(status, '•')} **{_trunc(title, 60)}** — {status}{mark}"
        last = _last_log(f)
        if last:
            row += f"\n<font color='grey'>{_trunc(last, 120)}</font>"
        elements.append(_md(row))
        acts = _board_actions(status, rid)
        if acts:
            elements.append(_action_row(acts))
        elements.append({"tag": "hr"})
    if elements and elements[-1].get("tag") == "hr":
        elements.pop()
    return {
        "config": {"wide_screen_mode": True},
        "header": _header(f"需求看板 · {len(flight)} 条在途", template="blue"),
        "elements": elements,
    }


AGENT_CHOICES = ["claude", "codex", "gemini", "cursor"]


def _settings_hint(status: str | None) -> str:
    """提示在当前状态下改 Agent/工作区到底会不会生效——这是"设置了不生效"的根源。"""
    if status == C.S_SETUP:
        return "✅ 选好澄清 Agent 和工作区后，点「🚀 开始澄清」即用所选配置在对应工作区澄清。"
    if status in (C.S_DEV, C.S_REVIEW, C.S_MERGE):
        return ("⚠️ 已进入开发/Review：切换工作区**不会迁移**已建好的工作区，"
                "改 Agent 仅对之后阶段生效。要换工作区重做，建议先「重新澄清」。")
    if status == C.S_BLOCKED:
        return "提示：当前已阻塞；改完 Agent/工作区后用「解除阻塞」回到对应阶段才会生效。"
    if status in (C.S_CLARIFY, C.S_ANSWER, C.S_CONFIRM):
        return "✅ 现在设置最稳妥：开发尚未开始，选择会在后续阶段生效。"
    return ""


def settings_card(rec: dict, workspace_keys: list[str] | None = None) -> dict:
    """交互式配置卡片：点按选择执行 Agent / 工作区，点完原地刷新；待确认时附带「确认开发」。"""
    f = _record_fields(rec)
    rid = rec.get("record_id", "")
    status = f.get(C.F_STATUS)
    cur_agent = f.get(C.F_AGENT) or "默认"
    cur_ws = f.get(C.F_WORKSPACE) or "默认"
    elements: list[dict] = [
        _fields(("状态", status or "-"), ("当前 Agent", cur_agent), ("当前工作区", cur_ws)),
        _md("**执行 Agent**（点按即生效，对该需求所有阶段生效）"),
        _action_row([
            _button(a, "set_agent", rid, "primary" if a == cur_agent else "default", agent=a)
            for a in AGENT_CHOICES
        ]),
    ]
    keys = workspace_keys or []
    if keys:
        elements.append(_md("**工作区**"))
        elements.append(_action_row([
            _button(k, "set_workspace", rid, "primary" if k == cur_ws else "default", workspace=k)
            for k in keys[:9]
        ]))
    hint = _settings_hint(status)
    if hint:
        elements.append(_md(f"<font color='grey'>{hint}</font>"))
    if status == C.S_SETUP:
        elements.append({"tag": "hr"})
        elements.append(_action_row([_button("🚀 开始澄清", "start_clarify", rid)]))
    elif status == C.S_CONFIRM:
        elements.append({"tag": "hr"})
        elements.append(_action_row([_button("✅ 确认开发", "confirm", rid)]))
    title_prefix = "新需求 · 选择澄清配置" if status == C.S_SETUP else "配置"
    return {
        "config": {"wide_screen_mode": True},
        "header": _header(f"{title_prefix}：{_title(rec)}", status),
        "elements": elements,
    }


def done_toast_card(title: str, note: str, template: str = "grey") -> dict:
    """点完按钮后用来"替换"原卡片，把按钮去掉、显示结果。"""
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title[:80]}, "template": template},
        "elements": [_md(note)],
    }
