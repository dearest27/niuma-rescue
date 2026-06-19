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
    _ensure_workspaces()
    print(" · 各阶段默认 agent（cursor/claude/gemini/codex；飞书可用「需求@xxx」覆盖）")
    ask("PIPELINE_ENGINE_CLARIFY", "澄清阶段 agent", "cursor")
    ask("PIPELINE_ENGINE_CODE", "开发阶段 agent", "cursor")
    ask("PIPELINE_ENGINE_REVIEW", "Review 阶段 agent", "cursor")
    print(" · 验收门命令（在 worktree 里跑，exit 0 通过；留空则不设门）")
    ask("PIPELINE_TEST_CMD", "测试/lint 命令，如 npm run lint")

    # 4. 建表
    print("[4/6] 飞书多维表格 ...")
    if env_load().get("PIPELINE_BASE_TOKEN") not in _PLACEHOLDERS:
        print("  已有 BASE_TOKEN，跳过（新建另一张表：venv python src/bootstrap.py --force）")
    else:
        sh([str(VPY), "-B", str(SRC / "bootstrap.py")])

    # 5. 自检
    print("[5/6] 自检 ...")
    sh([str(VPY), "-B", str(SRC / "doctor.py")])

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
    f.write_text(json.dumps({
        "default": name,
        "items": {name: {"path": repo, "scm": "git", "base": "origin/main", "test_cmd": ""}},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ 已生成 workspaces.json（默认工作区：{name}）")


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
  <key>ProgramArguments</key><array><string>{VPY}</string><string>-B</string><string>{SRC / (s + '.py')}</string></array>
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
        print(f"  ✓ {s} 服务已装并启动")
    print("  注意：装了 hermes 的话别再 hermes gateway start（会和 listener 抢长连接）。")


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
