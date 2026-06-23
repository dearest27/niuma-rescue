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

**从零克隆后的完整运行步骤（前置条件 / 飞书 Base 字段与状态选项 / 装成常驻服务）见 → [go/README.md](go/README.md)。**

## 它能做什么

- 飞书私聊机器人收需求 → 写入多维表格，进「需求池」
- **需求池多选**：发来的需求先攒在「待选择」，一张多选卡片让你勾选要做的几条、一次确认（`PIPELINE_SETUP_GATE=0` 可关，直接开跑）
- 自动澄清（产出 PRD 或追问，多条**并行**）、人工确认后进「待开发」队列
- **合批开发**：点「开始开发本批」把同工作区的多条需求合并成**一次** Agent 调用，开发完停在「待合并」由人决定 Review
- 飞书命令：`需求池` `开始开发` `看板` `状态` `配置` `健康` `统计` `周报` `重试` `解除阻塞` …
- agent 瞬时网络错自动重试、卡死看门狗、阻塞主动告警卡片
- 默认 **inline 模式**（所有需求在目标仓库当前工作树上改、人工决定提交）；也可按工作区切 `worktree`（各自独立目录/分支、自动 push/PR/MR）。本地 SQLite 记录执行锁/去重/重试

## 架构

```
飞书 IM ──长连接──▶ niuma（单进程）
                     ├─ 收消息/卡片回调 → message router → 飞书 Base 记录
                     ├─ 调度器（goroutine 并发）→ 各阶段调 CLI Agent
                     └─ git worktree + 验收门 + Review → 回写飞书状态/日志/通知
```

人工卡点：`待选择`（需求池多选）· `待回答`（补充信息）· `待确认`（确认 PRD）· `待开发`（攒批后点「开始开发本批」）· `待合并`（做 Review / 标记完成）。

## 配置

复制 `.env.example` 为 `.env` 填好飞书凭据、Base 标识、目标仓库路径。
多工作区 / SCM 见 `workspaces.example.json`。飞书应用权限与事件订阅见 [docs/feishu-app-setup.md](docs/feishu-app-setup.md)。

> 注：`docs/` 下其余文档为早期 Python 版部署说明，正在迁移；以 [go/README.md](go/README.md) 为准。

## License

见 [LICENSE](LICENSE)。
