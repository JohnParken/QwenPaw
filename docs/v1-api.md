# QwenPaw Service v1 API

`/v1` 是多用户服务的主接口。API 只接受 BFF 转发的服务令牌和用户身份，不接受浏览器
直接登录。除健康检查外，请求都带：

```text
Authorization: Bearer <QWENPAW_SERVER_SERVICE_TOKEN>
X-QwenPaw-User: <user-id>
```

服务令牌错误为 `401`；缺少或过长的用户头为 `400`；资源不存在为 `404`；冲突为 `409`。
所有用户可见资源按 `X-QwenPaw-User` 隔离。

## 提交运行

`POST /v1/runs` 返回 `202`。请求体严格接受以下字段：

```json
{
  "usrid": "alice",
  "sessionid": "s1",
  "channelid": "web",
  "request_id": "req-1",
  "message": "你好",
  "debug": false,
  "attachments": []
}
```

`usrid` 必须等于用户头；文本最长 100,000 字符，附件最多 20 个。相同用户重试同一
`request_id` 会复用原运行；同一请求 ID 对应不同载荷返回 `409`。响应是运行记录，记录的
`id` 为 `run_id`，`session_id` 为内部会话 UUID（不是请求的外部 `sessionid`）。
以下为响应中的关键字段示例，实际记录还包含配置版本、输入及执行信息：

```json
{"id":"<run-uuid>","session_id":"<session-uuid>","status":"queued"}
```

状态包括 `queued`、`running`、`waiting_approval` 和终态 `completed`、`failed`、
`cancelled`、`interrupted`；请求取消不保证立即进入终态。

## 查询、取消与事件

| 方法 | 路径 | 成功 |
| --- | --- | --- |
| GET | `/v1/runs/{run_id}` | `200`，运行记录 |
| POST | `/v1/runs/{run_id}/cancel` | `200`，取消后的运行记录 |
| GET | `/v1/runs/{run_id}/events` | `200 text/event-stream` |
| GET | `/v1/sessions?limit=100` | `200`，最多 100 个会话 |
| GET | `/v1/sessions/{session_id}/messages?after=0&limit=100` | `200`，消息分页 |

SSE 每条持久化事件包含 `id` 和 JSON `data`；`data` 保留 Runtime 载荷并可能增加
`persisted_at`。结束时发送 `event: end`，其 data 为 `{"status":"completed"}` 等终态。
断线后把最后收到的序号放入 `Last-Event-ID` 重连；非法游标返回 `400`。连接断开不会取消
运行。代理必须关闭缓冲并透传 `Last-Event-ID`。消息、工具、审批和临时预览事件的
载荷与展示规则见 [流式工具契约](streaming-tool-console.md)。

```bash
curl -N http://localhost:8090/v1/runs/RUN_ID/events \
  -H "Authorization: Bearer $QWENPAW_SERVER_SERVICE_TOKEN" \
  -H 'X-QwenPaw-User: alice' -H 'Last-Event-ID: 0'
```

## 审批与文件

`POST /v1/approvals/{approval_id}/decision` 请求体为 `{"approved":true}` 或
`{"approved":false}`，成功返回 `200`；只有所属用户可决定，超时会取消运行。

`POST /v1/files` 使用 multipart 字段 `file`，成功 `201`；超过配置上限返回 `413`。
`GET /v1/files/{file_id}` 返回文件记录和有效期下载 URL，`GET /v1/files/{file_id}/content`
返回二进制内容；`DELETE /v1/files/{file_id}` 和 `DELETE /v1/memory` 成功返回 `204`。
`POST /v1/sessions/{session_id}/files` 请求体为 `{"file_id":"..."}`，会话忙时返回
`409`。任务附件在运行开始时校验归属；TL 文本解析另受 10 MiB/100,000 字符限制。

## 助手与运维

`GET /v1/assistant` 返回当前助手名称、版本、模型协议、附件能力和工具目录。
`GET /health` 返回 `{"status":"ok","mode":"server"}`；`GET /ready` 返回
`{"status":"ready"}` 或数据库未就绪错误。`GET /internal/metrics` 只需服务令牌，返回
Prometheus 文本指标。

## 个人兼容接口

个人服务仍提供 `POST /api/console/chat`，成功为 `200 text/event-stream`。请求体使用
`AgentRequest`：`input` 消息列表、可选 `session_id`/`user_id`、`stream`（默认 `true`）和
`metadata`；额外字段允许保留。可用 `reconnect=true` 重新连接运行中的流，重复提交同一
聊天会返回 `409`；`POST /api/console/chat/stop?chat_id=...` 停止运行。该接口不等同于
`/v1` 的持久化运行协议。

