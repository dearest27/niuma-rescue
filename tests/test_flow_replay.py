from __future__ import annotations

import unittest

import config as C
import flow_replay


class FlowReplayTest(unittest.TestCase):
    def test_nominal_replay_reaches_done_and_exercises_review_retry(self) -> None:
        result = flow_replay.run_nominal_with_review_retry()

        flow_replay.assert_replay_ok(result)
        self.assertEqual(result.record["fields"][C.F_STATUS], C.S_DONE)
        self.assertTrue(any(s.status_from == C.S_REVIEW and s.status_to == C.S_DEV for s in result.steps))

    def test_render_includes_final_status(self) -> None:
        text = flow_replay.render(flow_replay.run_nominal_with_review_retry())

        self.assertIn("final_status: 完成", text)
        self.assertIn("Review中 -> 开发中", text)


if __name__ == "__main__":
    unittest.main()
