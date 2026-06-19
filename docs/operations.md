# 运维与排错

## 进程

需要两个常驻进程：

```text
src/listener.py     收飞书消息，写 Base，事件触发 dispatcher --once
src/dispatcher.py   兜底轮询，处理待澄清/开发中/Review中
```

常驻服务应使用 `python3 -B`，并设置：

```text
PYTHONDONTWRITEBYTECODE=1
```

这样不会读写 `__pycache__`，避免服务重启后继续加载旧 bytecode。


## 统一运维入口

优先使用 `src/pipelinectl.py` 看状态和排障：

```bash
python3 -B src/pipelinectl.py status
python3 -B src/pipelinectl.py logs
python3 -B src/pipelinectl.py once
python3 -B src/pipelinectl.py inbox
python3 -B src/pipelinectl.py replay --failed
python3 -B src/pipelinectl.py runs
python3 -B src/pipelinectl.py run-events
python3 -B src/pipelinectl.py doctor
python3 -B src/pipelinectl.py restart
```

`status` 会同时展示 launchd 服务状态、本地健康心跳和最近事件。健康状态写在 `state/` 目录，属于运行时产物，不进入交付包。

`inbox` 查看本地 SQLite 收件箱；`replay <id>` 重放某条消息，`replay --failed` 重放失败消息。

`runs` 查看 dispatcher 的记录级执行锁、当前 run_id、失败原因和下次重试时间；`run-events <record_id>` 查看某条需求的本地执行事件。

## 飞书控制指令

在机器人私聊里可以对当前会话最近一条未完成需求发指令：

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

说明：

- `状态` 只查看当前需求摘要。
- `重试` 会清理本地执行锁/重试等待；如果当前状态是 `待澄清/开发中/Review中`，listener 会立即触发一轮 dispatcher。
- `清锁` 只清本地锁，不改飞书状态。
- `解除阻塞` 默认回到 `开发中`，也可以指定 `待澄清/开发中/Review中`。
- `完成` 会人工标记为 `完成`，适合已经线下合并/发布的需求。

## 手动恢复命令

命令行恢复按 `record_id` 操作：

```bash
python3 -B src/pipelinectl.py retry-run rec_xxx
python3 -B src/pipelinectl.py retry-run rec_xxx --dispatch
python3 -B src/pipelinectl.py clear-lock rec_xxx
python3 -B src/pipelinectl.py unblock rec_xxx --status 开发中 --dispatch
python3 -B src/pipelinectl.py mark-done rec_xxx
python3 -B src/pipelinectl.py set-status rec_xxx Review中 --dispatch
python3 -B src/pipelinectl.py set-agent rec_xxx cursor --stage code
python3 -B src/pipelinectl.py set-workspace rec_xxx backend-service
```

默认恢复命令只修改状态/锁；带 `--dispatch` 时才会立即执行 `src/dispatcher.py --once`。

## 日志

默认日志目录：

```text
logs/listener.log
logs/dispatcher.log
```

Windows 计划任务也会写入同一目录。

## 常见问题

### 飞书发消息没有新增记录

检查：

- `src/listener.py` 是否在运行
- 飞书 app 是否订阅 `im.message.receive_v1`
- 消息是否以 `需求` 开头
- 是否同时启动了 Hermes gateway 分流事件
- `logs/listener.log` 是否有异常

### 记录新增了但不流转

检查：

- `src/dispatcher.py` 是否在运行
- `logs/dispatcher.log`
- Base 记录状态是否是 `待澄清/开发中/Review中`
- `python3 -B src/pipelinectl.py runs` 是否显示 `processing` 锁或 `failed` 等待重试
- agent CLI 是否能无头运行

### agent 命令找不到

常驻服务 PATH 不等于终端 PATH。重新运行安装脚本，或手动把 CLI 所在目录加入服务环境。

### 2 小时后 API 401

当前 `lark.py` 会在 token 过期前自动刷新。如果仍出现 401，检查 `.env` 的 `FEISHU_APP_ID/FEISHU_APP_SECRET` 是否正确。

### 重复记录

系统有三层防护：event_id 去重、快速 ACK、同会话同描述在途记录幂等。如果调试时反复重启 listener，仍建议先看 Base 里是否已有同描述在途记录。

## 停止服务

macOS launchd：

```bash
launchctl unload ~/Library/LaunchAgents/com.agentpipeline.listener.plist
launchctl unload ~/Library/LaunchAgents/com.agentpipeline.dispatcher.plist
```

Windows 计划任务：

```powershell
Stop-ScheduledTask -TaskName AgentPipelineListener
Stop-ScheduledTask -TaskName AgentPipelineDispatcher
```
