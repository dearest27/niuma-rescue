"""中央配置：飞书 Base 标识、字段/状态常量、调度策略、各阶段引擎。

部署相关的值（Base 标识、目标仓库、测试命令、轮询间隔、默认引擎）从环境变量
或同目录 .env 读取——见 .env.example。其余是系统契约（字段名 / 状态 / 引擎命令），
保持为常量。dispatcher.py / lark.py / message_router.py 只引用本模块。
"""
import os
import re
from pathlib import Path

_PIPELINE_DIR = Path(__file__).resolve().parent          # 代码目录（src/）
_ROOT = _PIPELINE_DIR.parent                             # 项目根（.env / state / logs / workspaces 都在这）


def _load_dotenv(path: Path) -> None:
    """把 .env 的键值灌进 os.environ（不覆盖进程里已有的同名变量）。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k and k not in os.environ:
            os.environ[k] = v.strip().strip('"').strip("'")


_load_dotenv(_ROOT / ".env")


def validate() -> None:
    """dispatcher 启动时调用：必填项缺失就报错退出。首次部署请先跑 bootstrap.py 建表。"""
    missing = [k for k in ("PIPELINE_BASE_TOKEN", "PIPELINE_TABLE_ID", "PIPELINE_REPO_PATH")
               if not os.getenv(k)]
    if missing:
        raise SystemExit(
            f"缺少必填配置 {missing}：在 {_ROOT}/.env 设置（参考 .env.example）。\n"
            f"首次部署请先跑：python3 bootstrap.py"
        )


# ── 飞书 Base（部署相关 · 来自 .env；首次为空，bootstrap 建表后写入）──
BASE_TOKEN = os.getenv("PIPELINE_BASE_TOKEN", "")
TABLE_ID   = os.getenv("PIPELINE_TABLE_ID", "")

# ── 字段名（系统契约，必须和 Base 列名完全一致）──────────────────────
F_TITLE   = "需求标题"
F_STATUS  = "状态"
F_DESC    = "需求描述"
F_CLARIFY = "澄清记录"
F_PRD     = "PRD"
F_LINK    = "分支PR链接"
F_LOG     = "执行日志"
F_FAILS   = "失败次数"
F_OWNER   = "提需求人"
F_CHAT    = "会话ID"      # 飞书会话 chat_id，入站消息靠它关联到需求记录
F_WORKSPACE = "工作区"     # 可选：需求级工作区 key，对应 workspaces.json
F_AGENT   = "执行Agent"   # 可选：claude/codex/gemini/cursor，作为各阶段默认 agent
F_AGENT_CLARIFY = "澄清Agent"  # 可选：覆盖澄清阶段 agent
F_AGENT_CODE    = "开发Agent"  # 可选：覆盖开发阶段 agent
F_AGENT_REVIEW  = "ReviewAgent"  # 可选：覆盖 Review 阶段 agent

# ── 状态值（单选选项）────────────────────────────────────────────────
S_CLARIFY = "待澄清"
S_ANSWER  = "待回答"     # 等人回答（dispatcher 不碰）
S_CONFIRM = "待确认"     # 等人确认 PRD（dispatcher 不碰）
S_DEV     = "开发中"
S_REVIEW  = "Review中"
S_MERGE   = "待合并"     # 等人 merge（dispatcher 不碰）
S_DONE    = "完成"
S_BLOCKED = "已阻塞"

# dispatcher 只处理这三个；其余是人工卡点或终态。
ACTIONABLE = {S_CLARIFY, S_DEV, S_REVIEW}

# 等待人在飞书里输入的状态（message_router 收到消息时按这些状态决定怎么处理）。
HUMAN_INPUT = {S_ANSWER, S_CONFIRM}

# dispatcher 允许自动推进的状态边。人工可在飞书里改状态，但 dispatcher 自己只走这些边。
VALID_TRANSITIONS = {
    S_CLARIFY: {S_ANSWER, S_CONFIRM, S_BLOCKED},
    S_DEV: {S_REVIEW, S_BLOCKED},
    S_REVIEW: {S_DEV, S_MERGE, S_BLOCKED},
}

# ── 飞书 IM 交互（系统契约）─────────────────────────────────────────
INTAKE_PREFIX = ("需求", "需求：", "需求:")   # 私聊以此开头 → 新建需求记录
CONFIRM_WORDS = ("确认", "ok", "OK", "确定", "可以开发")  # 待确认时回复这些 → 开发中
EVENT_KEY = "im.message.receive_v1"

# ── 执行环境（部署相关 · 来自 .env）─────────────────────────────────
REPO_PATH     = Path(os.getenv("PIPELINE_REPO_PATH", ""))   # 默认代码仓库；需求级工作区优先
WORKSPACES_FILE = Path(os.getenv("PIPELINE_WORKSPACES_FILE", str(_ROOT / "workspaces.json")))
BASE_REF      = os.getenv("PIPELINE_BASE_REF", "origin/main")
WORKTREE_BASE = Path(os.getenv("PIPELINE_WORKTREE_BASE", str(_ROOT / "worktrees")))
DOSSIER_DIR   = ".pipeline"                              # 需求档案目录，建在每个 worktree/分支内
LOCKFILE      = _ROOT / ".dispatcher.lock"              # 防止常驻轮询与 listener 触发的 --once 并发处理
GH_REPO       = os.getenv("PIPELINE_GH_REPO", "")        # 留空则用 worktree 的默认 remote


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


# Push/PR 是 SCM 平台能力，不是流水线核心能力。GitLab/SVN/本地验证场景可关闭。
PUSH_ENABLED = _env_bool("PIPELINE_PUSH_ENABLED", False)
PR_ENABLED   = _env_bool("PIPELINE_PR_ENABLED", False)
# 验收门：PIPELINE_TEST_CMD 是一条 shell 命令（在 worktree 里跑，exit 0 视为通过），
# 由平台默认 shell 执行（Windows=cmd / Unix=sh），跨平台。留空则不设门。
# dispatcher 只在本分支相对 origin/main 改了"代码文件"时才跑它（纯文档改动跳过）。
TEST_CMD  = os.getenv("PIPELINE_TEST_CMD", "")
CODE_EXTS = tuple(e.strip() for e in os.getenv(
    "PIPELINE_CODE_EXTS",
    ".ts,.tsx,.js,.jsx,.py,.java,.go,.rb,.rs,.php,.cs,.kt,.c,.cpp,.h").split(",") if e.strip())

# 验收门"相对基线"：改动后没过时，去 base_ref 上再跑一次比错误数。
# GATE_RELATIVE=False 则退回"绝对门"（整仓必须绿）。GATE_ERROR_RE 用来从输出里数"错误行"。
GATE_RELATIVE = _env_bool("PIPELINE_GATE_RELATIVE", True)
GATE_ERROR_RE = re.compile(os.getenv("PIPELINE_GATE_ERROR_RE", r"(?i)\berror\b|✖|\bfailed\b"))

# ── 策略开关（可用 .env 覆盖）───────────────────────────────────────
POLL_INTERVAL   = int(os.getenv("PIPELINE_POLL_INTERVAL", "900"))   # 秒。事件驱动为主，轮询只做兜底（15min）
MAX_CONCURRENCY = int(os.getenv("PIPELINE_MAX_CONCURRENCY", "1"))   # MVP 串行。之后提高 + 文件域锁
FAILURE_LIMIT   = int(os.getenv("PIPELINE_FAILURE_LIMIT", "2"))     # 连续失败 N 次 → 已阻塞
# 各阶段 agent 超时（秒）。开发别再默认 2h——卡住会一直持锁到超时。
TIMEOUT_CLARIFY = int(os.getenv("PIPELINE_TIMEOUT_CLARIFY", "600"))    # 澄清 10min
TIMEOUT_CODE    = int(os.getenv("PIPELINE_TIMEOUT_CODE", "1800"))      # 开发 30min（够真活、卡住也不拖 2h）
TIMEOUT_REVIEW  = int(os.getenv("PIPELINE_TIMEOUT_REVIEW", "900"))     # Review 15min
AGENT_TIMEOUT   = int(os.getenv("PIPELINE_AGENT_TIMEOUT", str(max(TIMEOUT_CLARIFY, TIMEOUT_CODE, TIMEOUT_REVIEW))))  # run_agent 默认上限
# 卡死看门狗：流式引擎（cursor）多久没有任何输出事件就判定卡死并杀掉重试（秒）。
# 0 关闭。正常思考间隙约 10s，留足余量默认 120s；偶发静默卡死从此 ~2min 自愈而非干等总超时。
INACTIVITY_TIMEOUT = int(os.getenv("PIPELINE_INACTIVITY_TIMEOUT", "120"))
# 飞书实时进度卡片：运行中原地更新一张卡片的最小间隔（秒）。0 关闭进度卡片。
PROGRESS_INTERVAL = int(os.getenv("PIPELINE_PROGRESS_INTERVAL", "20"))
EXECUTION_STALE_AFTER = int(os.getenv("PIPELINE_EXECUTION_STALE_AFTER", str(AGENT_TIMEOUT + 600)))  # 认领多久算 stale（可被别人接管）
RETRY_BASE_DELAY = int(os.getenv("PIPELINE_RETRY_BASE_DELAY", "60"))  # 失败后退避秒数基数

# ── 各阶段默认引擎（.env 可覆盖；记录级 *Agent 字段优先级更高）────────
ENGINE_CLARIFY = os.getenv("PIPELINE_ENGINE_CLARIFY", "claude")
ENGINE_CODE    = os.getenv("PIPELINE_ENGINE_CODE", "cursor")
ENGINE_REVIEW  = os.getenv("PIPELINE_ENGINE_REVIEW", "gemini")

# 用户在飞书字段里可能填中文/大小写/别名，这里统一归一到 AGENT_CMDS 的 key。
AGENT_ALIASES = {
    "claude": "claude",
    "Claude": "claude",
    "claude code": "claude",
    "Claude Code": "claude",
    "codex": "codex",
    "Codex": "codex",
    "openai codex": "codex",
    "OpenAI Codex": "codex",
    "gemini": "gemini",
    "Gemini": "gemini",
    "cursor": "cursor",
    "Cursor": "cursor",
    "cursor-agent": "cursor",
    "Cursor Agent": "cursor",
}

# 各引擎的 headless 命令模板，prompt 走 stdin 传入。
# 无人值守的免交互权限 flag 已配：cursor(--force --trust)、gemini(--skip-trust --approval-mode yolo)。
# claude/codex 若用于开发阶段需补：claude → --permission-mode/--dangerously-skip-permissions；codex → approval 策略。
AGENT_CMDS = {
    "claude": ["claude", "-p", "--output-format", "text"],
    "codex":  ["codex", "exec", "-"],
    "gemini": ["gemini", "--skip-trust", "--approval-mode", "yolo", "-p", " "],
    "cursor": ["cursor-agent", "--print", "--force", "--trust", "--output-format", "text"],
}

# 启动 agent 子进程前，从环境里剔除这些会污染认证的变量。
# 典型坑：从 Claude Code 会话里启动 dispatcher 时，ANTHROPIC_BASE_URL 会把
# 订阅 OAuth 认证逼成 API-key 认证 → 401。这些变量统统不传给子进程。
SCRUB_ENV_KEYS = ["ANTHROPIC_BASE_URL", "CLAUDECODE", "ANTHROPIC_AUTH_TOKEN"]
SCRUB_ENV_PREFIXES = ["CLAUDE_CODE_", "CLAUDE_PLUGIN", "AI_AGENT"]
