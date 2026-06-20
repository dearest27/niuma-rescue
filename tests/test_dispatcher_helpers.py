from __future__ import annotations

import unittest
from pathlib import Path

import review_utils
import routing_utils


class ClarifyRoutingTest(unittest.TestCase):
    def test_clean_clear_routes_to_clear(self) -> None:
        v, payload = routing_utils.route_clarify("CLEAR\n\n## 背景\n做一件事")
        self.assertEqual(v, "CLEAR")
        self.assertTrue(payload.startswith("## 背景"))

    def test_preamble_before_clear_still_routes_clear(self) -> None:
        # agent 在 CLEAR 前多写了一行前言——旧逻辑会误判成问题，新逻辑要容忍
        out = "先确认根目录 README.md 是否存在，再判断是否可输出 CLEAR。\nCLEAR\n\n## 背景\n内容"
        v, payload = routing_utils.route_clarify(out)
        self.assertEqual(v, "CLEAR")
        self.assertEqual(payload, "## 背景\n内容")

    def test_questions_routes_to_questions(self) -> None:
        v, _ = routing_utils.route_clarify("QUESTIONS\n1. 放在哪里？")
        self.assertEqual(v, "QUESTIONS")

    def test_no_marker_defaults_to_questions(self) -> None:
        v, _ = routing_utils.route_clarify("我觉得需求挺清楚的，可以开始。")
        self.assertEqual(v, "QUESTIONS")

    def test_clear_mentioned_in_prose_does_not_false_trigger(self) -> None:
        # 句中提到 CLEAR 但不在行首 → 不应误判为 CLEAR
        v, _ = routing_utils.route_clarify("这个需求还不能直接 CLEAR，我有几个疑问\nQUESTIONS\n1. ?")
        self.assertEqual(v, "QUESTIONS")


class ReviewVerdictTest(unittest.TestCase):
    def test_pass_first_line(self) -> None:
        self.assertEqual(routing_utils.review_verdict("PASS\n无阻塞项"), "PASS")

    def test_preamble_before_pass(self) -> None:
        self.assertEqual(routing_utils.review_verdict("我看了改动。\nPASS\n建议项：无"), "PASS")

    def test_fail_when_unclear(self) -> None:
        self.assertEqual(routing_utils.review_verdict("看起来还行但我不太确定"), "FAIL")


class DispatcherHelpersTest(unittest.TestCase):
    def test_review_failure_summary_strips_fail_header_and_keeps_report_path(self) -> None:
        summary = review_utils.review_failure_summary(
            "FAIL\n\n阻塞项\n- README.md:1 缺少必要说明\n\n建议项\n- 补充验证步骤",
            Path("/tmp/review.md"),
        )

        self.assertNotIn("FAIL\n", summary)
        self.assertIn("阻塞项", summary)
        self.assertIn("README.md:1", summary)
        self.assertIn("完整 Review 报告：/tmp/review.md", summary)

    def test_review_failure_summary_truncates_long_output(self) -> None:
        summary = review_utils.review_failure_summary(
            "FAIL\n" + ("x" * 2000),
            Path("/tmp/review.md"),
            limit=80,
        )

        self.assertIn("已截断", summary)
        self.assertIn("完整 Review 报告：/tmp/review.md", summary)


if __name__ == "__main__":
    unittest.main()
