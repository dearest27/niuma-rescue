#!/usr/bin/env python3
"""Workspace registry for agent-pipeline.

Each requirement can target a named local workspace. The name is safe to expose
in Feishu Base, while absolute paths stay in local config.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import config as C

_WORKSPACE_TOKEN_RE = re.compile(r"(?:^|\s)#(?P<key>[A-Za-z0-9_.-]+)(?=\s|[：:]|$)")


@dataclass(frozen=True)
class Workspace:
    key: str
    path: Path
    base_ref: str = "origin/main"
    scm: str = "git"
    test_cmd: str = ""
    code_exts: tuple[str, ...] = ()
    push_enabled: bool = False
    pr_enabled: bool = False
    pr_provider: str = "github"   # github | gitlab | none（svn 忽略此项）
    gh_repo: str = ""             # 可选：PR 目标仓库 org/repo（github）

    @property
    def safe_key(self) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", self.key).strip("._-") or "default"


def _load_raw() -> dict[str, Any]:
    if not C.WORKSPACES_FILE.exists():
        return {}
    try:
        return json.loads(C.WORKSPACES_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"读取 workspaces.json 失败: {exc}") from exc


def _default_key(raw: dict[str, Any]) -> str:
    value = raw.get("default")
    if isinstance(value, str) and value.strip():
        return value.strip()
    items = raw.get("items")
    if isinstance(items, dict) and items:
        return next(iter(items))
    return "default"


def _fallback_workspace(key: str = "default") -> Workspace:
    return Workspace(
        key=key,
        path=C.REPO_PATH,
        base_ref=C.BASE_REF,
        test_cmd=C.TEST_CMD,
        code_exts=C.CODE_EXTS,
        push_enabled=C.PUSH_ENABLED,
        pr_enabled=C.PR_ENABLED,
    )


def get(key: str | None = None) -> Workspace:
    raw = _load_raw()
    items = raw.get("items") if isinstance(raw.get("items"), dict) else {}
    selected = (key or "").strip() or _default_key(raw)
    if not items:
        return _fallback_workspace(selected)
    cfg = items.get(selected)
    if cfg is None:
        allowed = ", ".join(sorted(items))
        raise ValueError(f"未知工作区 `{selected}`，可选：{allowed}")
    if isinstance(cfg, str):
        cfg = {"path": cfg}
    if not isinstance(cfg, dict):
        raise ValueError(f"工作区 `{selected}` 配置必须是对象或路径字符串")
    path = Path(str(cfg.get("path") or "")).expanduser()
    if not path:
        raise ValueError(f"工作区 `{selected}` 缺少 path")
    code_exts_raw = cfg.get("code_exts")
    if isinstance(code_exts_raw, str):
        code_exts = tuple(x.strip() for x in code_exts_raw.split(",") if x.strip())
    elif isinstance(code_exts_raw, list):
        code_exts = tuple(str(x).strip() for x in code_exts_raw if str(x).strip())
    else:
        code_exts = C.CODE_EXTS
    return Workspace(
        key=selected,
        path=path,
        base_ref=str(cfg.get("base") or C.BASE_REF),
        scm=str(cfg.get("scm") or "git"),
        test_cmd=str(cfg.get("test_cmd") if cfg.get("test_cmd") is not None else C.TEST_CMD),
        code_exts=code_exts,
        push_enabled=bool(cfg.get("push_enabled", C.PUSH_ENABLED)),
        pr_enabled=bool(cfg.get("pr_enabled", C.PR_ENABLED)),
        pr_provider=str(cfg.get("pr_provider") or "github").lower(),
        gh_repo=str(cfg.get("gh_repo") or C.GH_REPO),
    )


def list_workspaces() -> list[Workspace]:
    raw = _load_raw()
    items = raw.get("items") if isinstance(raw.get("items"), dict) else {}
    if not items:
        return [get(None)]
    return [get(key) for key in sorted(items)]


def parse_workspace_token(text: str) -> tuple[str, str | None]:
    """Return (text_without_workspace_token, workspace_key)."""
    match = _WORKSPACE_TOKEN_RE.search(text)
    if not match:
        return text, None
    key = match.group("key")
    cleaned = (text[: match.start()] + " " + text[match.end() :]).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned, key


def ensure_example() -> None:
    if C.WORKSPACES_FILE.exists():
        return
    default_name = C.REPO_PATH.name or "default"
    data = {
        "default": default_name,
        "items": {
            default_name: {
                "path": str(C.REPO_PATH),
                "scm": "git",
                "base": C.BASE_REF,
                "test_cmd": C.TEST_CMD,
            }
        },
    }
    C.WORKSPACES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
