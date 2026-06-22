#!/usr/bin/env python3
"""Small helpers for development-stage dispatch decisions."""
from __future__ import annotations

import config as C
import workspaces


def field_text(value) -> str:
    """把飞书字段值归一成字符串，兼容单选/多选/人员等不同返回形状。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return field_text(value[0]) if value else ""
    if isinstance(value, dict):
        for key in ("text", "name", "value", "id"):
            if value.get(key):
                return str(value[key]).strip()
        return ""
    return str(value).strip()


def should_reuse_existing_changes(ws: workspaces.Workspace, fields: dict, changed: list[str]) -> bool:
    if not changed:
        return False
    if getattr(ws, "work_mode", "worktree") != "inline":
        return True
    link = field_text(fields.get(C.F_LINK))
    return link.startswith(f"inline:{ws.key}:")
