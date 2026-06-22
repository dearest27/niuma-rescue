"""Route realtime Feishu text messages into the requirement state machine."""

import re

import cards
import config as C
import health
import lark
import ops
import workspaces


def _ws_keys() -> list[str]:
    try:
        return [w.key for w in workspaces.list_workspaces()]
    except Exception:
        return []


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
    if action == "start_clarify":
        if status != C.S_SETUP:
            return f"当前状态「{status}」，无需开始澄清", False, None
        lark.update(rid, {C.F_STATUS: C.S_CLARIFY})
        agent = f.get(C.F_AGENT) or C.ENGINE_CLARIFY
        ws = f.get(C.F_WORKSPACE) or "默认"
        return ("🚀 开始澄清", True,
                cards.done_toast_card(f"🚀 开始澄清：{title}",
                                      f"将用 {agent} 在工作区 {ws} 澄清，请稍候。", "wathet"))
    if action == "open_settings":
        return "打开配置", False, cards.settings_card(rec, _ws_keys())
    if action == "set_agent":
        res = ops.set_agent(rec, (value or {}).get("agent", ""))
        return res.message, False, cards.settings_card(rec, _ws_keys())
    if action == "set_workspace":
        key = (value or {}).get("workspace", "")
        try:
            workspaces.get(key)
        except Exception as exc:
            return f"工作区无效：{exc}", False, None
        res = ops.set_workspace(rec, key)
        return res.message, False, cards.settings_card(rec, _ws_keys())
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
看板（所有在途需求一览）
状态（当前会话的需求）
配置（点按选择 Agent / 工作区）
统计 / 周报（运行报表）
诊断（当前需求）
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
需求：修改登录页按钮样式
  发完会先停在「待选择」，弹卡片让你选澄清 Agent + 工作区，点「开始澄清」才开跑。
  也可内联预选：需求@cursor #frontend-app：…（仍会弹卡片，已帮你选好，直接点开始）
  选好后也可直接回「开始澄清」。"""


def _active_record(records: list[dict], chat_id: str) -> dict | None:
    return ops.find_latest_for_chat(
        records,
        chat_id,
        {
            C.S_SETUP, C.S_CLARIFY, C.S_ANSWER, C.S_CONFIRM, C.S_DEV,
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


def _last_log(fields: dict) -> str:
    lines = [line.strip() for line in (fields.get(C.F_LOG) or "").splitlines() if line.strip()]
    return lines[-1] if lines else "-"


def _record_diagnosis_text(rec: dict) -> str:
    fields = rec["fields"]
    return "\n".join([
        f"需求诊断：{fields.get(C.F_TITLE) or rec.get('record_id')}",
        f"· record_id: {rec.get('record_id')}",
        f"· 状态: {fields.get(C.F_STATUS) or '-'}",
        f"· 失败次数: {fields.get(C.F_FAILS) or 0}",
        f"· 工作区: {fields.get(C.F_WORKSPACE) or '默认'}",
        f"· Agent: 澄清={fields.get(C.F_AGENT_CLARIFY) or fields.get(C.F_AGENT) or C.ENGINE_CLARIFY} / "
        f"开发={fields.get(C.F_AGENT_CODE) or fields.get(C.F_AGENT) or C.ENGINE_CODE} / "
        f"Review={fields.get(C.F_AGENT_REVIEW) or fields.get(C.F_AGENT) or C.ENGINE_REVIEW}",
        f"· 最近日志: {_last_log(fields)}",
        "本地深度诊断：python src/pipelinectl.py diagnose " + str(rec.get("record_id")),
    ])


def handle_command(text: str, chat_id: str, records: list[dict]) -> bool | None:
    """Return True/False when a command was handled; None means not a command."""
    normalized = re.sub(r"\s+", " ", text.strip())
    lower = normalized.lower()
    if normalized in {"指令", "帮助", "help", "/help"}:
        lark.send_text(chat_id, _COMMAND_HELP)
        return False
    if normalized in {"看板", "全部", "board", "/board"}:
        _send_card_or_text(chat_id, cards.board_card(records), cards.board_text(records))
        return False
    if normalized in {"开始澄清", "开始", "go"}:
        rec = find_record(records, chat_id, {C.S_SETUP})
        if not rec:
            return None  # 不在待选择语境 → 交给后续（可能是别的输入）
        lark.update(rec["record_id"], {C.F_STATUS: C.S_CLARIFY})
        lark.send_text(chat_id, "🚀 开始澄清。")
        return True
    if normalized in {"配置", "config", "/config"}:
        rec = _active_record(records, chat_id)
        if not rec:
            lark.send_text(chat_id, "当前会话没有进行中的需求。")
            return False
        _send_card_or_text(chat_id, cards.settings_card(rec, _ws_keys()),
                           "回复『切换Agent cursor』或『切换工作区 <key>』来设置。")
        return False
    if normalized in {"统计", "报表", "stats"}:
        lark.send_text(chat_id, health.summary_text(24))
        return False
    if normalized in {"周报", "weekly"}:
        lark.send_text(chat_id, health.summary_text(168))
        return False
    if normalized in {"诊断", "diagnose", "/diagnose"}:
        rec = _active_record(records, chat_id)
        if not rec:
            lark.send_text(chat_id, "当前会话没有进行中的需求。")
            return False
        lark.send_text(chat_id, _record_diagnosis_text(rec))
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


def _normalize_created_record(created: dict, fallback_fields: dict) -> dict:
    """Normalize Feishu create-record response into {record_id, fields}."""
    record = created.get("record") if isinstance(created.get("record"), dict) else created
    record_id = record.get("record_id") or record.get("id")
    if not record_id:
        raise RuntimeError(f"创建需求成功但返回缺少 record_id: {created}")
    return {"record_id": record_id, "fields": record.get("fields") or fallback_fields}


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
            C.F_STATUS: C.S_SETUP,                   # → 待选择：先让人选澄清 Agent + 工作区
            C.F_CHAT: chat_id,
            C.F_OWNER: [{"id": sender}] if sender else None,
        }
        if workspace_key:
            fields[C.F_WORKSPACE] = workspace_key
        if clarify_agent:                            # 内联 @agent 作为预选（执行Agent=全阶段默认）
            fields[C.F_AGENT] = clarify_agent
        created = lark.create(fields)
        rec = _normalize_created_record(created, fields)
        _send_card_or_text(
            chat_id,
            cards.settings_card(rec, _ws_keys()),
            "需求已收到。回复『切换Agent <名>』『切换工作区 <key>』选好后回『开始澄清』。",
        )
        return False                                 # 等人点「开始澄清」，先不跑 dispatcher

    lark.send_text(chat_id, "发「需求：<一句话描述>」给我，就能提交一个新需求开始走流水线。")
    return False
