#!/usr/bin/env python3
"""SCM 适配层：把"准备工作区 / 算改动 / 基线测试 / 发布"抽象出来。

支持：
  - git   ：GitHub（gh pr create）、GitLab（glab mr create）、纯 git（push 不建 PR）
  - svn   ：集中式、无 PR——开发改工作副本，Review 通过后 svn commit，人工去 svn 看/合

模型差异：
  - git 流：开发阶段 push 分支 → Review 阶段建 PR/MR → 人工 merge
  - svn 流：开发阶段不提交 → Review 通过后 svn commit 到分支 → 人工在 svn review/合并
⚠️ svn 分支为最佳努力实现，未在真实 svn 仓库验证；首次用请在测试库验证 svn 命令细节。
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import workspaces


@dataclass
class Prepared:
    work_path: Path     # agent 实际干活的目录
    branch: str         # 分支名 / 标识


@dataclass
class PublishResult:
    ok: bool
    link: str = ""      # 写进 Base「分支PR链接」
    note: str = ""      # 日志/通知文案
    detail: str = ""    # 失败详情


def _run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None,
                          capture_output=True, text=True, check=check)


def _is_svn(ws: "workspaces.Workspace") -> bool:
    return getattr(ws, "scm", "git") == "svn"


# ── 准备工作区 ───────────────────────────────────────────────────────
def prepare(ws: "workspaces.Workspace", req_id: str, worktree_base: Path) -> Prepared:
    if _is_svn(ws):
        branch = f"req-{req_id}"
        wc = worktree_base / f"REQ-{req_id}"
        if not wc.exists():
            worktree_base.mkdir(parents=True, exist_ok=True)
            _run(["svn", "checkout", ws.base_ref, str(wc)])   # base_ref 是 svn URL（trunk/分支）
        return Prepared(wc, branch)
    # git
    branch = f"feat/req-{req_id}"
    wt = worktree_base / f"REQ-{req_id}"
    if not wt.exists():
        worktree_base.mkdir(parents=True, exist_ok=True)
        _run(["git", "fetch", "origin"], cwd=ws.path, check=False)
        _run(["git", "worktree", "add", "-B", branch, str(wt), ws.base_ref], cwd=ws.path)
    return Prepared(wt, branch)


# ── 算改动文件 ───────────────────────────────────────────────────────
def changed_files(ws: "workspaces.Workspace", work: Path) -> list[str]:
    if _is_svn(ws):
        out = _run(["svn", "status"], cwd=work, check=False).stdout
        return [line[8:].strip() for line in out.splitlines() if line[:1] in {"A", "M", "D", "R"}]
    out = _run(["git", "diff", "--name-only", f"{ws.base_ref}...HEAD"], cwd=work, check=False).stdout
    return [x for x in out.splitlines() if x.strip()]


# ── 改动的 diff 文本（喂给 Review）──────────────────────────────────
def diff_text(ws: "workspaces.Workspace", work: Path) -> str:
    if _is_svn(ws):
        return _run(["svn", "diff"], cwd=work, check=False).stdout
    return _run(["git", "diff", f"{ws.base_ref}...HEAD"], cwd=work, check=False).stdout


# ── 相对基线测试（在 base 上跑一次 run_test）─────────────────────────
def baseline_run(ws: "workspaces.Workspace", work: Path, run_test):
    """返回 (rc, out)；建立不了基线返回 None。run_test(path)->(rc,out)。"""
    base = work.parent / f"{work.name}__base"
    if _is_svn(ws):
        try:
            _run(["svn", "checkout", ws.base_ref, str(base)])
        except Exception:
            return None
        try:
            return run_test(base)
        finally:
            shutil.rmtree(base, ignore_errors=True)
    # git：临时 worktree at base_ref，复用 node_modules 软链
    try:
        _run(["git", "worktree", "add", "--detach", "--force", str(base), ws.base_ref], cwd=work)
    except Exception:
        return None
    try:
        nm = work / "node_modules"
        if nm.exists() and not (base / "node_modules").exists():
            try:
                (base / "node_modules").symlink_to(nm, target_is_directory=True)
            except OSError:
                pass
        return run_test(base)
    finally:
        _run(["git", "worktree", "remove", "--force", str(base)], cwd=work, check=False)


# ── 开发阶段发布（git：push 分支；svn：留待 commit）──────────────────
def after_develop(ws: "workspaces.Workspace", work: Path, branch: str) -> PublishResult:
    if _is_svn(ws):
        return PublishResult(True, f"svnwc:{ws.key}", "改动留在 svn 工作副本，待 Review 通过后提交")
    if not ws.push_enabled:
        return PublishResult(True, f"local:{ws.key}:{branch}", f"本地分支 {branch}（未启用 push）")
    p = _run(["git", "push", "-u", "origin", branch], cwd=work, check=False)
    if p.returncode != 0:
        return PublishResult(False, detail=(p.stderr or p.stdout)[-400:])
    return PublishResult(True, branch, f"已推送 {branch}")


# ── Review 通过后发布（git：建 PR/MR；svn：commit）───────────────────
def after_review(ws: "workspaces.Workspace", work: Path, branch: str,
                 title: str, body: str) -> PublishResult:
    if _is_svn(ws):
        if not ws.push_enabled:
            return PublishResult(True, f"svnwc:{ws.key}", "Review 通过（未启用 svn commit）")
        msg = f"{title}\n\n{body}".strip()[:2000]
        p = _run(["svn", "commit", "-m", msg], cwd=work, check=False)
        if p.returncode != 0:
            return PublishResult(False, detail=(p.stderr or p.stdout)[-400:])
        return PublishResult(True, f"svn:{ws.base_ref}", "已 svn commit（SVN 无 PR，请人工 review/合并）")
    # git：建 PR/MR
    if not ws.pr_enabled:
        return PublishResult(True, branch, f"已推送 {branch}（未启用建 PR）")
    provider = getattr(ws, "pr_provider", "github")
    if provider == "gitlab":
        cmd = ["glab", "mr", "create", "--source-branch", branch,
               "--title", title, "--description", body, "--yes"]
        kind = "MR"
    elif provider == "github":
        cmd = ["gh", "pr", "create", "--head", branch, "--title", title, "--body", body]
        if getattr(ws, "gh_repo", ""):
            cmd += ["--repo", ws.gh_repo]
        kind = "PR"
    else:
        return PublishResult(True, branch, f"已推送 {branch}（pr_provider={provider}，不建 PR）")
    r = _run(cmd, cwd=work, check=False)
    out = (r.stdout or r.stderr).strip()
    if r.returncode != 0:
        return PublishResult(False, branch, detail=out[-400:])
    url = out.splitlines()[-1] if out else branch
    return PublishResult(True, url, f"已建 {kind}")
