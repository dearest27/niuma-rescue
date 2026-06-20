"""Agent CLI adapters.

Each adapter owns how a specific local agent is invoked and how its output is
classified. Dispatcher keeps orchestration state; adapters keep engine quirks.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import config as C
import health


LogFn = Callable[[str], None]
ProgressFn = Callable[[dict], None]


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

    def run(self, prompt: str, cwd: Path, timeout: int, log: LogFn | None = None,
            on_progress: ProgressFn | None = None) -> AgentResult:
        # 基类非流式：一次性 capture_output，on_progress 无从触发（流式引擎自行覆盖 run）。
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


@dataclass
class _CursorStream:
    """累积 cursor `--output-format stream-json` 的事件，重建终态文本 + 进度计数。"""

    result_text: str | None = None
    is_error: bool | None = None          # result 事件的 is_error；None=未见 result
    assistant_text: list[str] = field(default_factory=list)
    error_lines: list[str] = field(default_factory=list)
    events: int = 0
    thinking: int = 0
    tool_calls: int = 0

    def feed(self, tag: str, line: str) -> None:
        if tag == "err":
            s = line.strip()
            if s:
                self.error_lines.append(s)
            return
        s = line.strip()
        if not s:
            return
        if not s.startswith("{"):
            # cursor 偶尔直接打纯文本（非 JSON 行），当作回复文本收着。
            self.assistant_text.append(line.rstrip("\n"))
            return
        try:
            d = json.loads(s)
        except json.JSONDecodeError:
            self.assistant_text.append(line.rstrip("\n"))
            return
        t = d.get("type")
        self.events += 1
        if t == "thinking":
            self.thinking += 1
        elif t == "tool_call":
            if d.get("subtype") == "started":
                self.tool_calls += 1
        elif t == "assistant":
            for blk in d.get("message", {}).get("content", []):
                if blk.get("type") == "text" and (blk.get("text") or "").strip():
                    self.assistant_text.append(blk["text"])
        elif t == "result":
            self.result_text = d.get("result")
            self.is_error = bool(d.get("is_error"))

    def progress(self) -> dict:
        return {"events": self.events, "thinking": self.thinking, "tool_calls": self.tool_calls}

    def final_text(self) -> str:
        if self.result_text and self.result_text.strip():
            return self.result_text
        return "\n".join(self.assistant_text).strip()


def _terminate(proc: subprocess.Popen) -> None:
    for step in (proc.terminate, proc.kill):
        try:
            step()
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue
        except Exception:
            return


class CursorAdapter(AgentAdapter):
    error_markers = AgentAdapter.error_markers + (
        "cursor agent is not authenticated",
        "login required",
        "workspace is not trusted",
    )

    def stream_argv(self) -> list[str]:
        """把 --output-format 的值换成 stream-json：边跑边吐事件，才能按活跃度判卡死。"""
        argv = self.prepare_argv()
        for i, a in enumerate(argv):
            if a == "--output-format" and i + 1 < len(argv):
                argv[i + 1] = "stream-json"
                return argv
        return argv + ["--output-format", "stream-json"]

    def run(self, prompt: str, cwd: Path, timeout: int, log: LogFn | None = None,
            on_progress: ProgressFn | None = None) -> AgentResult:
        inactivity = C.INACTIVITY_TIMEOUT
        if inactivity <= 0:
            # 看门狗关闭 → 退回基类非流式行为。
            return super().run(prompt, cwd, timeout, log=log)

        argv = self.stream_argv()
        logger = log or (lambda _msg: None)
        health.emit("dispatcher", "agent_start", engine=self.engine, cwd=str(cwd),
                    timeout=timeout, inactivity=inactivity)
        logger(f"  调用 {self.engine}(stream): {' '.join(argv)} "
               f"(cwd={cwd}, timeout={timeout}s, 静默上限={inactivity}s)…")
        started = time.time()
        try:
            proc = subprocess.Popen(
                argv, cwd=str(cwd),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1, env=self.env(),
            )
        except FileNotFoundError:
            health.emit("dispatcher", "agent_command_missing", engine=self.engine, command=argv[0])
            logger(f"  找不到命令 `{argv[0]}` —— cursor CLI 没装或不在 PATH")
            return AgentResult(False, f"command not found: {argv[0]}", command=tuple(argv))

        try:
            if proc.stdin:
                proc.stdin.write(prompt)
                proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass

        q: queue.Queue = queue.Queue()

        def _reader(stream, tag):
            try:
                for line in stream:
                    q.put((tag, line))
            finally:
                q.put((tag, None))  # EOF 哨兵

        for stream, tag in ((proc.stdout, "out"), (proc.stderr, "err")):
            threading.Thread(target=_reader, args=(stream, tag), daemon=True).start()

        state = _CursorStream()
        last_activity = time.time()
        last_progress = 0.0
        eofs = 0
        killed = None  # "inactivity" | "timeout"
        while True:
            now = time.time()
            if now - started > timeout:
                killed = "timeout"
                break
            if now - last_activity > inactivity:
                killed = "inactivity"
                break
            try:
                tag, line = q.get(timeout=min(5, inactivity))
            except queue.Empty:
                continue
            if line is None:
                eofs += 1
                if eofs >= 2:  # stdout + stderr 都到 EOF → 进程输出结束
                    break
                continue
            last_activity = now
            state.feed(tag, line)
            if on_progress and now - last_progress >= 1.0:
                last_progress = now
                try:
                    on_progress({**state.progress(), "elapsed": round(now - started)})
                except Exception:
                    pass

        duration = time.time() - started
        if killed:
            _terminate(proc)
            event = "agent_inactive_kill" if killed == "inactivity" else "agent_timeout"
            health.emit("dispatcher", event, engine=self.engine, timeout=timeout,
                        inactivity=inactivity, duration=round(duration, 1),
                        events=state.events, tool_calls=state.tool_calls)
            if killed == "inactivity":
                logger(f"  {self.engine} 静默超过 {inactivity}s（疑似卡死，已收到 {state.events} 个事件），"
                       f"终止并重试")
                msg = f"{self.engine} 无输出超过 {inactivity}s（疑似卡死），已终止"
            else:
                logger(f"  {self.engine} 超时（{timeout}s）")
                msg = f"{self.engine} timed out after {timeout}s"
            return AgentResult(False, msg, returncode=None, duration=duration, command=tuple(argv))

        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _terminate(proc)
        returncode = proc.returncode
        text = state.final_text()
        blob = text or "\n".join(state.error_lines)
        health.emit("dispatcher", "agent_done", engine=self.engine, returncode=returncode,
                    duration=round(duration, 1), output_len=len(blob),
                    events=state.events, tool_calls=state.tool_calls)
        logger(f"  {self.engine} 退出码={returncode}，耗时 {duration:.0f}s，"
               f"{state.events} 事件/{state.tool_calls} 工具，输出 {len(blob)} 字")

        if state.is_error:
            result = AgentResult(False, f"{self.engine} 报错: {blob[:300]}", returncode=returncode)
        else:
            result = self.validate(returncode, blob)
        result.duration = duration
        result.command = tuple(argv)
        return result


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


def run_agent(engine: str, prompt: str, cwd: Path, timeout: int, log: LogFn | None = None,
              on_progress: ProgressFn | None = None) -> AgentResult:
    return get_adapter(engine).run(prompt, cwd, timeout, log=log, on_progress=on_progress)
