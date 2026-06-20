"""飞书 OpenAPI 数据层：多维表格记录 + IM 文本消息。

生产链路不再依赖 lark-cli，避免后台 Hermes 进程里的 profile/keychain
问题。凭据从环境变量或 ~/.hermes/.env 中读取：
  - FEISHU_APP_ID
  - FEISHU_APP_SECRET
"""
import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import config as C

_REC = f"/open-apis/bitable/v1/apps/{C.BASE_TOKEN}/tables/{C.TABLE_ID}/records"
_FEISHU_BASE_URL = "https://open.feishu.cn"
_TOKEN: str | None = None
_TOKEN_EXP: float = 0.0          # token 过期时间戳（已含提前量），常驻进程靠它自动刷新


def _load_hermes_env() -> None:
    """Load Feishu app credentials from ~/.hermes/.env when not inherited."""
    if os.getenv("FEISHU_APP_ID") and os.getenv("FEISHU_APP_SECRET"):
        return
    env_path = Path.home() / ".hermes" / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in {"FEISHU_APP_ID", "FEISHU_APP_SECRET"} and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def _tenant_access_token() -> str:
    global _TOKEN, _TOKEN_EXP
    if _TOKEN and time.time() < _TOKEN_EXP:   # 未过期直接用，过期/将过期则重取
        return _TOKEN
    _load_hermes_env()
    app_id = os.getenv("FEISHU_APP_ID")
    app_secret = os.getenv("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        raise RuntimeError("缺少 FEISHU_APP_ID/FEISHU_APP_SECRET，无法直接调用飞书 OpenAPI")

    body = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode("utf-8")
    req = Request(
        _FEISHU_BASE_URL + "/open-apis/auth/v3/tenant_access_token/internal",
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    data = _http_json(req)
    token = data.get("tenant_access_token")
    if not token:
        raise RuntimeError(f"获取 tenant_access_token 失败: {data}")
    _TOKEN = token
    _TOKEN_EXP = time.time() + data.get("expire", 7200) - 300   # 提前 5 分钟刷新
    return token


def _http_json(req: Request) -> dict:
    try:
        with urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Feishu HTTP {e.code}: {body[:300]}")


def _api(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = Request(
        _FEISHU_BASE_URL + path,
        data=data,
        headers={
            "Authorization": f"Bearer {_tenant_access_token()}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method=method,
    )
    d = _http_json(req)
    if d.get("code") not in (0, None) or d.get("ok") is False:
        raise RuntimeError(f"feishu api {method} {path} 失败: {d}")
    return d.get("data", d)


def list_records() -> list[dict]:
    """返回 [{record_id, fields:{名:值}}, ...]。MVP 不分页（量小）；
    需要分页时加 ?page_size=&page_token= 并跟 has_more/page_token。"""
    return _api("GET", _REC).get("items", [])


def update(record_id: str, fields: dict) -> dict:
    return _api("PUT", f"{_REC}/{record_id}", {"fields": fields})


def create(fields: dict) -> dict:
    return _api("POST", _REC, {"fields": fields})


def delete(record_id: str) -> dict:
    return _api("DELETE", f"{_REC}/{record_id}")


# ── 飞书 IM 消息（发文本到会话）──────────────────────────────────────
def send_text(chat_id: str, text: str) -> None:
    """给指定会话发一条纯文本消息。"""
    query = urlencode({"receive_id_type": "chat_id"})
    payload = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    _api("POST", f"/open-apis/im/v1/messages?{query}", payload)


def send_card(chat_id: str, card: dict) -> str | None:
    """给指定会话发一张交互卡片（含按钮）。返回 message_id（用于后续原地更新）。"""
    query = urlencode({"receive_id_type": "chat_id"})
    payload = {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    }
    data = _api("POST", f"/open-apis/im/v1/messages?{query}", payload)
    return data.get("message_id")


def patch_card(message_id: str, card: dict) -> None:
    """原地更新一张已发出的交互卡片（用于实时进度，不刷屏）。"""
    _api("PATCH", f"/open-apis/im/v1/messages/{message_id}",
         {"content": json.dumps(card, ensure_ascii=False)})
