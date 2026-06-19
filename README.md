# agent-pipeline

飞书多维表格驱动的本地多 agent 研发流水线模板。

用户在飞书私聊机器人发送需求，系统把需求写入多维表格，再由 `src/listener.py` 触发 `src/dispatcher.py` 调用本地 agent CLI 完成澄清、开发、测试、review，并按配置选择是否 push/建 PR，最后把状态和日志写回飞书。

## 工作流

```text
飞书 IM
  -> src/listener.py 长连接收消息
  -> message_router.py 写入/推进 Base 记录
  -> src/dispatcher.py 处理待澄清/开发中/Review中
  -> cursor/claude/codex/gemini CLI
  -> git worktree + 测试门 + 可选 push/PR
  -> 飞书 Base 状态/日志/通知
```

人工卡点：

```text
待回答   用户在飞书回复补充信息
待确认   用户回复「确认」后进入开发
待合并   人工检查本地分支 / PR 后合并
```

## 快速开始

常用运维入口：

```bash
python3 -B src/pipelinectl.py status
python3 -B src/pipelinectl.py logs
python3 -B src/pipelinectl.py restart
```

查看工作区：

```bash
python3 -B src/pipelinectl.py workspaces
```


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
- `src/bootstrap.py` 创建飞书多维表格和字段
- `src/doctor.py` 自检
- 可选注册常驻进程（macOS launchd / Windows 计划任务）

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

在飞书私聊机器人：

```text
需求@cursor：给 README 增加环境变量说明
```

也可以不指定 agent：

```text
需求：修复登录页按钮样式
```

支持的 agent 名称：

```text
cursor / claude / codex / gemini
```

常用控制指令：

```text
指令
状态
重试
清锁
解除阻塞
重新澄清
完成
切换Agent cursor
切换工作区 qtcc
```

## 交付内容

交付给别人时保留：

```text
install.py
install.sh
install.ps1
requirements.txt
.env.example
src/
tools/
docs/
README.md
LICENSE
```

不要交付：

```text
.env
.venv/
__pycache__/
logs/
worktrees/
.dispatcher.lock
scripts/windows/
```

## 更多文档

- [快速部署](docs/quickstart.md)
- [飞书应用配置](docs/feishu-app-setup.md)
- [配置说明](docs/config-reference.md)
- [Agent CLI 配置](docs/agent-cli-setup.md)
- [运维与排错](docs/operations.md)
- [Windows 安装说明](docs/windows-install.md)
- [交付与打包清单](docs/delivery.md)

## 打包

```bash
python tools/package_release.py 0.1.0
```

生成：

```text
dist/agent-pipeline-v0.1.0.zip
```

## License

MIT License. See [LICENSE](LICENSE).
