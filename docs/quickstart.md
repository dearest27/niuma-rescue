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

首次运行会引导生成 `workspaces.json`，可选择普通 Git、本地 GitLab MR 流程或 SVN 工作副本流程。

## Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

脚本结束后，如果选择创建计划任务，会注册：

```text
AgentPipelineListener
AgentPipelineDispatcher
```

首次运行会引导生成 `workspaces.json`，可选择普通 Git、本地 GitLab MR 流程或 SVN 工作副本流程。

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

## 常见翻车点

| 症状 | 多半原因 | 处理 |
|---|---|---|
| 每次调用 agent 都失败 / auth 报错 | agent CLI **装了但没登录** | `cursor-agent login`（或对应 CLI 登录）；`python src/doctor.py --deep` 实测 |
| 终端能跑 agent，常驻服务里报"找不到命令" | 服务进程 PATH 比终端干净 | 检查服务 PATH；重跑 `install.sh` 让它重新探测写入；见 [agent-cli-setup](agent-cli-setup.md) |
| 飞书发消息没反应 / 事件像被吞了 | 同一个 app 同时开了 Hermes gateway，两个长连接抢事件 | 停掉 Hermes gateway，只留 `listener` |
| doctor 报"缺核心字段" | Base 表结构不全 | 重跑 `python src/bootstrap.py`（已有表会跳过，加 `--force` 另建） |
| 需求卡在澄清反复打转 | 在 `待回答` 状态发了"确认"被当成澄清答案 | 先回答澄清问题，或等它产出 PRD 进 `待确认` 再确认 |
| 看不清现在在跑什么 / 卡哪了 | —— | 飞书发 `看板`（全部在途）、`状态`（当前会话）、`统计`（运行报表） |

> agent 偶发卡死已内置自愈：无输出超过 `PIPELINE_INACTIVITY_TIMEOUT`（默认 120s）会自动杀掉重试，无需手动干预。
