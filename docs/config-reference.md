# 配置说明

配置文件是项目根目录 `.env`，可从 `.env.example` 复制。

## 必填项

```text
FEISHU_APP_ID
FEISHU_APP_SECRET
PIPELINE_BASE_TOKEN
PIPELINE_TABLE_ID
PIPELINE_REPO_PATH
```

`PIPELINE_BASE_TOKEN` 和 `PIPELINE_TABLE_ID` 可以由 `bootstrap.py` 自动写入。

## 代码仓库

```text
PIPELINE_REPO_PATH=/abs/path/to/repo
```

默认 Git 工作区要求：

- 是 git 仓库
- 有 `origin/main`
- 当前机器能创建 git worktree

如果主要使用 `workspaces.json` 指定 GitLab/SVN 工作区，`PIPELINE_REPO_PATH` 可以作为默认兜底仓库；实际需求会优先走 `工作区` 字段。

## 工作区

每条需求可以指定目标工作区。飞书消息示例：

```text
需求 #frontend-app：修改 README
需求 #backend-service：调整面试通知推送方式
```

本机路径维护在 `workspaces.json`，交付包提供 `workspaces.example.json`。查看当前配置：

```bash
python3 -B src/pipelinectl.py workspaces
```

优先级：Base 记录 `工作区` > 消息 `#workspace` 写入的字段 > `workspaces.json.default` > `PIPELINE_REPO_PATH`。

## 发布 / Review

```text
PIPELINE_PUSH_ENABLED=0
PIPELINE_PR_ENABLED=0
PIPELINE_GH_REPO=org/repo
```

`PIPELINE_PUSH_ENABLED` 控制开发完成后是否自动发布变更；`PIPELINE_PR_ENABLED` 控制 Review 通过后是否自动创建 GitHub PR / GitLab MR。默认关闭时，流水线会把变更保留在本地分支或 SVN 工作副本并推进到 `待合并`。

GitLab 工作区示例：

```json
{
  "path": "/absolute/path/to/gitlab/repo",
  "scm": "git",
  "base": "origin/main",
  "target_branch": "main",
  "push_enabled": true,
  "pr_enabled": true,
  "pr_provider": "gitlab",
  "gitlab_repo": "group/project",
  "test_cmd": "npm test"
}
```

要求本机已安装并登录 `glab`。如果 `gitlab_repo` 留空，`glab` 会从当前 git remote 推断项目。

SVN 工作区示例：

```json
{
  "path": "/absolute/path/to/local/svn-checkout-or-placeholder",
  "scm": "svn",
  "base": "https://svn.example.com/repos/project/trunk",
  "push_enabled": false,
  "test_cmd": "pytest -q"
}
```

SVN 模式会按需求 checkout 独立工作副本。`push_enabled=false` 时 Review 通过后只进入 `待合并`，不提交；改为 `true` 后会在 Review 通过后执行 `svn add/delete/commit`。建议先在测试 SVN 仓库验证。

## 验收门

```text
PIPELINE_TEST_CMD=npm test
```

在需求 worktree 里执行，退出码为 0 才算通过。留空则不跑验收门。

`PIPELINE_CODE_EXTS` 控制哪些扩展名被视为代码改动。纯文档改动会跳过验收门。

## Agent

```text
PIPELINE_ENGINE_CLARIFY=cursor
PIPELINE_ENGINE_CODE=cursor
PIPELINE_ENGINE_REVIEW=gemini
```

飞书里可以用 `需求@cursor：xxx` 覆盖澄清阶段 agent。表格字段 `执行Agent` 可作为记录级默认值，`澄清Agent/开发Agent/ReviewAgent` 可分别覆盖阶段。

## 轮询

```text
PIPELINE_POLL_INTERVAL=900
```

事件驱动为主，轮询只做兜底。测试时可临时调小。

## 执行锁与重试

```text
PIPELINE_EXECUTION_STALE_AFTER=7800
PIPELINE_RETRY_BASE_DELAY=60
PIPELINE_FAILURE_LIMIT=2
```

dispatcher 会把记录级执行锁、run_id、失败原因和下次重试时间写入本地 `state/runs.sqlite3`。`PIPELINE_EXECUTION_STALE_AFTER` 控制 processing 锁多久算过期；`PIPELINE_RETRY_BASE_DELAY` 控制失败后退避重试的基数；`PIPELINE_FAILURE_LIMIT` 达到上限后把需求推进到 `已阻塞`。

查看本地执行状态：

```bash
python3 -B src/pipelinectl.py runs
python3 -B src/pipelinectl.py run-events
```
