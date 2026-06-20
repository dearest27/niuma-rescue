# Windows 安装说明

## 运行安装器

在 PowerShell 中进入项目目录：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

安装器会：

- 创建 `.venv`
- 安装依赖
- 交互写 `.env`
- 生成默认 `workspaces.json`
- 运行 `src/bootstrap.py`
- 运行 `src/doctor.py`
- 可选创建登录启动的计划任务

## 计划任务

可选创建：

```text
AgentPipelineListener
AgentPipelineDispatcher
```

查看日志：

```text
logs\listener.log
logs\dispatcher.log
```

停止：

```powershell
Stop-ScheduledTask -TaskName AgentPipelineListener
Stop-ScheduledTask -TaskName AgentPipelineDispatcher
```

删除：

```powershell
Unregister-ScheduledTask -TaskName AgentPipelineListener -Confirm:$false
Unregister-ScheduledTask -TaskName AgentPipelineDispatcher -Confirm:$false
```

## 注意事项

- Windows 上 agent CLI 可能是 `.cmd`，dispatcher 会通过 `shutil.which` 解析。
- `PIPELINE_REPO_PATH` 使用 Windows 路径，例如 `C:\Users\you\repo`。
- `PIPELINE_TEST_CMD` 可留空；填写后会作为验收门命令在 worktree 中执行。
- 如果 PowerShell 执行策略拦截脚本，用 `-ExecutionPolicy Bypass` 启动。
- 如果计划任务启动后找不到 `git/gh/cursor-agent`，重新运行安装器或手动调整任务里的 PATH。
