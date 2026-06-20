#!/usr/bin/env python3
"""脱钩 hermes 的入站监听：用官方 lark-oapi SDK 直连飞书长连接收消息。

替代 hermes 网关 + pre_gateway_dispatch 插件这条入站路径。
⚠️ 一旦启用本进程，必须停掉 hermes 网关（hermes gateway stop），否则同一个 app
   两个长连接消费者会分流事件，两边都丢消息。

依赖：pip install lark-oapi
运行：python3 listener.py        （常驻；建议用 launchd/pm2/systemd 托管）

⚠️ 本文件基于 lark-oapi 文档化 API 编写，但开发环境无法联网安装/验证。
   首次使用请 pip install lark-oapi 后实跑，按报错微调（属性路径 / 方法名）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path

import config as C  # noqa: F401  (触发 .env 加载，把 FEISHU_* 灌进环境)
import health
import inbox
import lark  # 本地数据/IM 模块（含 patch_card）；main() 里把 lark_oapi 局部别名为 lark，互不影响
import message_router

_PIPELINE_DIR = Path(__file__).resolve().parent

# 飞书可能重投同一条事件（handler 处理慢、未及时 ACK 时）。按 event_id 去重，保证幂等。
_seen_ids: deque = deque(maxlen=2000)
_seen_set: set = set()


def _first_seen(event_id) -> bool:
    """首次见返回 True；重复（飞书重投）返回 False。没 event_id 则放行。"""
    if not event_id:
        return True
    if event_id in _seen_set:
        return False
    _seen_ids.append(event_id)
    _seen_set.add(event_id)
    if len(_seen_ids) == _seen_ids.maxlen:
        _seen_set.intersection_update(_seen_ids)
    return True


# 让子进程脱离父进程，跨平台：Windows 用 creationflags，POSIX 用 start_new_session。
if sys.platform == "win32":
    _DETACH_KW = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
else:
    _DETACH_KW = {"start_new_session": True}


def _trigger_dispatch() -> None:
    """异步起一个 dispatcher --once（不阻塞监听）；并发由 dispatcher 的文件锁防护。"""
    try:
        (_PIPELINE_DIR.parent / "logs").mkdir(exist_ok=True)
        logf = open(_PIPELINE_DIR.parent / "logs" / "dispatcher.log", "a", encoding="utf-8")
        health.emit("listener", "trigger_dispatch")
        subprocess.Popen(
            [sys.executable, "-B", "dispatcher.py", "--once"],
            cwd=str(_PIPELINE_DIR),
            stdout=logf, stderr=logf,
            **_DETACH_KW,
        )
        logf.close()  # 子进程已持有 fd 副本
    except Exception as e:
        health.emit("listener", "trigger_dispatch_failed", error=str(e))
        print(f"[listener] 触发 dispatcher 失败: {e}", file=sys.stderr)

try:
    import lark_oapi as lark
except ImportError:
    sys.exit("缺少 lark-oapi：请先 pip install lark-oapi")


def _on_message(data) -> None:
    """收到一条 IM 消息：转成 message_router 认的 msg dict 并处理。

    lark-oapi 的 text 消息 content 是 JSON 串 {"text": "..."}，需解析；
    我们的 message_router.handle_message 期望 content 是渲染后的纯文本。
    """
    try:
        eid = getattr(getattr(data, "header", None), "event_id", None)
        if not _first_seen(eid):
            return                   # 飞书重投，已处理过 → 幂等跳过
        m = data.event.message
        if getattr(m, "message_type", None) != "text":
            return
        text = json.loads(m.content or "{}").get("text", "").strip()
        sender = None
        if getattr(data.event, "sender", None) and data.event.sender.sender_id:
            sender = data.event.sender.sender_id.open_id
        msg = {
            "message_type": "text",
            "chat_id": m.chat_id,
            "content": text,
            "sender_id": sender,
            "message_id": getattr(m, "message_id", None),
        }
        inbox_id, inserted = inbox.enqueue(eid, msg)
        health.emit("listener", "message_received", chat_id=m.chat_id, text=text[:200], event_id=eid, inbox_id=inbox_id, inserted=inserted)
        print(f"[listener] recv chat={m.chat_id} inbox={inbox_id} inserted={inserted} text={text[:80]!r}", file=sys.stderr)
        # 消息已落本地 inbox，后台线程消费；_on_message 立刻返回 → 飞书快速 ACK。
        if inserted:
            threading.Thread(target=_drain_inbox, daemon=True).start()
    except Exception as e:  # 单条出错不拖垮监听
        health.emit("listener", "message_parse_failed", error=str(e))
        print(f"[listener] 处理消息异常: {e}", file=sys.stderr)


def _drain_inbox() -> None:
    processed = inbox.process_pending(message_router.handle_message, _trigger_dispatch)
    health.emit("listener", "inbox_drained", processed=processed)


def _on_card_action(data):
    """卡片按钮点击回调：推进状态 + 弹 toast。走同一条 WS 长连接，无需公网 URL。"""
    from lark_oapi.event.callback.model.p2_card_action_trigger import (
        P2CardActionTriggerResponse, CallBackToast,
    )
    toast_text = "已处理"
    try:
        ev = getattr(data, "event", None)
        action = getattr(ev, "action", None) if ev else None
        value = (getattr(action, "value", None) or {}) if action else {}
        ctx = getattr(ev, "context", None) if ev else None
        message_id = getattr(ctx, "open_message_id", None) if ctx else None
        toast_text, should_dispatch, new_card = message_router.handle_card_action(value)
        health.emit("listener", "card_action", value=value, dispatch=should_dispatch)
        print(f"[listener] card_action {value} → {toast_text}", file=sys.stderr)
        if new_card and message_id:          # 原地更新卡片（确认/完成/配置选择都靠它生效）
            try:
                lark.patch_card(message_id, new_card)
            except Exception as e:
                print(f"[listener] 卡片更新失败: {e}", file=sys.stderr)
        if should_dispatch:
            _trigger_dispatch()
    except Exception as e:
        health.emit("listener", "card_action_failed", error=str(e))
        print(f"[listener] 卡片回调异常: {e}", file=sys.stderr)
        toast_text = "处理失败"
    resp = P2CardActionTriggerResponse()
    t = CallBackToast()
    t.type = "info"
    t.content = toast_text
    resp.toast = t
    return resp


def main() -> None:
    app_id = os.getenv("FEISHU_APP_ID")
    app_secret = os.getenv("FEISHU_APP_SECRET")
    if not (app_id and app_secret):
        health.emit("listener", "startup_failed", error="missing FEISHU_APP_ID/FEISHU_APP_SECRET")
        sys.exit("缺少 FEISHU_APP_ID/FEISHU_APP_SECRET（项目 .env）")

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(_on_message)
        .register_p2_card_action_trigger(_on_card_action)   # 卡片按钮点击 → 推进状态
        # 应用订阅了"消息已读"等事件但我们不处理，注册空 handler 免得刷 ERROR 日志
        .register_p2_im_message_message_read_v1(lambda data: None)
        .build()
    )
    client = lark.ws.Client(
        app_id,
        app_secret,
        event_handler=handler,
        log_level=lark.LogLevel.INFO,
    )
    health.emit("listener", "starting", event_key=C.EVENT_KEY)
    print("[listener] 连接飞书长连接，监听 im.message.receive_v1 …", file=sys.stderr)
    client.start()  # 阻塞常驻；断线自动重连由 SDK 处理


if __name__ == "__main__":
    main()
