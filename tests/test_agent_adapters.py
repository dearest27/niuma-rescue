from __future__ import annotations

import os
import unittest

import agent_adapters


class AgentAdapterTest(unittest.TestCase):
    def test_adapter_types_are_registered_for_supported_engines(self) -> None:
        self.assertEqual(
            sorted(agent_adapters.ADAPTER_TYPES),
            ["claude", "codex", "cursor", "gemini"],
        )
        self.assertIsInstance(agent_adapters.get_adapter("cursor"), agent_adapters.CursorAdapter)

    def test_validate_rejects_empty_output_and_error_markers(self) -> None:
        adapter = agent_adapters.ClaudeAdapter("claude")

        self.assertFalse(adapter.validate(0, "").ok)
        auth = adapter.validate(0, "Claude Code is not authenticated. Please run /login")
        self.assertFalse(auth.ok)
        self.assertIn("疑似错误", auth.output)
        self.assertFalse(adapter.validate(2, "boom").ok)

    def test_validate_accepts_normal_output(self) -> None:
        result = agent_adapters.CursorAdapter("cursor").validate(0, "CLEAR\nPRD content")

        self.assertTrue(result.ok)
        self.assertEqual(result.output, "CLEAR\nPRD content")

    def test_env_scrubs_agent_auth_pollution(self) -> None:
        old = dict(os.environ)
        try:
            os.environ["ANTHROPIC_BASE_URL"] = "https://example.invalid"
            os.environ["CLAUDE_CODE_TOKEN"] = "secret"
            os.environ["SAFE_VALUE"] = "kept"

            env = agent_adapters.AgentAdapter("cursor").env()

            self.assertNotIn("ANTHROPIC_BASE_URL", env)
            self.assertNotIn("CLAUDE_CODE_TOKEN", env)
            self.assertEqual(env.get("SAFE_VALUE"), "kept")
        finally:
            os.environ.clear()
            os.environ.update(old)


if __name__ == "__main__":
    unittest.main()
