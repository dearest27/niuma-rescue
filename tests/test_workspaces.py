from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import config as C
import workspaces


class WorkspacesTest(unittest.TestCase):
    def test_parse_workspace_token_removes_token_and_keeps_text(self) -> None:
        text, key = workspaces.parse_workspace_token("#backend-service：修复登录按钮")

        self.assertEqual(text, "修复登录按钮")
        self.assertEqual(key, "backend-service")

    def test_parse_workspace_token_ignores_normal_hash_text(self) -> None:
        text, key = workspaces.parse_workspace_token("修复 #1 登录按钮")

        self.assertEqual(text, "修复 #1 登录按钮")
        self.assertIsNone(key)

    def test_workspace_can_use_inline_work_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            cfg = path / "workspaces.json"
            cfg.write_text(json.dumps({
                "default": "inline-app",
                "items": {
                    "inline-app": {
                        "path": "/repo/inline-app",
                        "scm": "git",
                        "work_mode": "inline",
                    }
                },
            }), encoding="utf-8")
            old = C.WORKSPACES_FILE
            C.WORKSPACES_FILE = cfg
            try:
                ws = workspaces.get("inline-app")
            finally:
                C.WORKSPACES_FILE = old

        self.assertEqual(ws.work_mode, "inline")


if __name__ == "__main__":
    unittest.main()
