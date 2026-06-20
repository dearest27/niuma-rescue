from __future__ import annotations

import unittest
from pathlib import Path

import review_utils


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
