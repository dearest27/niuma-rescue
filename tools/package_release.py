#!/usr/bin/env python3
"""Build a clean release zip for delivery.

Usage:
  python tools/package_release.py
  python tools/package_release.py 0.1.0
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = sys.argv[1] if len(sys.argv) > 1 else "0.1.0"
OUT = ROOT / "dist" / f"agent-pipeline-v{VERSION}.zip"

EXCLUDE_DIRS = {
    ".git",
    ".claude",
    ".venv",
    "__pycache__",
    "hermes-plugin",
    "logs",
    "state",
    "worktrees",
    "dist",
    "scripts/windows",
}
EXCLUDE_PATH_PREFIXES = {
    "scripts/hermes",
    "scripts/windows",
}
EXCLUDE_FILES = {
    ".env",
    ".DS_Store",
    ".dispatcher.lock",
    "agents.json",
    "fields.json",
    "pipeline_realtime.py",
    "workspaces.json",
    "zentao.json",
    "_run_tests_tmp.py",
}
EXCLUDE_SUFFIXES = {
    ".pyc",
    ".pyo",
}


def should_include(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    rel_posix = rel.as_posix()
    parts = rel.parts
    if any(part in EXCLUDE_DIRS for part in parts):
        return False
    if any(rel_posix.startswith(prefix + "/") for prefix in EXCLUDE_PATH_PREFIXES):
        return False
    if path.name in EXCLUDE_FILES:
        return False
    if path.suffix in EXCLUDE_SUFFIXES:
        return False
    return True


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    files = [p for p in ROOT.rglob("*") if p.is_file() and should_include(p)]
    with zipfile.ZipFile(OUT, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(files):
            arcname = Path(f"agent-pipeline-v{VERSION}") / path.relative_to(ROOT)
            zf.write(path, arcname)
    print(f"created {OUT}")
    print(f"files: {len(files)}")


if __name__ == "__main__":
    main()
