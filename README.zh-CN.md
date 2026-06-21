# 牛马自救中心

[English](README.md) | [简体中文](README.zh-CN.md)

把需求丢进飞书，让 Agent 替你加班。

`niuma-rescue` 是一个本地优先的多 Agent 研发流程编排器。它用飞书多维表格做需求池和状态面板，用本地常驻服务监听消息，再调度 Cursor、Claude Code、Codex、Gemini 等 CLI Agent 完成需求澄清、开发、测试、Review 和交付流转。

## 它能做什么

- 从飞书私聊机器人收集需求，自动写入多维表格。
- 支持按需求指定 Agent：`需求@cursor：xxx`。
- 支持按需求指定工作区：`需求 #backend-service：xxx`。
- 自动澄清需求，生成 PRD，并等待人工确认。
- 确认后自动创建或复用独立 worktree，让 Agent 开发。
- 自动运行测试或 lint 验收门。
- 自动进入 Review，并把结果回写飞书。
- 运行中在飞书推送实时进度卡片，原地更新，不刷屏。
- Agent 偶发卡死会按活跃度自愈：无输出超时即杀掉重试，不干等总超时。
- 飞书 `看板` 看全部在途需求，`统计` / `周报` 看运行报表；需求阻塞时主动告警。
- 本地 SQLite 记录 inbox、run_id、执行锁、失败原因和重试状态。
- 提供飞书指令和 `pipelinectl` 命令做恢复、重试、清锁、切换 Agent。

## 工作流

```text
飞书 IM
  -> src/listener.py 长连接收消息
  -> 本地 inbox.sqlite3 落库，防丢失，可 replay
  -> src/message_router.py 写入/推进 Base 记录
  -> src/dispatcher.py 处理 待澄清 / 开发中 / Review中
  -> Cursor / Claude / Codex / Gemini CLI
  -> git worktree + 测试门 + Review
  -> 飞书 Base 状态 / 日志 / 通知
```

人工卡点：

```text
待选择   新需求落地后先选澄清 Agent + 工作区，点「开始澄清」才开跑
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
- 引导生成 `workspaces.json`，支持 Git / GitLab / SVN
- 可选生成 `fields.json`，用于接入已有飞书 Base 的字段映射
- 生成 `agents.json`，用于默认 Agent、CLI 命令和别名配置
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

发完需求后，会先停在 **待选择**：弹出一张配置卡片让你点选澄清 Agent + 工作区。点 `🚀 开始澄清` 才会用所选配置在对应工作区开始澄清。内联的 `@agent #workspace` 会预选好，通常直接点开始即可；也可以回文字 `开始澄清`。

## 飞书控制指令

全局观测：

```text
看板        # 所有在途需求一览，按“需处理”优先排序；
            # 已阻塞 / 待确认 / 待合并行自带恢复按钮
统计        # 最近 24h 运行报表：agent 调用次数、各引擎、平均耗时、
            # 卡死自愈、超时、验收门、状态流转
周报        # 同上，统计窗口为最近 7 天
指令        # 帮助
```

操作当前会话最近一条未完成需求：

```text
状态        # 当前需求详情卡片，包含最近日志和恢复按钮
配置        # 点按选择执行 Agent / 工作区的交互卡片
诊断        # 当前需求摘要和本地深度诊断命令
开始澄清    # 待选择状态下启动澄清
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

推荐用 `配置` 卡片点按选择，比记文字命令更省事；文字命令保留作兜底。

需求进入 `已阻塞` 时，机器人会主动推一张告警卡片，包含阻塞原因、最近日志和一键恢复按钮。

## 运维命令

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

本地 smoke 检查：

```bash
python3 -B tools/smoke.py
python3 -B tools/smoke.py --feishu --dispatch
```

离线流程回放：

```bash
python3 -B tools/replay_flow.py
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

核心部署配置在 `.env`，可从 `.env.example` 复制：

```text
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
PIPELINE_BASE_TOKEN=app_token_xxx
PIPELINE_TABLE_ID=tblxxx
PIPELINE_REPO_PATH=/abs/path/to/your/repo
```

可选调优项：

```text
PIPELINE_INACTIVITY_TIMEOUT=120   # cursor 多久无输出判定卡死并杀掉重试，0 表示关闭
PIPELINE_PROGRESS_INTERVAL=20     # 飞书实时进度卡片原地更新的最小间隔，单位秒
```

配置分层：

- `.env`：飞书凭据、Base token、默认仓库和运行策略
- `workspaces.json`：多工作区 / GitLab / SVN
- `fields.json`：已有飞书 Base 的字段名映射
- `agents.json`：默认 Agent、CLI 命令和别名

交付包包含：

- `workspaces.example.json`
- `fields.example.json`
- `agents.example.json`

## 打包

```bash
python tools/test.py
python tools/verify_release.py 0.1.0
```

生成：

```text
dist/agent-pipeline-v0.1.0.zip
```

release zip 会排除本地配置和运行数据：

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
