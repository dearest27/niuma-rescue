#!/usr/bin/env python3
"""Lightweight smoke checks for local deployment.

Default mode is local-only and does not call Feishu or run agents. Use
--feishu / --dispatch when validating a real environment.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))


class Smoke:
    def __init__(self) -> None:
        self.failures = 0
        self.warnings = 0

    def pass_(self, msg: str) -> None:
        print(f"PASS {msg}")

    def warn(self, msg: str) -> None:
        self.warnings += 1
        print(f"WARN {msg}")

    def fail(self, msg: str) -> None:
        self.failures += 1
        print(f"FAIL {msg}")

    def check(self, cond: bool, ok: str, bad: str, *, fatal: bool = True) -> None:
        if cond:
            self.pass_(ok)
        elif fatal:
            self.fail(bad)
        else:
            self.warn(bad)


def _run(argv: list[str], cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True)


def local_checks(smoke: Smoke) -> None:
    smoke.check((ROOT / ".env.example").exists(), ".env.example exists", ".env.example missing")
    smoke.check((SRC / "listener.py").exists(), "listener.py exists", "listener.py missing")
    smoke.check((SRC / "dispatcher.py").exists(), "dispatcher.py exists", "dispatcher.py missing")
    smoke.check((ROOT / "workspaces.example.json").exists(), "workspaces.example.json exists", "workspaces.example.json missing")
    smoke.check((ROOT / "fields.example.json").exists(), "fields.example.json exists", "fields.example.json missing")
    smoke.check((ROOT / "agents.example.json").exists(), "agents.example.json exists", "agents.example.json missing")

    try:
        import config as C
        import health
        import workspaces

        smoke.pass_("core modules import")
        smoke.check(bool(C.AGENT_CMDS), "agent command registry loaded", "agent command registry empty")
        for engine in {C.ENGINE_CLARIFY, C.ENGINE_CODE, C.ENGINE_REVIEW}:
            binary = C.AGENT_CMDS.get(engine, [engine])[0]
            smoke.check(shutil.which(binary) is not None, f"{engine} CLI found ({binary})", f"{engine} CLI not found ({binary})", fatal=False)
        try:
            items = workspaces.list_workspaces()
            smoke.check(bool(items), f"workspace registry readable ({len(items)})", "workspace registry empty", fatal=False)
            for ws in items:
                smoke.check(ws.path.exists(), f"workspace path exists: {ws.key}", f"workspace path missing: {ws.key} -> {ws.path}", fatal=False)
        except Exception as exc:
            smoke.warn(f"workspace registry warning: {exc}")
        latest = health.read_all()
        if latest:
            smoke.pass_(f"local health files readable ({len(latest)})")
        else:
            smoke.warn("no local health heartbeat yet")
    except Exception as exc:
        smoke.fail(f"core module import failed: {exc}")


def feishu_check(smoke: Smoke) -> None:
    try:
        import lark

        rows = lark.list_records()
        smoke.pass_(f"Feishu Base reachable ({len(rows)} records)")
    except Exception as exc:
        smoke.fail(f"Feishu Base check failed: {exc}")


def dispatch_check(smoke: Smoke) -> None:
    proc = _run([sys.executable, "-B", str(SRC / "dispatcher.py"), "--once"], cwd=SRC)
    if proc.returncode == 0:
        smoke.pass_("dispatcher --once completed")
    else:
        out = ((proc.stdout or "") + (proc.stderr or ""))[-800:]
        smoke.fail(f"dispatcher --once failed: {out}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local smoke checks")
    parser.add_argument("--feishu", action="store_true", help="call Feishu OpenAPI and list Base records")
    parser.add_argument("--dispatch", action="store_true", help="run dispatcher.py --once")
    args = parser.parse_args()

    smoke = Smoke()
    local_checks(smoke)
    if args.feishu:
        feishu_check(smoke)
    if args.dispatch:
        dispatch_check(smoke)

    print(f"summary: failures={smoke.failures} warnings={smoke.warnings}")
    return 1 if smoke.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
