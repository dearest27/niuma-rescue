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


class RawSinkTest(unittest.TestCase):
    def test_stdout_is_answer_stderr_is_error_fallback(self) -> None:
        s = agent_adapters._RawSink()
        s.feed("out", "答案第一行\n")
        s.feed("out", "答案第二行\n")
        s.feed("err", "some warning\n")
        self.assertEqual(s.final_text(), "答案第一行\n答案第二行")
        self.assertIn("some warning", s.error_blob())
        self.assertIsNone(s.is_error)

    def test_progress_reports_output_len(self) -> None:
        s = agent_adapters._RawSink()
        s.feed("out", "abcd")
        self.assertEqual(s.progress()["output_len"], 4)


class CursorStreamTest(unittest.TestCase):
    def _feed(self, lines, tag="out"):
        s = agent_adapters._CursorStream()
        for ln in lines:
            s.feed(tag, ln)
        return s

    def test_result_event_is_final_text(self) -> None:
        s = self._feed([
            '{"type":"system","subtype":"init"}',
            '{"type":"thinking"}',
            '{"type":"assistant","message":{"content":[{"type":"text","text":"半句"}]}}',
            '{"type":"result","subtype":"success","is_error":false,"result":"CLEAR\\nPRD"}',
        ])
        self.assertEqual(s.final_text(), "CLEAR\nPRD")
        self.assertFalse(s.is_error)
        self.assertEqual(s.thinking, 1)

    def test_falls_back_to_assistant_text_without_result(self) -> None:
        s = self._feed([
            '{"type":"assistant","message":{"content":[{"type":"text","text":"答案"}]}}',
        ])
        self.assertIsNone(s.is_error)
        self.assertEqual(s.final_text(), "答案")

    def test_counts_tool_calls_started_only(self) -> None:
        s = self._feed([
            '{"type":"tool_call","subtype":"started"}',
            '{"type":"tool_call","subtype":"completed"}',
            '{"type":"tool_call","subtype":"started"}',
        ])
        self.assertEqual(s.tool_calls, 2)
        self.assertEqual(s.progress()["tool_calls"], 2)

    def test_stderr_and_nonjson_lines_are_tolerated(self) -> None:
        s = agent_adapters._CursorStream()
        s.feed("err", "Error: [aborted] socket disconnected")
        s.feed("out", "plain text reply")
        self.assertIn("socket disconnected", "\n".join(s.error_lines))
        self.assertEqual(s.final_text(), "plain text reply")

    def test_stream_argv_swaps_output_format(self) -> None:
        argv = agent_adapters.CursorAdapter("cursor").stream_argv()
        self.assertIn("stream-json", argv)
        self.assertNotIn("text", argv)


if __name__ == "__main__":
    unittest.main()
