# Agent CLI 配置

系统通过本地 CLI 无头调用 agent，prompt 走 stdin。

当前默认命令在 `config.py` 的 `AGENT_CMDS`：

```text
cursor: cursor-agent --print --force --trust --output-format text
gemini: gemini --skip-trust --approval-mode yolo -p " "
claude: claude -p --output-format text
codex:  codex exec -
```

执行细节在 `src/agent_adapters.py`。新增 agent 时通常需要两步：

```text
1. 在 config.py 里加入 AGENT_CMDS 和 AGENT_ALIASES
2. 在 src/agent_adapters.py 里加入对应 Adapter，补充认证失败、限流、空输出等错误识别
```

## 安装要求

至少安装一个你准备使用的 agent CLI，并完成登录或 API key 配置。

建议先在目标仓库里手动测试：

```bash
cursor-agent --print --force --trust --output-format text
gemini --skip-trust --approval-mode yolo -p " "
claude -p --output-format text
codex exec -
```

## PATH

常驻进程的 PATH 往往比交互终端干净。安装脚本会尽量探测 `cursor-agent/gemini/claude/codex/gh/git/node/npm` 所在目录并写入服务配置。

如果 agent 在终端能跑、服务里找不到命令，优先检查服务环境的 PATH。

## 权限模式

Cursor 和 Gemini 当前已经配置了较宽的无头权限。Claude / Codex 如果要承担开发阶段，需要按你的安全策略补充无头权限参数。

建议先只让一个 agent 跑通，再逐步打开更多 agent。
