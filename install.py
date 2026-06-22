#!/usr/bin/env python3
"""agent-pipeline 跨平台引导安装（Windows / macOS / Linux）。
  Windows: py install.py   或   python install.py
  macOS/Linux: python3 install.py   （也可直接 bash install.sh）
幂等：可重复运行；已配置项以当前值作默认。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent
SRC = DIR / "src"                       # 代码都在 src/，入口脚本从这跑
IS_WIN = sys.platform == "win32"
VENV = DIR / ".venv"
VPY = VENV / ("Scripts/python.exe" if IS_WIN else "bin/python")
ENV = DIR / ".env"
MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
_PLACEHOLDERS = {"", "cli_xxx", "xxx", "app_token_xxx", "tblxxx", "/abs/path/to/your/repo"}


def sh(cmd: list[str]) -> int:
    return subprocess.run(cmd, cwd=str(DIR)).returncode


def env_load() -> dict:
    d = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip()
    return d


def env_set(key: str, val: str) -> None:
    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    out, seen = [], False
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and s.split("=", 1)[0] == key:
            out.append(f"{key}={val}"); seen = True
        else:
            out.append(line)
    if not seen:
        out.append(f"{key}={val}")
    ENV.write_text("\n".join(out) + "\n", encoding="utf-8")


def ask(key: str, prompt: str, default: str = "") -> None:
    cur = env_load().get(key, "")
    if cur in _PLACEHOLDERS:
        cur = ""
    cur = cur or default
    val = input(f"  {prompt} [{cur}]: ").strip() or cur
    if val:
        env_set(key, val)


def prompt(text: str, default: str = "") -> str:
    val = input(f"  {text} [{default}]: ").strip()
    return val or default


def yes(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def main() -> None:
    print("=" * 46)
    print(" agent-pipeline 引导安装")
    print(f" 目录: {DIR}  平台: {sys.platform}")
    print("=" * 46)

    if not ENV.exists() and (DIR / ".env.example").exists():
        ENV.write_text((DIR / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")

    # 1. venv
    if not VPY.exists():
        print("[1/6] 创建 venv ...")
        sh([sys.executable, "-m", "venv", str(VENV)])
    # 2. 依赖（镜像优先，失败回落官方）
    print("[2/6] 安装依赖 (lark-oapi + filelock) ...")
    if sh([str(VPY), "-m", "pip", "install", "-q", "-r", "requirements.txt", "-i", MIRROR]) != 0:
        sh([str(VPY), "-m", "pip", "install", "-q", "-r", "requirements.txt"])
    print("  依赖就绪")

    # 3. 交互配置
    print("[3/6] 配置（回车用方括号默认值）")
    print(" · 飞书 app 凭据（开发者后台自建应用，需 bitable + im 权限）")
    ask("FEISHU_APP_ID", "飞书 APP_ID (cli_...)")
    ask("FEISHU_APP_SECRET", "飞书 APP_SECRET")
    print(" · 目标代码仓库（agent 在这里改代码，需 git 仓库且有 origin/main）")
    ask("PIPELINE_REPO_PATH", "目标仓库绝对路径")
    print(" · 各阶段默认 agent（cursor/claude/gemini/codex；飞书可用「需求@xxx」覆盖）")
    ask("PIPELINE_ENGINE_CLARIFY", "澄清阶段 agent", "cursor")
    ask("PIPELINE_ENGINE_CODE", "开发阶段 agent", "cursor")
    ask("PIPELINE_ENGINE_REVIEW", "Review 阶段 agent", "cursor")
    print("   ⚠ 务必确认所选 agent 的 CLI 已**登录**（如 `cursor-agent login`）——"
          "装了但没登录会导致每次调用都失败。第 5 步 `doctor.py --deep` 可实测。")
    print(" · 验收门命令（在 worktree 里跑，exit 0 通过；留空则不设门）")
    ask("PIPELINE_TEST_CMD", "测试/lint 命令，如 npm run lint")
    _ensure_workspaces()
    _ensure_config_files()

    # 4. 建表
    print("[4/6] 飞书多维表格 ...")
    if env_load().get("PIPELINE_BASE_TOKEN") not in _PLACEHOLDERS:
        print("  已有 BASE_TOKEN，跳过（新建另一张表：venv python src/bootstrap.py --force）")
    else:
        sh([str(VPY), "-B", str(SRC / "bootstrap.py")])

    # 5. 自检
    print("[5/6] 自检 ...")
    sh([str(VPY), "-B", str(SRC / "doctor.py")])
    if input("  深度自检（实测各 agent 是否已登录可用，会各跑一次极短调用）? [y/N]: ").strip().lower().startswith("y"):
        sh([str(VPY), "-B", str(SRC / "doctor.py"), "--deep"])

    # 6. 常驻服务
    print("[6/6] 常驻服务（listener + dispatcher 需长期跑）")
    if IS_WIN:
        _windows_hint()
    elif sys.platform == "darwin":
        if input("  安装 launchd 常驻服务? [y/N]: ").strip().lower().startswith("y"):
            _mac_services()
    else:
        _linux_hint()

    print("=" * 46)
    print(" 完成。飞书私聊机器人发「需求@cursor：<一句话>」即可开跑。")
    print("=" * 46)


def _ensure_workspaces() -> None:
    f = DIR / "workspaces.json"
    if f.exists():
        return
    repo = env_load().get("PIPELINE_REPO_PATH", "")
    if not repo or repo in _PLACEHOLDERS:
        return
    name = Path(repo).name or "default"
    test_cmd = env_load().get("PIPELINE_TEST_CMD", "")
    print(" · 工作区 SCM（生成 workspaces.json，可稍后手改）")
    scm = prompt("SCM 类型 git/svn", "git").lower()
    if scm == "svn":
        base = prompt("SVN base URL（trunk/branch）", "")
        auto_commit = yes(prompt("Review 通过后自动 svn commit? y/N", "N"))
        item = {
            "path": repo,
            "scm": "svn",
            "base": base,
            "push_enabled": auto_commit,
            "test_cmd": test_cmd,
        }
    else:
        scm = "git"
        base = prompt("Git base ref", "origin/main")
        target = base.rstrip("/").rsplit("/", 1)[-1] if "/" in base else base
        provider = prompt("自动创建 Review? none/github/gitlab", "none").lower()
        auto_review = provider in {"github", "gitlab"}
        if not auto_review:
            provider = "none"
        item = {
            "path": repo,
            "scm": "git",
            "base": base,
            "target_branch": target,
            "push_enabled": auto_review,
            "pr_enabled": auto_review,
            "pr_provider": provider,
            "test_cmd": test_cmd,
        }
        if provider == "gitlab":
            item["gitlab_repo"] = prompt("GitLab 项目 group/project（可留空让 glab 从 remote 推断）", "")
        elif provider == "github":
            item["gh_repo"] = prompt("GitHub 项目 org/repo（可留空）", env_load().get("PIPELINE_GH_REPO", ""))
    f.write_text(json.dumps({"default": name, "items": {name: item}}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  ✓ 已生成 workspaces.json（默认工作区：{name} / scm={scm}）")


def _copy_example_if_missing(example_name: str, target_name: str) -> bool:
    target = DIR / target_name
    if target.exists():
        return False
    example = DIR / example_name
    if not example.exists():
        return False
    target.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    return True


def _ensure_config_files() -> None:
    print(" · 可迁移配置文件")
    if input("  接入已有飞书 Base，需要自定义字段名映射 fields.json? [y/N]: ").strip().lower().startswith("y"):
        if _copy_example_if_missing("fields.example.json", "fields.json"):
            print("  ✓ 已生成 fields.json，请按你的 Base 列名修改右侧值")
        else:
            print("  fields.json 已存在，跳过")
        env_set("PIPELINE_FIELDS_FILE", str(DIR / "fields.json"))

    if _copy_example_if_missing("agents.example.json", "agents.json"):
        print("  ✓ 已生成 agents.json（默认 agent/命令模板，可稍后手改）")
    else:
        print("  agents.json 已存在，跳过")
    env_set("PIPELINE_AGENTS_FILE", str(DIR / "agents.json"))


def _detect_path() -> str:
    import os
    import shutil
    parts = [] if IS_WIN else ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    for b in ("cursor-agent", "gemini", "claude", "codex", "gh", "git", "node"):
        p = shutil.which(b)
        if p:
            d = str(Path(p).parent)
            if d not in parts:
                parts.insert(0, d)
    return os.pathsep.join(parts)


def _mac_services() -> None:
    la = Path.home() / "Library" / "LaunchAgents"
    la.mkdir(parents=True, exist_ok=True)
    (DIR / "logs").mkdir(exist_ok=True)
    pathv = _detect_path()
    for s in ("listener", "dispatcher"):
        plist = la / f"com.agentpipeline.{s}.plist"
        plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.agentpipeline.{s}</string>
  <key>ProgramArguments</key><array><string>/usr/bin/caffeinate</string><string>-i</string><string>{VPY}</string><string>-B</string><string>{SRC / (s + '.py')}</string></array>
  <key>WorkingDirectory</key><string>{SRC}</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>{pathv}</string>
    <key>PYTHONDONTWRITEBYTECODE</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{DIR / 'logs' / (s + '.log')}</string>
  <key>StandardErrorPath</key><string>{DIR / 'logs' / (s + '.log')}</string>
</dict></plist>
""", encoding="utf-8")
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
        subprocess.run(["launchctl", "load", "-w", str(plist)])
        print(f"  ✓ {s} 服务已装并启动（caffeinate -i 防止待机休眠掉线）")
    print("  注意：装了 hermes 的话别再 hermes gateway start（会和 listener 抢长连接）。")
    print("  提示：合盖也想跑请 `sudo pmset -a disablesleep 1`，或用台式机/服务器常开。")


def _windows_hint() -> None:
    lis, dis = SRC / "listener.py", SRC / "dispatcher.py"
    print("  Windows 让两个进程长期跑（任选）：")
    print(f'    A) 两个窗口直接跑： "{VPY}" -B "{lis}"    和    "{VPY}" -B "{dis}"')
    print("    B) 任务计划程序（登录自启、后台）：")
    print(f'       schtasks /Create /TN agentpipeline-listener   /SC ONLOGON /TR "\\"{VPY}\\" -B \\"{lis}\\""')
    print(f'       schtasks /Create /TN agentpipeline-dispatcher /SC ONLOGON /TR "\\"{VPY}\\" -B \\"{dis}\\""')
    print("    C) 用 NSSM 注册成 Windows 服务（开机即跑，最稳）。")


def _linux_hint() -> None:
    print("  Linux 让两个进程长期跑（systemd --user 或 nohup）：")
    print(f"    nohup {VPY} -B {SRC / 'listener.py'}   >>logs/listener.log 2>&1 &")
    print(f"    nohup {VPY} -B {SRC / 'dispatcher.py'} >>logs/dispatcher.log 2>&1 &")


if __name__ == "__main__":
    main()
