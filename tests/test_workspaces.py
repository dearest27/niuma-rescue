from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
