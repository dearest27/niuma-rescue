"""Route realtime Feishu text messages into the requirement state machine."""

import re

import cards
import config as C
import lark
import ops
import workspaces


def handle_card_action(value: dict) -> tuple[str, bool, dict | None]:
    """处理卡片按钮点击。返回 (toast 文案, 是否触发 dispatch, 用于替换原卡片的新卡片或 None)。"""
    rid = (value or {}).get("record_id")
    action = (value or {}).get("action")
    if not rid:
        return "无效操作", False, None
    rec = next((r for r in lark.list_records() if r["record_id"] == rid), None)
    if not rec:
        return "记录不存在或已删除", False, None
    f = rec["fields"]
    status = f.get(C.F_STATUS)
    title = f.get(C.F_TITLE) or ""
    if action == "confirm":
        if status != C.S_CONFIRM:
            return f"当前状态「{status}」，无需确认", False, None
        lark.update(rid, {C.F_STATUS: C.S_DEV})
        return ("✅ 已确认，开始开发", True,
                cards.done_toast_card(f"✅ 已确认：{title}", "已进入开发，完成后同步进度。", "blue"))
    if action == "done":
        if status != C.S_MERGE:
            return f"当前状态「{status}」", False, None
        lark.update(rid, {C.F_STATUS: C.S_DONE})
        return ("✅ 已标记完成", False,
                cards.done_toast_card(f"✅ 已完成：{title}", "需求已收尾。", "green"))
    manual_actions = {
        "retry": lambda: ops.retry_record(rec),
        "clear_lock": lambda: ops.clear_lock(rec),
        "restart_clarify": lambda: ops.restart_clarify(rec),
        "unblock_dev": lambda: ops.unblock_record(rec, C.S_DEV),
        "mark_done": lambda: ops.mark_done(rec),
    }
    if action in manual_actions:
        result = manual_actions[action]()
        template = "green" if result.ok else "yellow"
        return (
            result.message,
            result.dispatch,
            cards.done_toast_card(("✅ " if result.ok else "⚠️ ") + title, result.message, template),
        )
    return "未知操作", False, None

_INTAKE_RE = re.compile(
    r"^需求(?:\s*@(?P<agent>[A-Za-z][\w -]*))?\s*[：:]?\s*(?P<body>.*)$",
    re.DOTALL,
)


def _normalize_agent(raw: str | None) -> str | None:
    if not raw:
        return None
    value = raw.strip()
    engine = C.AGENT_ALIASES.get(value) or C.AGENT_ALIASES.get(value.lower())
    if engine:
        return engine
    compact = value.lower().replace("-", " ")
    return C.AGENT_ALIASES.get(compact)


def parse_intake(text: str) -> tuple[str, str | None, str | None] | None:
    match = _INTAKE_RE.match(text)
    if not match:
        return None
    body = match.group("body").strip(" ：:")
    body, workspace_key = workspaces.parse_workspace_token(body)
    body = body.strip(" ：:")
    agent = _normalize_agent(match.group("agent"))
    return body, agent, workspace_key


def find_record(records: list[dict], chat_id: str, statuses: set) -> dict | None:
    for r in records:
        f = r["fields"]
        if f.get(C.F_CHAT) == chat_id and f.get(C.F_STATUS) in statuses:
            return r
    return None


def append_clarify(rec: dict, answer: str) -> None:
    f = rec["fields"]
    merged = ((f.get(C.F_CLARIFY) or "") + "\n\n【回答】" + answer).strip()
    lark.update(rec["record_id"], {C.F_CLARIFY: merged, C.F_STATUS: C.S_CLARIFY})


_COMMAND_HELP = """可用指令：
状态
重试
清锁
解除阻塞 [待澄清|开发中|Review中]
重新澄清
完成
切换Agent <claude|codex|gemini|cursor>
切换澄清Agent <agent>
切换开发Agent <agent>
切换ReviewAgent <agent>
切换工作区 <workspace>
设置状态 <状态>

新需求格式：
需求@cursor #frontend-app：修改登录页按钮样式"""


def _active_record(records: list[dict], chat_id: str) -> dict | None:
    return ops.find_latest_for_chat(
        records,
        chat_id,
        {
            C.S_CLARIFY, C.S_ANSWER, C.S_CONFIRM, C.S_DEV,
            C.S_REVIEW, C.S_MERGE, C.S_BLOCKED,
        },
    )


def _send_op_result(chat_id: str, result: ops.OpResult) -> None:
    prefix = "✅" if result.ok else "⚠️"
    lark.send_text(chat_id, f"{prefix} {result.message}")


def _send_card_or_text(chat_id: str, card: dict, fallback: str) -> None:
    try:
        lark.send_card(chat_id, card)
    except Exception:
        lark.send_text(chat_id, fallback)


def handle_command(text: str, chat_id: str, records: list[dict]) -> bool | None:
    """Return True/False when a command was handled; None means not a command."""
    normalized = re.sub(r"\s+", " ", text.strip())
    lower = normalized.lower()
    if normalized in {"指令", "帮助", "help", "/help"}:
        lark.send_text(chat_id, _COMMAND_HELP)
        return False
    if normalized == "状态":
        rec = _active_record(records, chat_id)
        if not rec:
            lark.send_text(chat_id, "当前会话没有进行中的需求。")
            return False
        f = rec["fields"]
        fallback = (
            "当前需求：\n"
            f"标题：{f.get(C.F_TITLE) or rec['record_id']}\n"
            f"状态：{f.get(C.F_STATUS)}\n"
            f"工作区：{f.get(C.F_WORKSPACE) or '-'}\n"
            f"链接：{f.get(C.F_LINK) or '-'}\n"
            f"失败次数：{f.get(C.F_FAILS) or 0}"
        )
        _send_card_or_text(
            chat_id,
            cards.status_card(rec),
            fallback,
        )
        return False

    command_patterns = (
        r"^(重试)$",
        r"^(清锁)$",
        r"^(解除阻塞)(?: (?P<unblock_status>待澄清|开发中|Review中))?$",
        r"^(重新澄清)$",
        r"^(完成|标记完成)$",
        r"^(?:切换|设置)(?P<agent_stage>澄清|开发|Review)?Agent (?P<agent>[A-Za-z][\w -]*)$",
        r"^(?:切换|设置)工作区 (?P<workspace>[A-Za-z0-9_.-]+)$",
        r"^设置状态 (?P<status>待澄清|待回答|待确认|开发中|Review中|待合并|完成|已阻塞)$",
    )
    match = None
    for pattern in command_patterns:
        match = re.match(pattern, normalized, flags=re.IGNORECASE)
        if match:
            break
    if not match:
        return None

    rec = _active_record(records, chat_id)
    if not rec:
        lark.send_text(chat_id, "当前会话没有可操作的需求。")
        return False

    try:
        if lower == "重试":
            result = ops.retry_record(rec)
        elif lower == "清锁":
            result = ops.clear_lock(rec)
        elif match.group(1) == "解除阻塞":
            result = ops.unblock_record(rec, match.groupdict().get("unblock_status") or C.S_DEV)
        elif lower == "重新澄清":
            result = ops.restart_clarify(rec)
        elif lower in {"完成", "标记完成"}:
            result = ops.mark_done(rec)
        elif match.groupdict().get("agent"):
            stage_name = match.groupdict().get("agent_stage")
            stage = {"澄清": "clarify", "开发": "code", "Review": "review", "review": "review"}.get(stage_name or "")
            result = ops.set_agent(rec, match.group("agent"), stage=stage)
        elif match.groupdict().get("workspace"):
            workspace_key = match.group("workspace")
            workspaces.get(workspace_key)
            result = ops.set_workspace(rec, workspace_key)
        else:
            result = ops.set_status(rec, match.group("status"))
    except Exception as exc:
        lark.send_text(chat_id, f"⚠️ 指令执行失败：{exc}")
        return False

    _send_op_result(chat_id, result)
    return result.dispatch


def _is_missing_field_error(exc: Exception, field_name: str) -> bool:
    text = str(exc)
    return (
        "FieldNameNotFound" in text
        or f"fields.{field_name}" in text
        or field_name in text
    )


def _create_requirement(fields: dict, clarify_agent: str | None) -> dict:
    """Create intake record, falling back when optional agent columns are absent."""
    if clarify_agent:
        fields[C.F_AGENT_CLARIFY] = clarify_agent
    try:
        return lark.create(fields)
    except Exception as exc:
        if not clarify_agent or not _is_missing_field_error(exc, C.F_AGENT_CLARIFY):
            raise

    fallback = dict(fields)
    fallback.pop(C.F_AGENT_CLARIFY, None)
    marker = f"【{C.F_AGENT_CLARIFY}】{clarify_agent}"
    fallback[C.F_CLARIFY] = ((fallback.get(C.F_CLARIFY) or "") + "\n" + marker).strip()
    return lark.create(fallback)


def handle_message(msg: dict) -> bool:
    """处理一条飞书消息。返回 True 表示记录进入了"机器该处理"的状态
    （待澄清/开发中），listener 据此立刻触发一次 dispatcher --once。"""
    if msg.get("message_type") != "text":
        return False
    chat_id = msg.get("chat_id")
    text = (msg.get("content") or "").strip()
    sender = msg.get("sender_id")
    if not chat_id or not text:
        return False

    records = lark.list_records()

    command_result = handle_command(text, chat_id, records)
    if command_result is not None:
        return command_result

    rec = find_record(records, chat_id, C.HUMAN_INPUT)
    if rec:
        status = rec["fields"].get(C.F_STATUS)
        if status == C.S_ANSWER:
            append_clarify(rec, text)               # → 待澄清
            lark.send_text(chat_id, "👌 收到，我再看看还需不需要补充。")
            return True
        if status == C.S_CONFIRM:
            if any(w.lower() in text.lower() for w in C.CONFIRM_WORDS):
                lark.update(rec["record_id"], {C.F_STATUS: C.S_DEV})  # → 开发中
                lark.send_text(chat_id, "🚀 已确认，开始开发，完成后我会同步进度。")
            else:
                append_clarify(rec, text)           # → 待澄清
                lark.send_text(chat_id, "已记录你的补充，我再过一遍。")
            return True
        return False

    intake = parse_intake(text)
    if intake:
        body, clarify_agent, workspace_key = intake
        # 幂等：同会话 + 同描述已有在途记录（非完成/已阻塞）→ 判为飞书重投，跳过建记录。
        # 比内存 event_id 去重更稳：扛得住 listener 重启和重投风暴。
        if any(r["fields"].get(C.F_CHAT) == chat_id
               and (r["fields"].get(C.F_DESC) or "") == body
               and r["fields"].get(C.F_STATUS) not in (C.S_DONE, C.S_BLOCKED)
               for r in records):
            return False
        fields = {
            C.F_TITLE: body[:30],
            C.F_DESC: body,
            C.F_STATUS: C.S_CLARIFY,                 # → 待澄清
            C.F_CHAT: chat_id,
            C.F_OWNER: [{"id": sender}] if sender else None,
        }
        if workspace_key:
            fields[C.F_WORKSPACE] = workspace_key
        created = _create_requirement(fields, clarify_agent)
        suffix_parts = []
        if clarify_agent:
            suffix_parts.append(f"澄清 Agent：{clarify_agent}")
        if workspace_key:
            suffix_parts.append(f"工作区：{workspace_key}")
        suffix = "（" + "，".join(suffix_parts) + "）" if suffix_parts else ""
        card_fields = created.get("fields", fields) if isinstance(created, dict) else fields
        _send_card_or_text(
            chat_id,
            cards.intake_card(card_fields),
            f"需求已收到{suffix}，我先澄清一下细节，稍等。",
        )
        return True

    lark.send_text(chat_id, "发「需求：<一句话描述>」给我，就能提交一个新需求开始走流水线。")
    return False
