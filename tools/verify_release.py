#!/usr/bin/env python3
"""Run release sanity checks and inspect the generated zip."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_ZIP_PARTS = {
    ".env",
    ".venv",
    "__pycache__",
    "logs",
    "state",
    "worktrees",
    "dist",
    "scripts/windows",
    "workspaces.json",
    "fields.json",
    "agents.json",
    "zentao.json",
}
REQUIRED_ZIP_PATHS = {
    "README.md",
    "LICENSE",
    ".env.example",
    "zentao.example.json",
    "requirements.txt",
    "install.py",
    "install.sh",
    "install.ps1",
    "src/dispatcher.py",
    "src/agent_adapters.py",
    "tools/package_release.py",
    "tools/smoke.py",
    "tools/test.py",
    "tools/verify_release.py",
    "docs/RELEASE_CHECKLIST.md",
}
TEXT_SUFFIXES = {".md", ".py", ".sh", ".ps1", ".txt", ".example", ".toml", ".json", ".yml", ".yaml"}
LOCAL_ONLY_MARKERS = (
    "/Users/" + "d/",
    "qt" + "cc",
    "project" + "_test_2",
)


class CheckFailed(RuntimeError):
    pass


def ok(msg: str) -> None:
    print(f"✓ {msg}")


def fail(msg: str) -> None:
    raise CheckFailed(msg)


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    print("+ " + " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if check and proc.returncode != 0:
        out = (proc.stdout or "") + (proc.stderr or "")
        fail(f"命令失败：{' '.join(cmd)}\n{out[-1200:]}")
    return proc


def check_bash() -> None:
    if not shutil.which("bash"):
        print("! 未找到 bash，跳过 install.sh 语法检查")
        return
    run(["bash", "-n", "install.sh"])
    ok("install.sh 语法检查通过")


def check_python_compile() -> None:
    files = [ROOT / "install.py"]
    files.extend(sorted((ROOT / "src").glob("*.py")))
    files.extend(sorted((ROOT / "tools").glob("*.py")))
    files.extend(sorted((ROOT / "tests").glob("*.py")))
    for path in files:
        source = path.read_text(encoding="utf-8")
        compile(source, str(path), "exec")
    ok(f"Python 语法检查通过（{len(files)} 个文件）")


def check_unit_tests() -> None:
    run([sys.executable, "-B", "tools/test.py"])
    ok("稳定性测试通过")


def check_smoke() -> None:
    run([sys.executable, "-B", "tools/smoke.py"])
    ok("本地 smoke 检查通过")


def check_requirements() -> None:
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    for name in ("lark-oapi", "filelock"):
        if name not in req:
            fail(f"requirements.txt 缺少 {name}")
    ok("requirements.txt 包含必要依赖")


def check_env_example() -> None:
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key in ("FEISHU_APP_ID=cli_xxx", "FEISHU_APP_SECRET=xxx", "PIPELINE_BASE_TOKEN=app_token_xxx", "PIPELINE_TABLE_ID=tblxxx"):
        if key not in text:
            fail(f".env.example 缺少占位配置：{key}")
    ok(".env.example 使用占位配置")


def build_release(version: str) -> Path:
    run([sys.executable, "-B", "tools/package_release.py", version])
    zip_path = ROOT / "dist" / f"agent-pipeline-v{version}.zip"
    if not zip_path.exists():
        fail(f"release zip 未生成：{zip_path}")
    ok(f"release zip 已生成：{zip_path.relative_to(ROOT)}")
    return zip_path


def _strip_root(name: str) -> str:
    parts = Path(name).parts
    if len(parts) <= 1:
        return ""
    return Path(*parts[1:]).as_posix()


def check_zip(zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        rel_names = {_strip_root(n) for n in names}
        missing = sorted(REQUIRED_ZIP_PATHS - rel_names)
        if missing:
            fail("release zip 缺少必要文件：" + ", ".join(missing))
        for rel in rel_names:
            parts = Path(rel).parts
            if rel in FORBIDDEN_ZIP_PARTS or any(part in FORBIDDEN_ZIP_PARTS for part in parts):
                fail(f"release zip 包含运行时/本地文件：{rel}")
            if rel.startswith("scripts/windows/"):
                fail(f"release zip 包含运行时生成脚本：{rel}")
        for name in names:
            rel = _strip_root(name)
            suffix = Path(rel).suffix
            if suffix not in TEXT_SUFFIXES and Path(rel).name != ".env.example":
                continue
            try:
                text = zf.read(name).decode("utf-8")
            except UnicodeDecodeError:
                continue
            for marker in LOCAL_ONLY_MARKERS:
                if marker in text:
                    fail(f"release zip 文本中包含本机痕迹 `{marker}`：{rel}")
    ok(f"release zip 内容检查通过（{len(names)} 个文件）")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify release packaging")
    parser.add_argument("version", nargs="?", default="0.1.0")
    args = parser.parse_args()
    os.chdir(ROOT)
    try:
        check_bash()
        check_python_compile()
        check_unit_tests()
        check_smoke()
        check_requirements()
        check_env_example()
        zip_path = build_release(args.version)
        check_zip(zip_path)
    except CheckFailed as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    print("✓ release 验收通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
