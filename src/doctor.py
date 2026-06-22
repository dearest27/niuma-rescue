#!/usr/bin/env python3
"""部署自检：逐项检查流水线能否跑起来，缺什么明确报出来。
  python3 doctor.py
退出码非 0 表示有必需项未通过。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import config as C
import workspaces

_fail = 0


def check(cond: bool, ok_msg: str, bad_msg: str, fatal: bool = True) -> bool:
    global _fail
    mark = "✓" if cond else ("✗" if fatal else "!")
    print(f"  {mark} {ok_msg if cond else bad_msg}")
    if not cond and fatal:
        _fail += 1
    return cond


def _hermes_env_has(key: str) -> bool:
    p = Path.home() / ".hermes" / ".env"
    return p.exists() and any(l.strip().startswith(key + "=") for l in p.read_text().splitlines())


def _git_remote_host(path: Path, remote: str = "origin") -> str:
    r = subprocess.run(
        ["git", "-C", str(path), "remote", "get-url", remote],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return ""
    url = (r.stdout or "").strip()
    parsed = urlparse(url)
    if parsed.netloc:
        return parsed.netloc
    if "@" in url and ":" in url:
        return url.split("@", 1)[1].split(":", 1)[0]
    return ""


def _check_scm_workspace(ws: workspaces.Workspace) -> None:
    if ws.scm == "svn":
        check(shutil.which("svn") is not None, f"{ws.key}: svn CLI 已安装", f"{ws.key}: svn CLI 未找到", fatal=False)
        check(bool(ws.base_ref), f"{ws.key}: SVN base 已配置", f"{ws.key}: 缺少 SVN base URL", fatal=False)
        return

    check(shutil.which("git") is not None, f"{ws.key}: git CLI 已安装", f"{ws.key}: git CLI 未找到", fatal=False)
    if ws.path.exists():
        check((ws.path / ".git").exists(), f"{ws.key}: 是 git 仓库", f"{ws.key}: 不是 git 仓库 {ws.path}", fatal=False)
    if ws.pr_enabled and ws.pr_provider == "github":
        gh = shutil.which("gh")
        if check(gh is not None, f"{ws.key}: gh 已安装", f"{ws.key}: gh 未安装（自动建 GitHub PR 需要）", fatal=False):
            r = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
            check(r.returncode == 0, f"{ws.key}: gh 已登录", f"{ws.key}: gh 未登录（gh auth login）", fatal=False)
    if ws.pr_enabled and ws.pr_provider == "gitlab":
        glab = shutil.which("glab")
        if check(glab is not None, f"{ws.key}: glab 已安装", f"{ws.key}: glab 未安装（自动建 GitLab MR 需要）", fatal=False):
            cmd = ["glab", "auth", "status"]
            host = _git_remote_host(ws.path)
            if host:
                cmd += ["--hostname", host]
            r = subprocess.run(cmd, capture_output=True, text=True)
            check(r.returncode == 0, f"{ws.key}: glab 已登录", f"{ws.key}: glab 未登录（glab auth login）", fatal=False)


def _agent_login_probe() -> None:
    """--deep：真正各跑一次极短调用，验证 agent 不只是装了、而是已登录可用。
    这是上手第一大坑——CLI 在 PATH 里但没登录，每次跑都 auth 失败。"""
    print("== Agent 登录探测（--deep，会各跑一次极短调用）==")
    import agent_adapters
    repo = os.getenv("PIPELINE_REPO_PATH") or "."
    cwd = Path(repo) if Path(repo).exists() else Path(".")
    for eng in sorted({C.ENGINE_CLARIFY, C.ENGINE_CODE, C.ENGINE_REVIEW}):
        binary = C.AGENT_CMDS.get(eng, [eng])[0]
        if not shutil.which(binary):
            check(False, "", f"{eng}（{binary}）未安装，跳过", fatal=False)
            continue
        try:
            r = agent_adapters.run_agent(eng, "只回复两个字：就绪", cwd, timeout=90)
            check(r.ok, f"{eng} 调用成功（已登录可用）",
                  f"{eng} 调用失败（多半没登录或没额度）：{(r.output or '')[:160]}", fatal=False)
        except Exception as e:
            check(False, "", f"{eng} 探测异常：{e}", fatal=False)


def main() -> None:
    deep = "--deep" in sys.argv
    print("== 飞书凭据 ==")
    creds = bool(os.getenv("FEISHU_APP_ID") and os.getenv("FEISHU_APP_SECRET"))
    hermes_creds = _hermes_env_has("FEISHU_APP_ID") and _hermes_env_has("FEISHU_APP_SECRET")
    check(creds or hermes_creds, "FEISHU_APP_ID/SECRET 就绪", "缺 FEISHU_APP_ID/SECRET（配到项目 .env）")

    print("== .env 必填项 ==")
    base, table = os.getenv("PIPELINE_BASE_TOKEN"), os.getenv("PIPELINE_TABLE_ID")
    repo = os.getenv("PIPELINE_REPO_PATH")
    check(bool(base and table), "BASE_TOKEN / TABLE_ID 已配", "缺 BASE_TOKEN/TABLE_ID（先跑 python3 bootstrap.py）")
    check(bool(repo), "REPO_PATH 已配", "缺 REPO_PATH（.env 里设目标仓库）")

    print("== 配置文件 ==")
    check(True, f"fields: {C.FIELDS_FILE}（{'custom' if C.FIELDS_FILE.exists() else '默认字段'}）", "")
    check(C.WORKSPACES_FILE.exists(), f"workspaces: {C.WORKSPACES_FILE}", f"workspaces 文件不存在：{C.WORKSPACES_FILE}", fatal=False)
    check(True, f"agents: {C.AGENTS_FILE}（{'custom' if C.AGENTS_FILE.exists() else '默认命令'}）", "")

    print("== 飞书 Base 可达 + 字段 ==")
    if base and table:
        try:
            import lark
            recs = lark.list_records()
            check(True, f"Base 可读（{len(recs)} 条记录）", "")
            data = lark._api("GET", f"/open-apis/bitable/v1/apps/{C.BASE_TOKEN}/tables/{C.TABLE_ID}/fields")
            fnames = {f["field_name"] for f in data["items"]}
            need = {C.F_TITLE, C.F_STATUS, C.F_DESC, C.F_CLARIFY, C.F_PRD,
                    C.F_LINK, C.F_LOG, C.F_FAILS, C.F_OWNER, C.F_CHAT}
            check(not (need - fnames), "核心字段齐全", f"缺核心字段：{need - fnames}")
            opt = {C.F_AGENT, C.F_AGENT_CLARIFY, C.F_AGENT_CODE, C.F_AGENT_REVIEW, C.F_WORKSPACE} - fnames
            check(not opt, "agent 选择字段齐全", f"缺可选 agent 字段（会走 fallback、日志有噪音）：{opt}", fatal=False)
        except Exception as e:
            check(False, "", f"Base 不可达：{e}")
    else:
        check(False, "", "跳过（BASE 未配）", fatal=False)

    print("== 工作区 ==")
    items = []
    try:
        items = workspaces.list_workspaces()
        check(bool(items), f"工作区配置可读（{len(items)} 个）", "workspaces.json 无可用工作区", fatal=False)
        for ws in items:
            check(ws.path.exists(), f"{ws.key}: {ws.path}", f"{ws.key}: 路径不存在 {ws.path}", fatal=False)
            _check_scm_workspace(ws)
    except Exception as e:
        check(False, "", f"工作区配置异常：{e}", fatal=False)

    print("== 默认目标仓库 ==")
    if repo:
        p = Path(repo)
        is_git = (p / ".git").exists()
        check(is_git, f"{repo} 是 git 仓库", f"{repo} 不是 git 仓库（如果只用 workspaces.json，可忽略）", fatal=False)
        if is_git:
            r = subprocess.run(["git", "-C", repo, "ls-remote", "--heads", "origin", "main"],
                               capture_output=True, text=True)
            check(bool(r.stdout.strip()), "origin/main 存在",
                  "origin/main 不存在（worktree 拉不起来）", fatal=False)

    print("== Agent CLI（按当前 ENGINE_* 配置）==")
    for eng in {C.ENGINE_CLARIFY, C.ENGINE_CODE, C.ENGINE_REVIEW}:
        binary = C.AGENT_CMDS.get(eng, [eng])[0]
        check(shutil.which(binary) is not None, f"{eng}（{binary}）已安装",
              f"{eng}（{binary}）未找到（PATH 里没有）", fatal=False)

    print("== 入站 listener ==")
    check((Path(__file__).resolve().parent / "listener.py").exists(),
          "listener.py 存在", "缺 listener.py（飞书消息进不来）")
    try:
        import lark_oapi  # noqa: F401
        has_lark_oapi = True
    except ImportError:
        has_lark_oapi = False
    check(has_lark_oapi, "lark-oapi 已安装", "缺 lark-oapi（pip install -r requirements.txt）", fatal=False)

    if deep:
        print()
        _agent_login_probe()

    print()
    if _fail:
        sys.exit(f"✗ {_fail} 项必需检查未通过。按上面 ✗ 项修复；"
                 f"飞书/Base 配置见 docs/feishu-app-setup.md，agent 见 docs/agent-cli-setup.md。")
    print("✓ 自检通过，可以启动 src/listener.py + src/dispatcher.py。")
    if not deep:
        print("  提示：再跑 `doctor.py --deep` 可实测 agent 是否已登录可用（各跑一次极短调用）。")


if __name__ == "__main__":
    main()
