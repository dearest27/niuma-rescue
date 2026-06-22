# 配置说明

配置采用分层文件，默认值可直接跑，迁移到别人机器时按需复制 example 文件：

- `.env`：飞书凭据、Base token、默认仓库、运行策略等部署值。
- `fields.json`：飞书 Base 字段名映射，默认读取内置字段名。
- `workspaces.json`：代码工作区、Git/GitLab/SVN 配置。
- `agents.json`：默认 agent、CLI 命令模板、别名。
- `zentao.json`：禅道 Bug 导入配置。

`.env` 可从 `.env.example` 复制；其他配置可从 `*.example.json` 复制。

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

## 飞书字段映射

默认字段名与 `bootstrap.py` 创建的 Base 保持一致。如果你接入的是已有 Base，字段名不完全一样，复制一份：

```bash
cp fields.example.json fields.json
```

然后按你的 Base 列名修改右侧值：

```json
{
  "title": "需求标题",
  "status": "状态",
  "description": "需求描述",
  "clarify": "澄清记录",
  "prd": "PRD",
  "link": "分支PR链接",
  "log": "执行日志",
  "fails": "失败次数",
  "owner": "提需求人",
  "chat": "会话ID",
  "workspace": "工作区",
  "agent": "执行Agent",
  "agent_clarify": "澄清Agent",
  "agent_code": "开发Agent",
  "agent_review": "ReviewAgent",
  "external_source": "来源系统",
  "external_id": "外部ID",
  "external_url": "外部链接",
  "external_type": "外部类型",
  "sync_status": "同步状态"
}
```

也可以通过 `.env` 指定路径：

```text
PIPELINE_FIELDS_FILE=/abs/path/to/fields.json
```

字段左侧 key 是流水线内部语义，不要改；右侧 value 是飞书 Base 里的真实列名。

外部来源字段是可选字段，用于禅道等外部系统同步。旧 Base 没有这些列时，禅道导入器会把来源标记写进需求描述继续工作；新建 Base 会由 `bootstrap.py` 自动创建这些字段。

## 禅道 Bug 导入

复制配置样例：

```bash
cp zentao.example.json zentao.json
```

最小配置：

```json
{
  "base_url": "https://zentao.example.com",
  "bug_endpoint": "/api.php/v1/bugs",
  "bug_query": {
    "status": "active",
    "limit": 50
  },
  "token": "",
  "token_env": "ZENTAO_TOKEN",
  "token_header": "Token",
  "workspace": "backend-service",
  "agent": "",
  "dry_run": true
}
```

先预览：

```bash
python3 -B src/sync_zentao.py pull --dry-run
```

确认后导入：

```bash
python3 -B src/sync_zentao.py pull
```

同步器会把 Bug 写成 `待选择`，由飞书卡片继续选择 Agent / 工作区并进入后续流水线。

## 发布 / Review

```text
PIPELINE_PUSH_ENABLED=0
PIPELINE_PR_ENABLED=0
PIPELINE_GH_REPO=org/repo
```

`PIPELINE_PUSH_ENABLED` 控制开发完成后是否自动发布变更；`PIPELINE_PR_ENABLED` 控制 Review 通过后是否自动创建 GitHub PR / GitLab MR。默认关闭时，流水线会把变更保留在本地分支或 SVN 工作副本并推进到 `待合并`。

### Git 工作区模式

Git workspace 默认使用 `work_mode: "worktree"`：每条需求创建独立 worktree 和需求分支，隔离最好，适合自动 push / PR / MR。

大仓库或本地人工控制场景可以使用 `work_mode: "inline"`：

```json
{
  "path": "/absolute/path/to/large/repo",
  "scm": "git",
  "work_mode": "inline",
  "base": "origin/main",
  "push_enabled": false,
  "pr_enabled": false,
  "test_cmd": ""
}
```

inline 模式会直接在 `path` 指向的当前工作区、当前分支上开发：

- 不创建 git worktree
- 不创建或切换分支
- 不 commit
- 不 push
- 不创建 PR/MR

Review 通过后，状态仍会进入 `待合并`，但实际含义是“改动已留在当前工作区，等待人工检查、提交或丢弃”。

注意：inline 模式的 diff / Review 基于当前工作区未提交改动。若目标仓库原本就有脏文件，它们也会进入改动列表；建议在开始需求前保持工作区干净，或先把无关改动 stash / commit / 移出。

GitLab 工作区示例：

```json
{
  "path": "/absolute/path/to/gitlab/repo",
  "scm": "git",
  "work_mode": "worktree",
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

默认 agent 和 CLI 命令也可以集中放到 `agents.json`：

```bash
cp agents.example.json agents.json
```

示例：

```json
{
  "defaults": {
    "clarify": "claude",
    "code": "cursor",
    "review": "gemini"
  },
  "commands": {
    "cursor": ["cursor-agent", "--print", "--force", "--trust", "--output-format", "text"]
  },
  "aliases": {
    "Cursor Agent": "cursor"
  }
}
```

优先级：`.env` 里的 `PIPELINE_ENGINE_*` > `agents.json.defaults` > 内置默认。
命令模板优先级：`agents.json.commands` 覆盖内置 `AGENT_CMDS`。
别名优先级：`agents.json.aliases` 覆盖/补充内置别名。

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
