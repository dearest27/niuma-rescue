# 交付与打包清单

## 交付介质

建议同时交付：

```text
1. 源码仓库
2. release zip
3. README + docs
4. demo 截图或录屏
5. 版本说明 CHANGELOG
```

## release zip 应包含

```text
README.md
.env.example
requirements.txt
install.py
install.sh
install.ps1
src/
tools/
docs/
```

`hermes-plugin/` 和 `scripts/hermes/` 属于历史 Hermes 接入。默认新部署走 `src/listener.py`，release zip 不包含旧 Hermes 接入。

生成 release zip：

```bash
python tools/package_release.py 0.1.0
```

输出：

```text
dist/agent-pipeline-v0.1.0.zip
```

## 不应包含

```text
.env
.venv/
__pycache__/
logs/
worktrees/
.dispatcher.lock
scripts/windows/
*.pyc
.DS_Store
```

## 发布前验收

```bash
bash -n install.sh
python -B -m py_compile src/*.py install.py
```

Windows 机器上再跑：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

## 目标体验

使用者拿到包后，应能在 30 分钟内完成：

```text
填 .env
bootstrap 建表
doctor 自检
启动 listener + dispatcher
飞书发送 需求@cursor：xxx
Base 新增记录并进入澄清流程
```
