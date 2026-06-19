# 飞书应用配置

## 应用类型

使用飞书开发者后台创建一个企业自建应用。系统需要：

- 通过 OpenAPI 读写多维表格
- 通过 IM 给用户发消息
- 通过长连接接收用户私聊消息

## 必要配置

把凭据填入项目 `.env`：

```text
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
```

## 权限

至少需要覆盖这些能力：

- 多维表格创建、读取、写入字段和记录
- IM 消息发送
- 接收机器人私聊消息事件

权限名称会随飞书后台调整，按能力勾选即可。授权或发布后，再运行：

```bash
python src/bootstrap.py
python src/doctor.py
```

## 事件订阅

listener 使用官方 `lark-oapi` SDK 长连接接收：

```text
im.message.receive_v1
```

请确保机器人能接收用户私聊消息。不要同时启动 Hermes gateway 使用同一个 app，否则两个长连接消费者可能分流事件。

## Base 表结构

`bootstrap.py` 会自动创建 Base 和字段，并把结果写回 `.env`：

```text
PIPELINE_BASE_TOKEN=...
PIPELINE_TABLE_ID=...
```

核心字段：

```text
需求标题 / 状态 / 需求描述 / 澄清记录 / PRD / 分支PR链接 / 执行日志 / 失败次数 / 提需求人 / 会话ID
```

Agent 字段：

```text
执行Agent / 澄清Agent / 开发Agent / ReviewAgent
```

