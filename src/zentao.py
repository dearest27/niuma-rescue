#!/usr/bin/env python3
"""ZenTao bug import helpers.

The first integration step is intentionally one-way: pull ZenTao bugs into
Feishu Base. ZenTao deployments differ a lot, so endpoint/auth are configurable
instead of hard-coded around one server version.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

import config as C


@dataclass(frozen=True)
class ZentaoConfig:
    base_url: str
    bug_endpoint: str = "/api.php/v1/bugs"
    bug_query: dict[str, Any] | None = None
    token: str = ""
    token_env: str = "ZENTAO_TOKEN"
    token_header: str = "Token"
    extra_headers: dict[str, str] | None = None
    workspace: str = ""
    agent: str = ""
    dry_run: bool = True


@dataclass(frozen=True)
class ZentaoBug:
    id: str
    title: str
    status: str = ""
    severity: str = ""
    priority: str = ""
    opened_by: str = ""
    assigned_to: str = ""
    product: str = ""
    project: str = ""
    module: str = ""
    steps: str = ""
    url: str = ""
    raw: dict[str, Any] | None = None


def _root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_config(path: str | Path | None = None) -> ZentaoConfig:
    cfg_path = Path(path or os.getenv("PIPELINE_ZENTAO_FILE") or (_root() / "zentao.json"))
    data: dict[str, Any] = {}
    if cfg_path.exists():
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    base_url = str(os.getenv("ZENTAO_BASE_URL") or data.get("base_url") or "").rstrip("/")
    if not base_url:
        raise SystemExit(f"缺少禅道地址：设置 ZENTAO_BASE_URL 或创建 {cfg_path}")
    token_env = str(data.get("token_env") or "ZENTAO_TOKEN")
    token = str(os.getenv(token_env) or os.getenv("ZENTAO_TOKEN") or data.get("token") or "")
    return ZentaoConfig(
        base_url=base_url,
        bug_endpoint=str(data.get("bug_endpoint") or os.getenv("ZENTAO_BUG_ENDPOINT") or "/api.php/v1/bugs"),
        bug_query=dict(data.get("bug_query") or {}),
        token=token,
        token_env=token_env,
        token_header=str(data.get("token_header") or "Token"),
        extra_headers={str(k): str(v) for k, v in dict(data.get("extra_headers") or {}).items()},
        workspace=str(data.get("workspace") or os.getenv("ZENTAO_WORKSPACE") or ""),
        agent=str(data.get("agent") or os.getenv("ZENTAO_AGENT") or ""),
        dry_run=bool(data.get("dry_run", True)),
    )


def _json_request(url: str, headers: dict[str, str]) -> Any:
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _headers(cfg: ZentaoConfig) -> dict[str, str]:
    headers = {"Accept": "application/json", **(cfg.extra_headers or {})}
    if cfg.token:
        header = cfg.token_header.strip()
        if header.lower() == "authorization":
            headers[header] = cfg.token if cfg.token.lower().startswith("bearer ") else f"Bearer {cfg.token}"
        else:
            headers[header] = cfg.token
    return headers


def _bugs_payload_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    candidates = [
        payload.get("bugs"),
        payload.get("items"),
        payload.get("data", {}).get("bugs") if isinstance(payload.get("data"), dict) else None,
        payload.get("data", {}).get("items") if isinstance(payload.get("data"), dict) else None,
        payload.get("data"),
    ]
    for value in candidates:
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("realname", "account", "name", "title", "id"):
            if value.get(key) is not None:
                return str(value[key])
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return ", ".join(_text(x) for x in value if _text(x))
    return str(value)


def _first(raw: dict[str, Any], *keys: str) -> str:
    for key in keys:
        if raw.get(key) not in (None, ""):
            return _text(raw.get(key)).strip()
    return ""


def normalize_bug(raw: dict[str, Any], base_url: str) -> ZentaoBug | None:
    bug_id = _first(raw, "id", "bugID", "bug_id")
    title = _first(raw, "title", "name", "summary")
    if not bug_id or not title:
        return None
    url = _first(raw, "url", "link", "html_url")
    if not url:
        url = f"{base_url.rstrip('/')}/bug-view-{bug_id}.html"
    return ZentaoBug(
        id=bug_id,
        title=title,
        status=_first(raw, "status"),
        severity=_first(raw, "severity"),
        priority=_first(raw, "pri", "priority"),
        opened_by=_first(raw, "openedBy", "opened_by", "openedByRealname"),
        assigned_to=_first(raw, "assignedTo", "assigned_to", "assignedToRealname"),
        product=_first(raw, "product", "productName"),
        project=_first(raw, "project", "projectName", "execution"),
        module=_first(raw, "module", "moduleName"),
        steps=_first(raw, "steps", "reproSteps", "desc", "description"),
        url=url,
        raw=raw,
    )


def fetch_bugs(cfg: ZentaoConfig) -> list[ZentaoBug]:
    query = urlencode({k: v for k, v in (cfg.bug_query or {}).items() if v not in (None, "")})
    endpoint = cfg.bug_endpoint
    if query:
        endpoint += ("&" if "?" in endpoint else "?") + query
    url = urljoin(cfg.base_url.rstrip("/") + "/", endpoint.lstrip("/"))
    payload = _json_request(url, _headers(cfg))
    bugs = [normalize_bug(item, cfg.base_url) for item in _bugs_payload_items(payload)]
    return [bug for bug in bugs if bug is not None]


def bug_marker(bug: ZentaoBug) -> str:
    return f"【外部来源】zentao bug #{bug.id}"


def bug_description(bug: ZentaoBug) -> str:
    parts = [
        bug_marker(bug),
        f"标题：{bug.title}",
        f"状态：{bug.status or '-'}",
        f"严重程度：{bug.severity or '-'}",
        f"优先级：{bug.priority or '-'}",
        f"指派给：{bug.assigned_to or '-'}",
        f"创建人：{bug.opened_by or '-'}",
        f"产品/项目/模块：{bug.product or '-'} / {bug.project or '-'} / {bug.module or '-'}",
        f"链接：{bug.url or '-'}",
    ]
    if bug.steps:
        parts += ["", "复现步骤 / 描述：", bug.steps]
    return "\n".join(parts).strip()


def base_fields_for_bug(bug: ZentaoBug, cfg: ZentaoConfig) -> dict[str, Any]:
    fields: dict[str, Any] = {
        C.F_TITLE: f"[禅道Bug#{bug.id}] {bug.title}"[:100],
        C.F_DESC: bug_description(bug),
        C.F_STATUS: C.S_SETUP,
        C.F_LOG: f"[zentao] imported bug #{bug.id}",
        C.F_FAILS: 0,
        C.F_EXTERNAL_SOURCE: "zentao",
        C.F_EXTERNAL_ID: bug.id,
        C.F_EXTERNAL_URL: bug.url,
        C.F_EXTERNAL_TYPE: "bug",
        C.F_SYNC_STATUS: "imported",
    }
    if cfg.workspace:
        fields[C.F_WORKSPACE] = cfg.workspace
    if cfg.agent:
        fields[C.F_AGENT] = cfg.agent
    return fields
