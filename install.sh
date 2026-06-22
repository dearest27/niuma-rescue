#!/usr/bin/env bash
# agent-pipeline 引导安装：一条命令从零跑起来。
#   bash install.sh
# 幂等：可重复运行；已配置的项会以当前值作默认。
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
DIR="$(pwd)"
SRC="$DIR/src"
PY="$DIR/.venv/bin/python"
PIP="$DIR/.venv/bin/pip"
PYPI_MIRROR="https://pypi.tuna.tsinghua.edu.cn/simple"

echo "═══════════════════════════════════════"
echo " agent-pipeline 引导安装"
echo " 目录: $DIR"
echo "═══════════════════════════════════════"

# ── .env 工具 ────────────────────────────────────────────
[ -f .env ] || cp .env.example .env
_get() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- || true; }
_is_placeholder() {
  case "${1:-}" in ""|"cli_xxx"|"xxx"|"app_token_xxx"|"tblxxx"|"/abs/path/to/your/repo") return 0 ;; *) return 1 ;; esac
}
_set() {  # _set KEY VALUE
  grep -vE "^$1=" .env > .env.tmp 2>/dev/null || true
  printf '%s=%s\n' "$1" "$2" >> .env.tmp
  mv .env.tmp .env
}
_ask() {  # _ask KEY 提示 [默认]
  local cur def val; cur="$(_get "$1")"
  _is_placeholder "$cur" && cur=""
  if [ -n "$cur" ]; then
    def="$cur"
  else
    def="${3:-}"
  fi
  read -r -p "  $2 [${def}]: " val
  val="${val:-$def}"
  [ -n "$val" ] && _set "$1" "$val"
}
_prompt() {  # _prompt 提示 默认值
  local val
  read -r -p "  $1 [$2]: " val
  printf '%s' "${val:-$2}"
}
_bool_json() {
  case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
    y|yes|true|1|on) printf 'true' ;;
    *) printf 'false' ;;
  esac
}
_copy_example_if_missing() {  # _copy_example_if_missing example target
  [ -f "$2" ] && return 1
  [ -f "$1" ] || return 1
  cp "$1" "$2"
  return 0
}

# ── 1. venv + 依赖 ───────────────────────────────────────
if [ ! -x "$PY" ]; then
  echo "[1/6] 创建 venv ..."
  python3 -m venv .venv
fi
echo "[2/6] 安装依赖 (lark-oapi + filelock) ..."
"$PIP" install -q --upgrade pip >/dev/null 2>&1 || true
"$PIP" install -q -r requirements.txt -i "$PYPI_MIRROR" \
  || "$PIP" install -q -r requirements.txt
echo "  ✓ 依赖就绪"

# ── 2. 交互配置 .env ─────────────────────────────────────
echo "[3/6] 配置（直接回车用方括号里的默认值）"
echo " · 飞书 app 凭据（开发者后台自建应用，需 bitable + im 权限）"
_ask FEISHU_APP_ID    "飞书 APP_ID (cli_...)"
_ask FEISHU_APP_SECRET "飞书 APP_SECRET"
echo " · 目标代码仓库（agent 在这里改代码，需 git 仓库且有 origin/main）"
_ask PIPELINE_REPO_PATH "目标仓库绝对路径"
echo " · 各阶段默认 agent（cursor / claude / gemini / codex；可在飞书用「需求@xxx」按需覆盖）"
_ask PIPELINE_ENGINE_CLARIFY "澄清阶段 agent" "${PIPELINE_ENGINE_CLARIFY:-cursor}"
_ask PIPELINE_ENGINE_CODE    "开发阶段 agent" "${PIPELINE_ENGINE_CODE:-cursor}"
_ask PIPELINE_ENGINE_REVIEW  "Review 阶段 agent" "${PIPELINE_ENGINE_REVIEW:-cursor}"
echo " · 验收门命令（在 worktree 里跑，exit 0 通过；留空则不设门）"
_ask PIPELINE_TEST_CMD "测试/lint 命令，如 npm run lint"
if [ ! -f workspaces.json ]; then
  repo_path="$(_get PIPELINE_REPO_PATH)"
  if ! _is_placeholder "$repo_path"; then
    repo_name="$(basename "$repo_path")"
    test_cmd="$(_get PIPELINE_TEST_CMD)"
    echo " · 工作区 SCM（生成 workspaces.json，可稍后手改）"
    scm="$(_prompt "SCM 类型 git/svn" "git")"
    scm="$(printf '%s' "$scm" | tr '[:upper:]' '[:lower:]')"
    if [ "$scm" = "svn" ]; then
      svn_base="$(_prompt "SVN base URL（trunk/branch）" "")"
      svn_commit="$(_prompt "Review 通过后自动 svn commit? y/N" "N")"
      svn_commit_bool="$(_bool_json "$svn_commit")"
      cat > workspaces.json <<JSON
{
  "default": "$repo_name",
  "items": {
    "$repo_name": {
      "path": "$repo_path",
      "scm": "svn",
      "base": "$svn_base",
      "push_enabled": $svn_commit_bool,
      "test_cmd": "$test_cmd"
    }
  }
}
JSON
    else
      git_base="$(_prompt "Git base ref" "origin/main")"
      target_branch="${git_base##*/}"
      work_mode="$(_prompt "Git 工作区模式 worktree/inline" "worktree")"
      work_mode="$(printf '%s' "$work_mode" | tr '[:upper:]' '[:lower:]')"
      case "$work_mode" in inline|inplace|in-place|current|current_branch) work_mode=inline ;; *) work_mode=worktree ;; esac
      review_provider="$(_prompt "自动创建 Review? none/github/gitlab" "none")"
      review_provider="$(printf '%s' "$review_provider" | tr '[:upper:]' '[:lower:]')"
      case "$review_provider" in github|gitlab) auto_review=true ;; *) auto_review=false; review_provider=none ;; esac
      if [ "$work_mode" = "inline" ]; then
        auto_review=false
        review_provider=none
        echo "  inline 模式会原地修改当前分支，不自动 commit/push/建 PR/MR。"
      fi
      extra=""
      if [ "$review_provider" = "gitlab" ]; then
        gitlab_repo="$(_prompt "GitLab 项目 group/project（可留空让 glab 从 remote 推断）" "")"
        extra=",
      \"gitlab_repo\": \"$gitlab_repo\""
      elif [ "$review_provider" = "github" ]; then
        gh_repo="$(_prompt "GitHub 项目 org/repo（可留空）" "$(_get PIPELINE_GH_REPO)")"
        extra=",
      \"gh_repo\": \"$gh_repo\""
      fi
      cat > workspaces.json <<JSON
{
  "default": "$repo_name",
  "items": {
    "$repo_name": {
      "path": "$repo_path",
      "scm": "git",
      "work_mode": "$work_mode",
      "base": "$git_base",
      "target_branch": "$target_branch",
      "push_enabled": $auto_review,
      "pr_enabled": $auto_review,
      "pr_provider": "$review_provider"$extra,
      "test_cmd": "$test_cmd"
    }
  }
}
JSON
    fi
    echo "  ✓ 已生成 workspaces.json（默认工作区：$repo_name / scm=$scm）"
  fi
fi

echo " · 可迁移配置文件"
read -r -p "  接入已有飞书 Base，需要自定义字段名映射 fields.json? [y/N]: " fields_cfg
if [[ "${fields_cfg:-N}" =~ ^[Yy] ]]; then
  if _copy_example_if_missing fields.example.json fields.json; then
    echo "  ✓ 已生成 fields.json，请按你的 Base 列名修改右侧值"
  else
    echo "  fields.json 已存在，跳过"
  fi
  _set PIPELINE_FIELDS_FILE "$DIR/fields.json"
fi
if _copy_example_if_missing agents.example.json agents.json; then
  echo "  ✓ 已生成 agents.json（默认 agent/命令模板，可稍后手改）"
else
  echo "  agents.json 已存在，跳过"
fi
_set PIPELINE_AGENTS_FILE "$DIR/agents.json"

# ── 3. 建飞书多维表格 ────────────────────────────────────
echo "[4/6] 飞书多维表格"
if ! _is_placeholder "$(_get PIPELINE_BASE_TOKEN)"; then
  echo "  已有 PIPELINE_BASE_TOKEN，跳过建表（要新建另一张表：$PY -B $SRC/bootstrap.py --force）"
else
  "$PY" -B "$SRC/bootstrap.py"
fi

# ── 4. 自检 ──────────────────────────────────────────────
echo "[5/6] 自检 ..."
"$PY" -B "$SRC/doctor.py" || echo "  ⚠ 有未通过项，按上面提示修复后可重跑 doctor.py"

# ── 5. 常驻服务（可选）───────────────────────────────────
echo "[6/6] 常驻服务"
read -r -p "  安装 launchd 常驻服务 listener + dispatcher? [y/N]: " svc
if [[ "${svc:-N}" =~ ^[Yy] ]]; then
  LA="$HOME/Library/LaunchAgents"; mkdir -p "$LA" logs
  # 探测各 CLI 路径拼 PATH（launchd 环境很干净）
  PATHV="/usr/bin:/bin:/usr/sbin:/sbin"
  for b in cursor-agent gemini claude codex gh git node; do
    p="$(command -v "$b" 2>/dev/null || true)"
    [ -n "$p" ] && case ":$PATHV:" in *":$(dirname "$p"):"*) ;; *) PATHV="$(dirname "$p"):$PATHV";; esac
  done
  for s in listener dispatcher; do
    plist="$LA/com.agentpipeline.$s.plist"
    cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.agentpipeline.$s</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>-B</string><string>$SRC/$s.py</string></array>
  <key>WorkingDirectory</key><string>$SRC</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$PATHV</string>
    <key>PYTHONDONTWRITEBYTECODE</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/logs/$s.log</string>
  <key>StandardErrorPath</key><string>$DIR/logs/$s.log</string>
</dict></plist>
PLIST
    launchctl unload "$plist" 2>/dev/null || true
    launchctl load -w "$plist"
    echo "  ✓ $s 服务已安装并启动"
  done
  echo "  注意：若装了 hermes，别再 hermes gateway start（会和 listener 抢飞书长连接）。"
fi

echo "═══════════════════════════════════════"
echo " 完成。飞书私聊机器人发「需求@cursor：<一句话>」即可开跑。"
echo " 手动跑：$PY -B $SRC/dispatcher.py   /   $PY -B $SRC/listener.py"
echo "═══════════════════════════════════════"
