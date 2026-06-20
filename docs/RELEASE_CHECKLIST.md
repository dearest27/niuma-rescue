# Release Checklist

发布前逐项确认：

- [ ] `.env` 没有进入包
- [ ] `.venv/`、`logs/`、`worktrees/`、`__pycache__/` 没有进入包
- [ ] `.env.example` 字段齐全且无真实 secret
- [ ] `requirements.txt` 包含 `lark-oapi` 和 `filelock`
- [ ] `python tools/test.py` 通过
- [ ] `python tools/verify_release.py 0.1.0` 通过
- [ ] Windows 上 `install.ps1` 实跑通过
- [ ] 飞书 app 权限说明已随包交付
- [ ] 至少一个 agent CLI 的无头模式实测可用
- [ ] `python -B src/doctor.py` 在目标机器通过或 warning 可解释
- [ ] Demo 流程：`需求@cursor：xxx` 能新增 Base 记录
- [ ] Demo 流程：确认后能进入开发、review，并创建 PR

建议版本命名：

```text
agent-pipeline-v0.1.0.zip
```
