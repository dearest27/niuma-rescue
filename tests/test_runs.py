from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import config as C
import health
import runs


class RunsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmp.name)
        self._old_state_dir = health.STATE_DIR
        self._old_events_file = health.EVENTS_FILE
        self._old_db_path = runs.DB_PATH
        health.STATE_DIR = self.state_dir
        health.EVENTS_FILE = self.state_dir / "events.jsonl"
        runs.DB_PATH = self.state_dir / "runs.sqlite3"

    def tearDown(self) -> None:
        runs.DB_PATH = self._old_db_path
        health.STATE_DIR = self._old_state_dir
        health.EVENTS_FILE = self._old_events_file
        self.tmp.cleanup()

    def test_claim_blocks_duplicate_processing(self) -> None:
        first = runs.claim("rec_1", "handle_develop", C.S_DEV, "title")
        second = runs.claim("rec_1", "handle_develop", C.S_DEV, "title")

        self.assertTrue(first.ok)
        self.assertFalse(second.ok)
        self.assertEqual(second.reason, "busy")
        self.assertEqual(second.run_id, first.run_id)

    def test_failed_run_respects_retry_wait_then_manual_clear(self) -> None:
        first = runs.claim("rec_1", "handle_develop", C.S_DEV, "title")
        runs.fail(first.run_id, "temporary", retry_delay=60)

        waiting = runs.claim("rec_1", "handle_develop", C.S_DEV, "title")
        self.assertFalse(waiting.ok)
        self.assertEqual(waiting.reason, "retry_wait")

        self.assertTrue(runs.retry_now("rec_1", "test retry"))
        next_claim = runs.claim("rec_1", "handle_develop", C.S_DEV, "title")
        self.assertTrue(next_claim.ok)
        self.assertNotEqual(next_claim.run_id, first.run_id)


if __name__ == "__main__":
    unittest.main()
