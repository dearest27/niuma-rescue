from __future__ import annotations

import unittest

import health


def _events(now: float) -> list[dict]:
    return [
        {"ts": now - 100, "event": "agent_done", "engine": "cursor", "duration": 40},
        {"ts": now - 200, "event": "agent_done", "engine": "cursor", "duration": 60},
        {"ts": now - 300, "event": "agent_inactive_kill", "engine": "cursor"},
        {"ts": now - 350, "event": "agent_timeout", "engine": "cursor"},
        {"ts": now - 400, "event": "gate_done", "ok": True},
        {"ts": now - 500, "event": "gate_done", "ok": False},
        {"ts": now - 600, "event": "transition", "to": "完成"},
        {"ts": now - 700, "event": "transition", "to": "已阻塞"},
        {"ts": now - 3 * 3600, "event": "agent_done", "engine": "cursor", "duration": 999},  # 窗口外
    ]


class SummaryTest(unittest.TestCase):
    def test_aggregates_within_window_only(self) -> None:
        now = 1_000_000.0
        s = health.summary(1.0, events=_events(now), now=now)
        self.assertEqual(s["agent_calls"], 2)          # 3h 前那条被滤掉
        self.assertEqual(s["avg_duration"], 50.0)
        self.assertEqual(s["by_engine"], {"cursor": 2})
        self.assertEqual(s["inactive_kills"], 1)
        self.assertEqual(s["timeouts"], 1)
        self.assertEqual(s["gate_ok"], 1)
        self.assertEqual(s["gate_fail"], 1)
        self.assertEqual(s["transitions"].get("完成"), 1)
        self.assertEqual(s["transitions"].get("已阻塞"), 1)

    def test_summary_text_renders_key_numbers(self) -> None:
        now = 1_000_000.0
        t = health.summary_text(1.0, events=_events(now), now=now)
        self.assertIn("运行报表", t)
        self.assertIn("卡死自愈 1", t)
        self.assertIn("完成 1", t)

    def test_empty_is_safe(self) -> None:
        s = health.summary(24.0, events=[], now=1000.0)
        self.assertEqual(s["agent_calls"], 0)
        self.assertEqual(s["avg_duration"], 0.0)
        self.assertEqual(s["transitions"], {})


if __name__ == "__main__":
    unittest.main()
