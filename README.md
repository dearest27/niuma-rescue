# 牛马自救中心 (niuma)

把需求丢进飞书，让 Agent 替你加班。

飞书多维表格做需求池 + 状态机，本地一个常驻进程监听消息、调度 Cursor / Claude / Codex / Gemini
等 CLI Agent 跑完需求澄清 → 开发 → 测试 → Review → 交付流转。

> **本仓库已是 Go 实现**（单进程、单静态二进制）。早期 Python 版的完整历史保留在 `main` 分支与
> git 历史中；当前代码全部在 [`go/`](go/)。

## 快速开始

```bash
cd go
GOPROXY=https://goproxy.cn,direct go build -o niuma .
# 复用同目录上层的 .env（飞书凭据 / Base / 仓库路径），见 .env.example
./niuma
```

详细构建/运行/配置见 **[go/README.md](go/README.md)**。

## 它能做什么

- 飞书私聊机器人收需求 → 写入多维表格
- 新需求先停在「待选择」让你点选澄清 Agent + 工作区（`PIPELINE_SETUP_GATE=0` 可关，直接开跑）
- 自动澄清（产出 PRD 或追问）、人工确认后自动开发、跑相对基线验收门、自动 Review、流转到待合并
- 飞书命令：`看板` `状态` `配置` `健康` `统计` `周报` `重试` `解除阻塞` …
- agent 瞬时网络错自动重试、卡死看门狗、阻塞主动告警卡片
- 多需求并发（不同需求各自 git worktree），本地 SQLite 记录执行锁/去重/重试

## 架构

```
飞书 IM ──长连接──▶ niuma（单进程）
                     ├─ 收消息/卡片回调 → message router → 飞书 Base 记录
                     ├─ 调度器（goroutine 并发）→ 各阶段调 CLI Agent
                     └─ git worktree + 验收门 + Review → 回写飞书状态/日志/通知
```

人工卡点：`待选择`（选 Agent/工作区）· `待回答`（补充信息）· `待确认`（确认 PRD）· `待合并`（人工合并）。

## 配置

复制 `.env.example` 为 `.env` 填好飞书凭据、Base 标识、目标仓库路径。
多工作区 / SCM 见 `workspaces.example.json`。飞书应用权限与事件订阅见 [docs/feishu-app-setup.md](docs/feishu-app-setup.md)。

> 注：`docs/` 下其余文档为早期 Python 版部署说明，正在迁移；以 [go/README.md](go/README.md) 为准。

## License

见 [LICENSE](LICENSE)。
