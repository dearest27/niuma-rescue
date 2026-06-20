# 牛马自救中心

把需求丢进飞书，让 Agent 替你加班。

`niuma-rescue` 是一个本地优先的多 Agent 研发流程编排器。它用飞书多维表格做需求池和状态面板，用本地常驻服务监听消息，再调度 Cursor、Claude Code、Codex、Gemini 等 CLI Agent 完成需求澄清、开发、测试、Review 和交付流转。

## 它能做什么

- 从飞书私聊机器人收集需求，自动写入多维表格
- 支持按需求指定 Agent：`需求@cursor：xxx`
- 支持按需求指定工作区：`需求 #backend-service：xxx`
- 自动澄清需求，生成 PRD，并等待人工确认
- 确认后自动创建/复用独立 worktree，让 Agent 开发
- 自动运行测试或 lint 验收门
- 自动进入 Review，并把结果回写飞书
- 本地 SQLite 记录 inbox、run_id、执行锁、失败原因和重试状态
- 提供飞书指令和 `pipelinectl` 命令做恢复、重试、清锁、切换 Agent

## 工作流

```text
飞书 IM
  -> src/listener.py 长连接收消息
  -> 本地 inbox.sqlite3 落库，防丢失，可 replay
  -> src/message_router.py 写入/推进 Base 记录
  -> src/dispatcher.py 处理待澄清/开发中/Review中
  -> Cursor / Claude / Codex / Gemini CLI
  -> git worktree + 测试门 + Review
  -> 飞书 Base 状态/日志/通知
```

人工卡点：

```text
待回答   用户在飞书回复补充信息
待确认   用户回复「确认」后进入开发
待合并   人工检查本地分支 / PR / MR 后合并
```

## 快速开始

macOS / Linux：

```bash
bash install.sh
```

Windows PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

安装脚本会完成：

- 创建 `.venv`
- 安装 `lark-oapi`、`filelock`
- 交互写入 `.env`
- 运行 `src/bootstrap.py` 创建飞书多维表格和字段
- 运行 `src/doctor.py` 自检
- 可选注册常驻进程：macOS launchd / Windows 计划任务

手动启动：

```bash
.venv/bin/python -B src/listener.py
.venv/bin/python -B src/dispatcher.py
```

Windows：

```powershell
.\.venv\Scripts\python.exe -B src\listener.py
.\.venv\Scripts\python.exe -B src\dispatcher.py
```

## 使用方式

在飞书私聊机器人发送：

```text
需求@cursor：给 README 增加环境变量说明
```

也可以不指定 Agent：

```text
需求：修复登录页按钮样式
```

指定工作区：

```text
需求@cursor #frontend-app：新增登录页文档
```

支持的 Agent 名称：

```text
cursor / claude / codex / gemini
```

## 飞书控制指令

在机器人私聊里可以直接操作当前会话最近一条未完成需求：

```text
指令
状态
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

## 运维命令

```bash
python3 -B src/pipelinectl.py status
python3 -B src/pipelinectl.py logs
python3 -B src/pipelinectl.py inbox
python3 -B src/pipelinectl.py runs
python3 -B src/pipelinectl.py run-events
python3 -B src/pipelinectl.py workspaces
python3 -B src/pipelinectl.py doctor
python3 -B src/pipelinectl.py restart
```

手动恢复：

```bash
python3 -B src/pipelinectl.py retry-run rec_xxx --dispatch
python3 -B src/pipelinectl.py clear-lock rec_xxx
python3 -B src/pipelinectl.py unblock rec_xxx --status 开发中 --dispatch
python3 -B src/pipelinectl.py mark-done rec_xxx
python3 -B src/pipelinectl.py set-agent rec_xxx cursor --stage code
python3 -B src/pipelinectl.py set-workspace rec_xxx backend-service
```

## 配置

核心配置在 `.env`，可从 `.env.example` 复制：

```text
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
PIPELINE_BASE_TOKEN=app_token_xxx
PIPELINE_TABLE_ID=tblxxx
PIPELINE_REPO_PATH=/abs/path/to/your/repo
```

多工作区配置在 `workspaces.json`，交付包提供 `workspaces.example.json`。

## 打包

```bash
python tools/test.py
python tools/verify_release.py 0.1.0
```

生成：

```text
dist/agent-pipeline-v0.1.0.zip
```

release zip 会排除：

```text
.env
.venv/
logs/
state/
worktrees/
dist/
workspaces.json
```

## 文档

- [快速部署](docs/quickstart.md)
- [飞书应用配置](docs/feishu-app-setup.md)
- [配置说明](docs/config-reference.md)
- [Agent CLI 配置](docs/agent-cli-setup.md)
- [运维与排错](docs/operations.md)
- [Windows 安装说明](docs/windows-install.md)
- [交付与打包清单](docs/delivery.md)

## License

MIT License. See [LICENSE](LICENSE).
