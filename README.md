# Niuma Rescue

Throw requirements into Feishu and let agents take the overtime shift.

`niuma-rescue` is a local-first multi-agent engineering workflow orchestrator. It uses Feishu Base as the requirement pool and status board, runs local long-lived services to listen for Feishu messages, and dispatches CLI agents such as Cursor, Claude Code, Codex, and Gemini to clarify requirements, implement changes, run tests, review, and hand off delivery.

## What It Does

- Collects requirements from a Feishu bot DM and writes them into Feishu Base.
- Supports per-requirement agent selection: `需求@cursor：xxx`.
- Supports per-requirement workspace selection: `需求 #backend-service：xxx`.
- Clarifies requirements automatically, generates a PRD, and waits for human confirmation.
- Creates or reuses an isolated worktree after confirmation, then asks an agent to implement the change.
- Runs a configurable test or lint gate.
- Moves into review automatically and writes the result back to Feishu.
- Sends live progress cards in Feishu while agents are running, updating in place instead of spamming messages.
- Recovers from stuck agents by activity timeout: if an agent produces no output for too long, it is killed and retried.
- Provides Feishu commands such as `看板`, `统计`, and `周报`; blocked requirements push proactive alert cards.
- Stores inbox messages, run IDs, execution locks, failure reasons, and retry state in local SQLite.
- Provides Feishu commands and `pipelinectl` commands for retry, recovery, lock clearing, and agent switching.

## Workflow

```text
Feishu IM
  -> src/listener.py receives messages over a long-lived connection
  -> local inbox.sqlite3 persists messages for replay and loss prevention
  -> src/message_router.py creates or advances Feishu Base records
  -> src/dispatcher.py handles 待澄清 / 开发中 / Review中
  -> Cursor / Claude / Codex / Gemini CLI
  -> git worktree + test gate + review
  -> Feishu Base status / logs / notifications
```

Human checkpoints:

```text
待选择   choose clarify agent + workspace, then click "开始澄清"
待回答   the user replies in Feishu with additional information
待确认   the user replies "确认" to start development
待合并   a human checks the local branch / PR / MR and merges
```

## Quick Start

macOS / Linux:

```bash
bash install.sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

The installer will:

- create `.venv`
- install `lark-oapi` and `filelock`
- write `.env` interactively
- guide you through creating `workspaces.json` for Git, GitLab, or SVN
- optionally create `fields.json` for existing Feishu Base field mappings
- create `agents.json` for default agents, CLI commands, and aliases
- run `src/bootstrap.py` to create the Feishu Base and fields
- run `src/doctor.py` for self-checks
- optionally register long-running services: macOS launchd / Windows Task Scheduler

Manual startup:

```bash
.venv/bin/python -B src/listener.py
.venv/bin/python -B src/dispatcher.py
```

Windows:

```powershell
.\.venv\Scripts\python.exe -B src\listener.py
.\.venv\Scripts\python.exe -B src\dispatcher.py
```

## Usage

Send a message to the Feishu bot:

```text
需求@cursor：给 README 增加环境变量说明
```

You can omit the agent:

```text
需求：修复登录页按钮样式
```

Specify a workspace:

```text
需求@cursor #frontend-app：新增登录页文档
```

Supported agent names:

```text
cursor / claude / codex / gemini
```

After intake, the requirement first stops at **待选择**. A configuration card lets you choose the clarify agent and workspace. Click `🚀 开始澄清` to start clarification in the selected workspace. Inline `@agent #workspace` hints are preselected, so you can usually just click start. Text command `开始澄清` is also supported.

## Feishu Commands

Global observation commands:

```text
看板        # all in-flight requirements, sorted by "needs attention" first;
            # blocked / confirmation / merge rows include recovery buttons
统计        # last 24h runtime report: agent calls, engines, average duration,
            # stuck-agent recovery, timeouts, test gates, state transitions
周报        # same report over the last 7 days
指令        # help
```

Commands for the latest unfinished requirement in the current chat:

```text
状态        # current requirement card with recent logs and recovery buttons
配置        # interactive card for agent / workspace selection
诊断        # current requirement summary and local deep-diagnose command
开始澄清    # start clarification from 待选择
重试
清锁
解除阻塞
解除阻塞 待澄清
重新澄清
完成
切换Agent cursor
切换澄清Agent claude
切换开发Agent cursor
切换ReviewAgent gemini
切换工作区 backend-service
设置状态 开发中
```

The `配置` card is usually easier than memorizing text commands. Text commands remain available as a fallback.

When a requirement enters `已阻塞`, the bot proactively sends an alert card with the blocking reason, recent logs, and one-click recovery actions.

## Operations

```bash
python3 -B src/pipelinectl.py status
python3 -B src/pipelinectl.py diagnose
python3 -B src/pipelinectl.py diagnose rec_xxx
python3 -B src/pipelinectl.py logs
python3 -B src/pipelinectl.py inbox
python3 -B src/pipelinectl.py runs
python3 -B src/pipelinectl.py run-events
python3 -B src/pipelinectl.py workspaces
python3 -B src/pipelinectl.py doctor
python3 -B src/pipelinectl.py restart
```

Local smoke checks:

```bash
python3 -B tools/smoke.py
python3 -B tools/smoke.py --feishu --dispatch
```

Offline flow replay:

```bash
python3 -B tools/replay_flow.py
```

Manual recovery:

```bash
python3 -B src/pipelinectl.py retry-run rec_xxx --dispatch
python3 -B src/pipelinectl.py clear-lock rec_xxx
python3 -B src/pipelinectl.py unblock rec_xxx --status 开发中 --dispatch
python3 -B src/pipelinectl.py mark-done rec_xxx
python3 -B src/pipelinectl.py set-agent rec_xxx cursor --stage code
python3 -B src/pipelinectl.py set-workspace rec_xxx backend-service
```

## Configuration

Core deployment settings live in `.env`, which can be copied from `.env.example`:

```text
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
PIPELINE_BASE_TOKEN=app_token_xxx
PIPELINE_TABLE_ID=tblxxx
PIPELINE_REPO_PATH=/abs/path/to/your/repo
```

Optional runtime tuning:

```text
PIPELINE_INACTIVITY_TIMEOUT=120   # seconds without cursor output before killing and retrying; 0 disables
PIPELINE_PROGRESS_INTERVAL=20     # minimum interval, in seconds, for in-place Feishu progress card updates
```

Configuration layers:

- `.env`: Feishu credentials, Base token, default repository, and runtime policy
- `workspaces.json`: multi-workspace / GitLab / SVN configuration
- `fields.json`: field-name mapping for an existing Feishu Base
- `agents.json`: default agents, CLI commands, and aliases

The release package includes:

- `workspaces.example.json`
- `fields.example.json`
- `agents.example.json`

## Packaging

```bash
python tools/test.py
python tools/verify_release.py 0.1.0
```

Generated artifact:

```text
dist/agent-pipeline-v0.1.0.zip
```

The release zip excludes local-only files:

```text
.env
.venv/
logs/
state/
worktrees/
dist/
workspaces.json
fields.json
agents.json
```

## Documentation

- [Quickstart](docs/quickstart.md)
- [Feishu app setup](docs/feishu-app-setup.md)
- [Configuration reference](docs/config-reference.md)
- [Agent CLI setup](docs/agent-cli-setup.md)
- [Operations and troubleshooting](docs/operations.md)
- [Windows installation](docs/windows-install.md)
- [Delivery and release checklist](docs/delivery.md)

## License

MIT License. See [LICENSE](LICENSE).
