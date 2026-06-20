"""Agent CLI adapters.

Each adapter owns how a specific local agent is invoked and how its output is
classified. Dispatcher keeps orchestration state; adapters keep engine quirks.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import config as C
import health


LogFn = Callable[[str], None]


@dataclass
class AgentResult:
    ok: bool
    output: str
    returncode: int | None = None
    duration: float = 0.0
    command: tuple[str, ...] = ()


class AgentAdapter:
    """Base adapter for stdin-driven headless CLIs."""

    error_markers: tuple[str, ...] = (
        "invalid authentication",
        "failed to authenticate",
        "authentication failed",
        "not authenticated",
        "api error",
        "401",
        "rate limit",
        "quota",
    )

    def __init__(self, engine: str):
        self.engine = engine

    def command(self) -> list[str]:
        return list(C.AGENT_CMDS[self.engine])

    def env(self) -> dict[str, str]:
        return {
            k: v for k, v in os.environ.items()
            if k not in C.SCRUB_ENV_KEYS
            and not any(k.startswith(p) for p in C.SCRUB_ENV_PREFIXES)
        }

    def prepare_argv(self) -> list[str]:
        argv = self.command()
        exe = shutil.which(argv[0])
        if exe:
            argv[0] = exe
        return argv

    def validate(self, returncode: int, output: str) -> AgentResult:
        if returncode != 0:
            return AgentResult(False, output, returncode=returncode)
        if not output.strip():
            return AgentResult(False, f"{self.engine} 返回空输出", returncode=returncode)
        lower = output.lower()
        for marker in self.error_markers:
            if marker in lower:
                return AgentResult(False, f"{self.engine} 输出疑似错误: {output[:300]}", returncode=returncode)
        return AgentResult(True, output, returncode=returncode)

    def run(self, prompt: str, cwd: Path, timeout: int, log: LogFn | None = None) -> AgentResult:
        argv = self.prepare_argv()
        logger = log or (lambda _msg: None)
        health.emit("dispatcher", "agent_start", engine=self.engine, cwd=str(cwd), timeout=timeout)
        logger(f"  调用 {self.engine}: {' '.join(argv)} (cwd={cwd}, timeout={timeout}s)…")
        started = time.time()
        try:
            proc = subprocess.run(
                argv,
                cwd=str(cwd),
                input=prompt,
                text=True,
                capture_output=True,
                timeout=timeout,
                env=self.env(),
            )
        except subprocess.TimeoutExpired:
            health.emit("dispatcher", "agent_timeout", engine=self.engine, timeout=timeout)
            logger(f"  {self.engine} 超时（{timeout}s）")
            return AgentResult(False, f"{self.engine} timed out after {timeout}s")
        except FileNotFoundError:
            health.emit("dispatcher", "agent_command_missing", engine=self.engine, command=argv[0])
            logger(f"  找不到命令 `{argv[0]}` —— 该引擎 CLI 没装或不在 PATH")
            return AgentResult(False, f"command not found: {argv[0]}", command=tuple(argv))

        output = (proc.stdout or "") + (proc.stderr or "")
        duration = time.time() - started
        health.emit(
            "dispatcher",
            "agent_done",
            engine=self.engine,
            returncode=proc.returncode,
            duration=round(duration, 1),
            output_len=len(output),
        )
        logger(f"  {self.engine} 退出码={proc.returncode}，耗时 {duration:.0f}s，输出 {len(output)} 字")
        result = self.validate(proc.returncode, output)
        result.duration = duration
        result.command = tuple(argv)
        return result


class ClaudeAdapter(AgentAdapter):
    error_markers = AgentAdapter.error_markers + (
        "claude code is not authenticated",
        "please run /login",
        "anthropic_auth_token",
    )


class CodexAdapter(AgentAdapter):
    error_markers = AgentAdapter.error_markers + (
        "not logged in",
        "please login",
        "approval denied",
    )


class GeminiAdapter(AgentAdapter):
    error_markers = AgentAdapter.error_markers + (
        "please login",
        "google api key",
        "permission denied",
    )


class CursorAdapter(AgentAdapter):
    error_markers = AgentAdapter.error_markers + (
        "cursor agent is not authenticated",
        "login required",
        "workspace is not trusted",
    )


ADAPTER_TYPES: dict[str, type[AgentAdapter]] = {
    "claude": ClaudeAdapter,
    "codex": CodexAdapter,
    "gemini": GeminiAdapter,
    "cursor": CursorAdapter,
}


def get_adapter(engine: str) -> AgentAdapter:
    if engine not in C.AGENT_CMDS:
        allowed = ", ".join(sorted(C.AGENT_CMDS))
        raise ValueError(f"unknown agent `{engine}`; allowed: {allowed}")
    cls = ADAPTER_TYPES.get(engine, AgentAdapter)
    return cls(engine)


def run_agent(engine: str, prompt: str, cwd: Path, timeout: int, log: LogFn | None = None) -> AgentResult:
    return get_adapter(engine).run(prompt, cwd, timeout, log=log)
