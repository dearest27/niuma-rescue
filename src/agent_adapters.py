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

    # 子类可覆盖：cursor 用 stream-json argv / 专用 sink，其余引擎用默认。
    def _argv(self) -> list[str]:
        return self.prepare_argv()

    def _new_sink(self):
        return _RawSink()

    def run(self, prompt: str, cwd: Path, timeout: int, log: LogFn | None = None,
            on_progress: ProgressFn | None = None) -> AgentResult:
        """统一执行路径：Popen + 读取线程 + 看门狗。
        - 总超时兜底；
        - 无输出超 INACTIVITY_TIMEOUT 即判卡死杀掉重试——但**出过首行输出后才武装**，
          所以"沉默到结束才吐结果"的引擎(如 claude 缓冲模式)不会被误杀，退回总超时；
        - 按时间触发 on_progress 心跳（"还在跑 Xs"），所有引擎/阶段都有反馈。"""
        argv = self._argv()
        logger = log or (lambda _msg: None)
        inactivity = C.INACTIVITY_TIMEOUT
        health.emit("dispatcher", "agent_start", engine=self.engine, cwd=str(cwd),
                    timeout=timeout, inactivity=inactivity)
        logger(f"  调用 {self.engine}: {' '.join(argv)} "
               f"(cwd={cwd}, timeout={timeout}s, 静默上限={inactivity}s)…")
        try:
            proc = subprocess.Popen(
                argv, cwd=str(cwd),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1, env=self.env(),
            )
        except FileNotFoundError:
            health.emit("dispatcher", "agent_command_missing", engine=self.engine, command=argv[0])
            logger(f"  找不到命令 `{argv[0]}` —— 该引擎 CLI 没装或不在 PATH")
            return AgentResult(False, f"command not found: {argv[0]}", command=tuple(argv))
        try:
            if proc.stdin:
                proc.stdin.write(prompt)
                proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass

        sink = self._new_sink()
        last_prog = [0.0]

        def on_tick(elapsed: float) -> None:
            if on_progress and elapsed - last_prog[0] >= 1.0:
                last_prog[0] = elapsed
                try:
                    on_progress({**sink.progress(), "elapsed": round(elapsed)})
                except Exception:
                    pass

        returncode, killed, duration = _pump(proc, timeout, inactivity, sink.feed, on_tick)

        if killed:
            event = "agent_inactive_kill" if killed == "inactivity" else "agent_timeout"
            health.emit("dispatcher", event, engine=self.engine, timeout=timeout,
                        inactivity=inactivity, duration=round(duration, 1))
            if killed == "inactivity":
                logger(f"  {self.engine} 静默超过 {inactivity}s（疑似卡死），终止并重试")
                msg = f"{self.engine} 无输出超过 {inactivity}s（疑似卡死），已终止"
            else:
                logger(f"  {self.engine} 超时（{timeout}s）")
                msg = f"{self.engine} timed out after {timeout}s"
            return AgentResult(False, msg, returncode=None, duration=duration, command=tuple(argv))

        text = sink.final_text()
        blob = text or sink.error_blob()
        health.emit("dispatcher", "agent_done", engine=self.engine, returncode=returncode,
                    duration=round(duration, 1), output_len=len(blob))
        logger(f"  {self.engine} 退出码={returncode}，耗时 {duration:.0f}s，输出 {len(blob)} 字")
        if getattr(sink, "is_error", None):
            result = AgentResult(False, f"{self.engine} 报错: {blob[:300]}", returncode=returncode)
        else:
            result = self.validate(returncode, blob)
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
class _RawSink:
    """非流式引擎的默认 sink：stdout 当回复、stderr 当错误兜底，按行收集。"""

    out_lines: list[str] = field(default_factory=list)
    err_lines: list[str] = field(default_factory=list)
    is_error: bool | None = None

    def feed(self, tag: str, line: str) -> None:
        (self.err_lines if tag == "err" else self.out_lines).append(line)

    def progress(self) -> dict:
        return {"output_len": sum(len(x) for x in self.out_lines)}

    def final_text(self) -> str:
        return "".join(self.out_lines).strip()

    def error_blob(self) -> str:
        return "".join(self.out_lines) + "".join(self.err_lines)


def _pump(proc: subprocess.Popen, timeout: int, inactivity: int,
          on_line: Callable[[str, str], None], on_tick: Callable[[float], None]):
    """读 proc 的 stdout/stderr 喂 on_line(tag,line)；周期 on_tick(elapsed) 做时间心跳。
    总超时杀；**出过首行输出后**才按无活跃超时杀（避免误杀沉默到底才出结果的引擎）。
    返回 (returncode, killed, duration)，killed ∈ {None,'inactivity','timeout'}。"""
    q: queue.Queue = queue.Queue()

    def _reader(stream, tag):
        try:
            for line in stream:
                q.put((tag, line))
        finally:
            q.put((tag, None))  # EOF 哨兵

    for stream, tag in ((proc.stdout, "out"), (proc.stderr, "err")):
        threading.Thread(target=_reader, args=(stream, tag), daemon=True).start()

    started = time.time()
    last_activity = started
    last_tick = 0.0
    eofs = 0
    seen_output = False
    killed = None
    while True:
        now = time.time()
        if now - started > timeout:
            killed = "timeout"
            break
        if seen_output and inactivity > 0 and now - last_activity > inactivity:
            killed = "inactivity"
            break
        if now - last_tick >= 1.0:
            last_tick = now
            try:
                on_tick(now - started)
            except Exception:
                pass
        try:
            tag, line = q.get(timeout=1.0)
        except queue.Empty:
            continue
        if line is None:
            eofs += 1
            if eofs >= 2:  # stdout + stderr 都 EOF → 输出结束
                break
            continue
        last_activity = now
        seen_output = True
        on_line(tag, line)

    if killed:
        _terminate(proc)
    else:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _terminate(proc)
    return proc.returncode, killed, time.time() - started


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

    def error_blob(self) -> str:
        return "\n".join(self.error_lines)


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

    def _argv(self) -> list[str]:
        return self.stream_argv()

    def _new_sink(self):
        return _CursorStream()


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
