#!/usr/bin/env python3
"""需求流水线 dispatcher：飞书 Base 状态机 → 多家 agent CLI。

状态机（粗体=人工卡点，dispatcher 不碰）：
  待澄清 ─clarify→ 待回答* | 待确认*
  待确认* ─人确认→ 开发中
  开发中 ─code+测试→ Review中 | (失败计数→已阻塞)
  Review中 ─review→ 待合并* | (打回开发中/已阻塞)
  待合并* ─人 merge→ 完成

dispatcher 只在 待澄清 / 开发中 / Review中 上动作。

用法：
  python dispatcher.py --once     # 跑一轮就退出（配 cron / hermes cron）
  python dispatcher.py            # 常驻轮询（POLL_INTERVAL 秒一轮）

设计原则：agent 无状态，上下文从工件（dossier 文件 + git diff）重建；
测试/review 是客观裁判，不信 agent 自述；同一记录一轮只处理一次（幂等）。
"""
import filelock          # 跨平台文件锁（替代 Unix-only 的 fcntl）
import shutil
import subprocess
import sys
import time
import re
from pathlib import Path

import cards
import config as C
import health
import lark
import runs
import scm
import workspaces

PROMPTS = Path(__file__).parent / "prompts"

# 手动模式开关：pipe.py 会把 VERBOSE 打开，把 agent 的输入/输出/状态流转全打到终端。
VERBOSE = False


def _v(*a):
    if VERBOSE:
        print(*a)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _short(text: object, limit: int = 240) -> str:
    value = str(text)
    return value if len(value) <= limit else value[:limit] + "..."


def workspace_for(rec: dict) -> workspaces.Workspace:
    key = _field_text(rec["fields"].get(C.F_WORKSPACE))
    return workspaces.get(key or None)


# ── 工具：状态推进 + 日志（一次写入，避免竞态）──────────────────────
def advance(rec: dict, status: str, log_line: str, **extra) -> None:
    """把记录推进到新状态，并把一行日志追加到执行日志。状态+日志+extra 一次写完。"""
    f = rec["fields"]
    current = f.get(C.F_STATUS)
    if status != current and status not in C.VALID_TRANSITIONS.get(current, set()):
        raise RuntimeError(f"非法状态流转：{current} -> {status}")
    new_log = ((f.get(C.F_LOG) or "") + log_line + "\n").strip() + "\n"
    fields = {C.F_STATUS: status, C.F_LOG: new_log, **extra}
    _v(f"  状态流转: {f.get(C.F_STATUS)} → {status}"
       + (f" · 写入 {[k for k in extra]}" if extra else ""))
    lark.update(rec["record_id"], fields)
    f.update(fields)  # 保持内存副本一致


def on_failure(rec: dict, msg: str, retry_status: str | None = None) -> None:
    """失败计数 +1；达到上限 → 已阻塞转人工，否则留在当前/指定状态等下轮重试。"""
    fails = int(rec["fields"].get(C.F_FAILS) or 0) + 1
    line = f"[fail#{fails}] {msg}"
    if fails >= C.FAILURE_LIMIT:
        advance(rec, C.S_BLOCKED, line + " → 已阻塞", **{C.F_FAILS: fails})
        notify(rec["fields"].get(C.F_CHAT),
               f"⚠️ 需求「{rec['fields'].get(C.F_TITLE) or ''}」已阻塞，需人工介入。\n原因：{msg[:300]}")
    elif retry_status and retry_status != rec["fields"].get(C.F_STATUS):
        advance(rec, retry_status, line + f" → 回到{retry_status}", **{C.F_FAILS: fails})
    else:
        f = rec["fields"]
        new_log = ((f.get(C.F_LOG) or "") + line + "\n").strip() + "\n"
        lark.update(rec["record_id"], {C.F_FAILS: fails, C.F_LOG: new_log})
        f.update({C.F_FAILS: fails, C.F_LOG: new_log})


def _retry_delay(fails: int) -> int:
    """Small exponential backoff, capped at 15 minutes."""
    fails = max(1, fails)
    return min(C.RETRY_BASE_DELAY * (2 ** (fails - 1)), 15 * 60)


def build_prompt(stage: str, **kw) -> str:
    """读 prompts/<stage>.md，把 {{key}} 占位符替换成实参。"""
    tpl = (PROMPTS / f"{stage}.md").read_text(encoding="utf-8")
    for k, v in kw.items():
        tpl = tpl.replace("{{" + k + "}}", str(v))
    return tpl


def _agent_env() -> dict:
    """给 agent 子进程一个干净环境：剔除会污染引擎认证的变量（见 config 说明）。"""
    import os
    env = {k: v for k, v in os.environ.items()
           if k not in C.SCRUB_ENV_KEYS
           and not any(k.startswith(p) for p in C.SCRUB_ENV_PREFIXES)}
    return env


def _field_text(value) -> str:
    """把飞书字段值归一成字符串，兼容单选/多选/人员等不同返回形状。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return _field_text(value[0]) if value else ""
    if isinstance(value, dict):
        for key in ("text", "name", "value", "id"):
            if value.get(key):
                return str(value[key]).strip()
        return ""
    return str(value).strip()


def _agent_marker(fields: dict, stage: str) -> str:
    """Read agent choice stored in text fields when optional Base columns are absent."""
    labels = {
        "clarify": C.F_AGENT_CLARIFY,
        "code": C.F_AGENT_CODE,
        "review": C.F_AGENT_REVIEW,
    }
    text = "\n".join(
        _field_text(fields.get(name))
        for name in (C.F_CLARIFY, C.F_LOG)
        if fields.get(name)
    )
    match = re.search(rf"【{re.escape(labels[stage])}】\s*([A-Za-z][\w -]*)", text)
    return match.group(1).strip() if match else ""


def resolve_agent(rec: dict, stage: str, default_engine: str) -> str:
    """按记录字段选择 agent：阶段字段 > 执行Agent > 默认配置。"""
    stage_fields = {
        "clarify": C.F_AGENT_CLARIFY,
        "code": C.F_AGENT_CODE,
        "review": C.F_AGENT_REVIEW,
    }
    fields = rec["fields"]
    raw = (
        _field_text(fields.get(stage_fields[stage]))
        or _field_text(fields.get(C.F_AGENT))
        or _agent_marker(fields, stage)
    )
    if not raw:
        return default_engine
    engine = C.AGENT_ALIASES.get(raw) or C.AGENT_ALIASES.get(raw.lower()) or raw.lower()
    if engine not in C.AGENT_CMDS:
        allowed = ", ".join(sorted(C.AGENT_CMDS))
        raise ValueError(f"未知 agent `{raw}`，可选：{allowed}")
    return engine


def run_agent(engine: str, prompt: str, cwd: Path, timeout: int = C.AGENT_TIMEOUT):
    """调对应引擎的 headless CLI，prompt 走 stdin。返回 (是否成功, 输出文本)。"""
    if engine not in C.AGENT_CMDS:
        allowed = ", ".join(sorted(C.AGENT_CMDS))
        return False, f"unknown agent `{engine}`; allowed: {allowed}"
    argv = list(C.AGENT_CMDS[engine])
    exe = shutil.which(argv[0])      # Windows 上把 cursor-agent → cursor-agent.cmd/.exe
    if exe:
        argv[0] = exe
    health.emit("dispatcher", "agent_start", engine=engine, cwd=str(cwd), timeout=timeout)
    log(f"  调用 {engine}: {' '.join(argv)} (cwd={cwd}, timeout={timeout}s)…")
    t0 = time.time()
    try:
        p = subprocess.run(argv, cwd=str(cwd), input=prompt, text=True,
                           capture_output=True, timeout=timeout, env=_agent_env())
    except subprocess.TimeoutExpired:
        health.emit("dispatcher", "agent_timeout", engine=engine, timeout=timeout)
        log(f"  {engine} 超时（{timeout}s）")
        return False, f"{engine} timed out after {timeout}s"
    except FileNotFoundError:
        health.emit("dispatcher", "agent_command_missing", engine=engine, command=argv[0])
        log(f"  找不到命令 `{argv[0]}` —— 该引擎 CLI 没装或不在 PATH")
        return False, f"command not found: {argv[0]}"
    out = (p.stdout or "") + (p.stderr or "")
    duration = time.time() - t0
    health.emit("dispatcher", "agent_done", engine=engine, returncode=p.returncode, duration=round(duration, 1), output_len=len(out))
    log(f"  {engine} 退出码={p.returncode}，耗时 {duration:.0f}s，输出 {len(out)} 字")
    # 注意：claude -p 等 CLI 在认证失败时仍可能返回退出码 0，所以光看 returncode 不够。
    # 这里再做两道防线：输出为空、或命中已知错误特征，都判为失败。
    # TODO: 更稳的做法是各引擎用结构化输出（claude --output-format json 看 is_error）。
    err_markers = ("Invalid authentication", "Failed to authenticate",
                   "API Error", "401", "rate limit", "quota")
    if p.returncode != 0:
        return False, out
    if not out.strip():
        return False, f"{engine} 返回空输出"
    if any(m in out for m in err_markers):
        return False, f"{engine} 输出疑似错误: {out[:300]}"
    return True, out


def git(*args, cwd=None, check=True):
    return subprocess.run(["git", *args], cwd=str(cwd) if cwd else None,
                          capture_output=True, text=True, check=check)


# ── 工件 / worktree ─────────────────────────────────────────────────
def ensure_worktree(req_id: str, ws: workspaces.Workspace):
    """为需求准备（或复用）独立工作区 + 分支。git=worktree，svn=工作副本。幂等。"""
    p = scm.prepare(ws, req_id, C.WORKTREE_BASE / ws.safe_key)
    return p.work_path, p.branch


def dossier_dir(wt: Path, req_id: str) -> Path:
    d = wt / C.DOSSIER_DIR / f"REQ-{req_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_dossier(wt: Path, req_id: str, fields: dict) -> Path:
    """把表里的 PRD/需求落成分支内工件，供 coder 读取。"""
    d = dossier_dir(wt, req_id)
    (d / "prd.md").write_text(fields.get(C.F_PRD) or "", encoding="utf-8")
    (d / "requirement.md").write_text(fields.get(C.F_DESC) or "", encoding="utf-8")
    return d


# ── 阶段处理器 ───────────────────────────────────────────────────────
def handle_clarify(rec: dict) -> None:
    f = rec["fields"]
    ws = workspace_for(rec)
    prompt = build_prompt("clarify",
                          requirement=f.get(C.F_DESC, ""),
                          clarifications=f.get(C.F_CLARIFY, ""))
    engine = resolve_agent(rec, "clarify", C.ENGINE_CLARIFY)
    notify(f.get(C.F_CHAT), f"🤔 正在澄清需求（{engine} · {ws.key}）…")
    ok, out = run_agent(engine, prompt, ws.path, timeout=C.TIMEOUT_CLARIFY)
    if not ok:
        return on_failure(rec, f"clarify({engine}) 调用失败: {out[:300]}")
    head, _, rest = out.strip().partition("\n")
    chat = f.get(C.F_CHAT)
    if head.strip().upper().startswith("CLEAR"):
        # 信息充分 → 产出 PRD，转人工确认
        advance(rec, C.S_CONFIRM, f"[clarify:{engine}] 信息充分，PRD 已生成，待人确认",
                **{C.F_PRD: rest.strip()})
        notify_card(chat, cards.confirm_card(rec))   # PRD + 「确认开发」按钮（也可回文字「确认」）
    else:
        # 还有疑问 → 追加问题到澄清记录，转人工回答
        prev = f.get(C.F_CLARIFY, "")
        merged = (prev + "\n\n" + out.strip()).strip()
        advance(rec, C.S_ANSWER, f"[clarify:{engine}] 产出澄清问题，待人回答",
                **{C.F_CLARIFY: merged})
        notify(chat, "🤔 关于这个需求我有几个问题，直接回复我即可：\n\n" + out.strip())


def notify(chat_id: str | None, text: str) -> None:
    """安全发消息：没有会话 ID（比如需求是从表格 UI 建的）时静默跳过。"""
    if not chat_id:
        return
    try:
        lark.send_text(chat_id, text)
    except Exception as e:
        print(f"[dispatcher] 发消息失败 chat={chat_id}: {e}", file=sys.stderr)


def notify_card(chat_id: str | None, card: dict) -> None:
    """安全发交互卡片；失败不影响主流程。"""
    if not chat_id:
        return
    try:
        lark.send_card(chat_id, card)
    except Exception as e:
        print(f"[dispatcher] 发卡片失败 chat={chat_id}: {e}", file=sys.stderr)


def _run_test(path: Path, ws: workspaces.Workspace) -> tuple[int, str]:
    """用平台默认 shell（Win=cmd / Unix=sh）跑 TEST_CMD。返回 (退出码, 合并输出)。"""
    p = subprocess.run(ws.test_cmd, cwd=str(path), shell=True,
                       capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _error_count(out: str) -> int:
    """从验收输出里估算"错误数"——数命中 GATE_ERROR_RE 的行（跨 linter 的通用启发式）。"""
    return sum(1 for line in out.splitlines() if C.GATE_ERROR_RE.search(line))


def run_gate(wt: Path, ws: workspaces.Workspace) -> tuple[bool, str]:
    """相对基线验收门（跨平台、跨 SCM）。无 TEST_CMD / 纯非代码改动 → 直接过；
    改动后 TEST_CMD 通过 → 过；没过则在 base 上再跑一次比错误数：
    没变多就放行（不让 agent 替仓库的旧账背锅）。GATE_RELATIVE=False 退回绝对门。"""
    if not ws.test_cmd:
        return True, "(未设验收门)"
    exts = ws.code_exts or C.CODE_EXTS      # ws 没配就用全局，避免空元组导致门永远跳过
    changed = scm.changed_files(ws, wt)
    if not any(f.endswith(exts) for f in changed):
        return True, "纯非代码改动，跳过验收门"
    after_rc, after_out = _run_test(wt, ws)
    if after_rc == 0:
        return True, "验收门通过（绿）"
    if not C.GATE_RELATIVE:
        return False, after_out[-400:]
    base = scm.baseline_run(ws, wt, lambda p: _run_test(p, ws))
    if base is None:
        return False, f"验收失败且无法建立基线对比：\n{after_out[-400:]}"
    base_rc, base_out = base
    if base_rc == 0:
        return False, f"基线本是通过的，本次改动引入了失败：\n{after_out[-400:]}"
    after_n, base_n = _error_count(after_out), _error_count(base_out)
    if after_n <= base_n:
        return True, f"基线本就红（{base_n} 处），本次未新增（{after_n} 处）→ 相对基线放行"
    return False, f"错误数 {base_n} → {after_n}，本次引入新问题：\n{after_out[-400:]}"


def handle_develop(rec: dict) -> None:
    rid = rec["record_id"]
    chat = rec["fields"].get(C.F_CHAT)
    ws = workspace_for(rec)
    wt, branch = ensure_worktree(rid, ws)
    write_dossier(wt, rid, rec["fields"])
    prompt = build_prompt("code", req_id=rid, dossier=str(dossier_dir(wt, rid)))
    engine = resolve_agent(rec, "code", C.ENGINE_CODE)
    changed = scm.changed_files(ws, wt)
    if changed:
        log(f"  复用已有 change set：{len(changed)} 个文件已相对 {ws.base_ref} 变更")
    else:
        notify(chat, f"🔧 开始开发（{engine}）：写代码 + 跑测试，可能需要几分钟，请稍候…")
        ok, out = run_agent(engine, prompt, wt, timeout=C.TIMEOUT_CODE)
        if not ok:
            return on_failure(rec, f"coder({engine}) 调用失败: {out[:300]}")
        changed = [x for x in git("diff", "--name-only", f"{ws.base_ref}...HEAD",
                                  cwd=wt, check=False).stdout.splitlines() if x.strip()]
    # 不信 agent 自述，自己跑验收门（跨平台）
    health.emit("dispatcher", "gate_start", record_id=rid, worktree=str(wt))
    ok_test, detail = run_gate(wt, ws)
    health.emit("dispatcher", "gate_done", record_id=rid, ok=ok_test, detail=_short(detail))
    if not ok_test:
        notify(chat, f"❌ 测试未通过，将重试（{engine}）。")
        return on_failure(rec, f"测试未通过: {detail[-400:]}")
    pub = scm.after_develop(ws, wt, branch)
    if not pub.ok:
        return on_failure(rec, f"发布失败: {pub.detail}")
    advance(rec, C.S_REVIEW, f"[code:{engine}] 完成、测试通过、{pub.note}",
            **{C.F_LINK: pub.link})
    notify(chat, f"✅ 开发完成（{engine}）：改动 {len(changed)} 个文件 · 测试通过 · {pub.note}，进入 Review。")


def handle_review(rec: dict) -> None:
    rid = rec["record_id"]
    chat = rec["fields"].get(C.F_CHAT)
    ws = workspace_for(rec)
    wt, branch = ensure_worktree(rid, ws)
    diff = scm.diff_text(ws, wt)
    prompt = build_prompt("review", dossier=str(dossier_dir(wt, rid)), diff=diff)
    engine = resolve_agent(rec, "review", C.ENGINE_REVIEW)
    notify(chat, f"🔍 开始 Review（{engine}）：审查改动中…")
    ok, out = run_agent(engine, prompt, wt, timeout=C.TIMEOUT_REVIEW)
    if not ok:
        return on_failure(rec, f"reviewer({engine}) 调用失败: {out[:300]}")
    if out.strip().split("\n", 1)[0].upper().startswith("PASS"):
        title = rec["fields"].get(C.F_TITLE) or branch
        body = rec["fields"].get(C.F_PRD) or ""
        pub = scm.after_review(ws, wt, branch, title, body)   # git: 建PR/MR；svn: commit
        if not pub.ok:
            return on_failure(rec, f"发布失败: {pub.detail}")
        link = pub.link or rec["fields"].get(C.F_LINK) or branch
        advance(rec, C.S_MERGE, f"[review:{engine}] PASS，{pub.note}，待人工合并",
                **{C.F_LINK: link})
        notify(chat, f"✅ Review 通过（{engine}）！{pub.note}。")
        notify_card(chat, cards.merge_card(rec))     # 链接 + 「已合并/完成」按钮
    else:
        # review 未过 → 写回意见、打回开发（失败计数，超限则阻塞）
        (dossier_dir(wt, rid) / "review.md").write_text(out, encoding="utf-8")
        notify(chat, f"⚠️ Review 未通过（{engine}），已打回开发重做。")
        on_failure(rec, "review 未通过，打回开发", retry_status=C.S_DEV)


HANDLERS = {
    C.S_CLARIFY: handle_clarify,
    C.S_DEV:     handle_develop,
    C.S_REVIEW:  handle_review,
}


# ── 主循环 ───────────────────────────────────────────────────────────
def _process_chain(rec: dict) -> None:
    """把一条记录连续推到人工卡点/终态：开发→Review→建 PR 一气呵成，
    撞上 待回答/待确认/待合并/完成/已阻塞，或失败留原地时停下。"""
    while rec["fields"].get(C.F_STATUS) in C.ACTIONABLE:
        status = rec["fields"].get(C.F_STATUS)
        title = rec["fields"].get(C.F_TITLE) or rec["record_id"]
        stage = HANDLERS[status].__name__
        claim = runs.claim(rec["record_id"], stage, status, title)
        if not claim.ok:
            health.emit(
                "dispatcher",
                "record_claim_skip",
                record_id=rec["record_id"],
                status=status,
                reason=claim.reason,
                run_id=claim.run_id,
                next_retry_at=claim.next_retry_at,
            )
            if claim.reason == "retry_wait":
                log(f"跳过「{title}」· 状态={status} · 等待下次重试")
            else:
                log(f"跳过「{title}」· 状态={status} · 已有执行锁 {claim.run_id}")
            return
        health.emit("dispatcher", "record_start", record_id=rec["record_id"], status=status, title=title, stage=stage, run_id=claim.run_id, attempt=claim.attempts)
        log(f"处理「{title}」· 状态={status} · 进入 {stage} · run={claim.run_id}")
        try:
            HANDLERS[status](rec)
        except Exception as e:  # 单条出错不拖垮整轮
            health.emit("dispatcher", "record_exception", record_id=rec["record_id"], status=status, run_id=claim.run_id, error=_short(e))
            log(f"  → 异常：{e}")
            try:
                on_failure(rec, f"{status} 阶段异常: {e}")
            except Exception as e2:
                print(f"[dispatcher] 记录 {rec['record_id']} 双重失败: {e2}", file=sys.stderr)
            fails = int(rec["fields"].get(C.F_FAILS) or 0)
            runs.fail(claim.run_id, f"{status} 阶段异常: {e}", retry_delay=_retry_delay(fails))
            return  # 异常后不在本链里立刻重试，留给下次触发/兜底轮询
        new = rec["fields"].get(C.F_STATUS)
        health.emit("dispatcher", "record_done", record_id=rec["record_id"], status=new, previous_status=status, run_id=claim.run_id)
        log(f"  → 完成，新状态={new}")
        if new == status:
            fails = int(rec["fields"].get(C.F_FAILS) or 0)
            runs.fail(claim.run_id, f"{status} 阶段未推进，等待重试", retry_delay=_retry_delay(fails))
            return  # 状态没变（失败留原地等重试）→ 避免 tight loop
        runs.complete(claim.run_id, status, new, "stage advanced")
        # 状态已推进且仍 actionable → while 继续链式（如 开发中→Review中）


def tick() -> None:
    health.emit("dispatcher", "tick_start")
    records = lark.list_records()
    actionable = [r for r in records if r["fields"].get(C.F_STATUS) in C.ACTIONABLE]
    health.emit("dispatcher", "tick_scanned", records=len(records), actionable=len(actionable))
    log(f"扫描 {len(records)} 条记录，待处理 {len(actionable)} 条")
    for rec in actionable:          # 本轮快照里的都处理，每条链式推到卡点
        _process_chain(rec)


def locked_tick() -> None:
    """加文件锁跑一次 tick；拿不到锁说明已有实例在处理，直接跳过（防并发）。
    filelock 跨平台（Windows/macOS/Linux 通用）。"""
    lock = filelock.FileLock(str(C.LOCKFILE))
    try:
        with lock.acquire(timeout=0):       # 非阻塞；被占就抛 Timeout
            tick()
    except filelock.Timeout:
        health.emit("dispatcher", "lock_busy", lockfile=str(C.LOCKFILE))
        log("已有 dispatcher 在处理，跳过本次")


def main() -> None:
    C.validate()
    once = "--once" in sys.argv
    health.emit("dispatcher", "starting", once=once, poll_interval=C.POLL_INTERVAL)
    log(f"dispatcher 启动 · base={C.BASE_TOKEN[:8]}… table={C.TABLE_ID} · "
        f"{'单轮' if once else f'轮询每 {C.POLL_INTERVAL}s'}")
    while True:
        try:
            locked_tick()
        except Exception as e:
            health.emit("dispatcher", "tick_exception", error=_short(e))
            print(f"[dispatcher] tick 异常: {e}", file=sys.stderr)
        if once:
            break
        time.sleep(C.POLL_INTERVAL)


if __name__ == "__main__":
    main()
