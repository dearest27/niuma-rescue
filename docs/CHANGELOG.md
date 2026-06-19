# Changelog

## v0.1.0

- 飞书 Base 作为需求池和状态面板
- `listener.py` 使用 lark-oapi 长连接接收飞书消息
- `dispatcher.py` 处理澄清、开发、Review、PR 创建
- 支持 `cursor/claude/codex/gemini` 作为可选 agent
- 支持 `需求@agent：xxx` 按需求选择澄清 agent
- 支持 bootstrap 自动建 Base 和字段
- 支持 doctor 部署自检
- 支持 macOS/Linux `install.sh`
- 支持 Windows `install.ps1`
