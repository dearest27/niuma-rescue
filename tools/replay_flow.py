#!/usr/bin/env python3
"""Offline end-to-end replay for the requirement state machine."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import flow_replay


def main() -> int:
    result = flow_replay.run_nominal_with_review_retry()
    flow_replay.assert_replay_ok(result)
    print(flow_replay.render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
