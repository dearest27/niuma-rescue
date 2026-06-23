# niuma (Go 重写)

把 Python 版（`../src`）重写成**单进程单二进制**的 Go 实现。listener + dispatcher 合并为一个进程，
goroutine 并发；复用同一份 `.env` / 飞书 Base / `workspaces.json`。

## 构建

```bash
cd go
GOPROXY=https://goproxy.cn,direct go build -o niuma .
# 交叉编译（给别人分发，零依赖单文件）：
GOOS=linux  GOARCH=amd64 go build -o niuma-linux  .
GOOS=darwin GOARCH=arm64 go build -o niuma-mac    .
```

## 运行

复用上层目录的 `.env`（自动在 `./.env` / `../.env` 里找）：

```bash
./niuma
```

⚠️ 和 Python 版用的是**同一个飞书 app**，两者长连接会抢事件——测试 Go 版前先停掉 Python 的
`listener`（`launchctl bootout …com.agentpipeline.listener` / 停掉 dispatcher 服务）。

状态/锁存到 `<root>/state/niuma.sqlite3`（与 Python 的 `runs.sqlite3`/`inbox.sqlite3` 分开，互不干扰）。

## 已移植（与 Python 行为对齐）

- 飞书 REST（token 自动刷新、记录 CRUD、文本/卡片、原地 patch 卡片）
- 长连接（消息 + **卡片回调**，无需公网 URL；回调异步不阻塞 keepalive；60s 心跳）
- 状态机 + 三阶段（澄清/开发/Review）、相对基线验收门、退避重试、熔断阻塞
- 卡死看门狗（**出首行输出才武装**，沉默引擎不误杀）+ cursor stream-json + 全程进度心跳
- 「待选择」卡点（`PIPELINE_SETUP_GATE=0` 可关）、所有飞书命令（看板/状态/配置/健康/统计/周报/诊断…）
- inbox 去重 + runs 认领/心跳/stale、events.jsonl 遥测
- **并发**：goroutine + per-record 认领 + 信号量（`PIPELINE_MAX_CONCURRENCY`，默认 2），
  不同需求各自 worktree 并行；解决了 Python 版"全局锁、一个慢 run 堵所有人"的瓶颈

## 暂未移植（按需再补）

- SCM 只做了 git（svn / gitlab MR 待补）
- bootstrap 建表（复用 Python 已建的 Base 即可，或先用 Python 跑一次 bootstrap）
- doctor / 安装脚本（Go 版部署 = 一个二进制 + systemd/launchd unit）

## 冒烟自测

已验证：启动 → 连上 `wss://msg-frontier.feishu.cn` → 读 Base 扫描记录。
完整功能（发需求→待选择→选 Agent/工作区→开始澄清→开发→Review）请停掉 Python 后实测。
