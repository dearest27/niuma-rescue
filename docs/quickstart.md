# 快速部署

## 前置条件

- Python 3.10+
- Git
- 一个可用的目标代码仓库；Git 默认要求存在 `origin/main`，SVN/GitLab 可通过 `workspaces.json` 配置
- 飞书自建应用的 `APP_ID` / `APP_SECRET`
- 至少一个可无头运行的 agent CLI，例如 `cursor-agent`
- 默认不要求 GitHub/GitLab/SVN CLI 的发布能力；只有开启对应工作区的自动 PR/MR/commit 时，才需要 `gh auth login`、`glab auth login` 或 `svn`

## macOS / Linux

```bash
bash install.sh
```

脚本结束后，如果选择安装常驻服务，会注册：

```text
com.agentpipeline.listener
com.agentpipeline.dispatcher
```

## Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

脚本结束后，如果选择创建计划任务，会注册：

```text
AgentPipelineListener
AgentPipelineDispatcher
```

## 手动启动

macOS / Linux：

```bash
.venv/bin/python -B src/listener.py
.venv/bin/python -B src/dispatcher.py
```

Windows：

```powershell
.\.venv\Scripts\python.exe -B src\listener.py
.\.venv\Scripts\python.exe -B src\dispatcher.py
```

## 验证

1. 运行自检：

```bash
python src/doctor.py
```

2. 在飞书私聊机器人发送：

```text
需求@cursor：测试流水线是否可用
```

3. 观察多维表格是否新增记录，状态是否进入 `待澄清`、`待确认` 或 `待回答`。
