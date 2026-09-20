# 多用户流式聊天与工具展示

本次实现使用现有 `/v1` API、共享 Worker 和 Repository。生产存储沿用
[TDSQL 存储说明](tdsql-storage.md)，本地验收使用同一存储状态机的内存实现。
个人模式 `/api/console/chat` 保留，服务模式的助手配置由平台定义决定。

## 事件契约

先调用 `POST /v1/runs`，使用稳定的 `request_id` 避免重复提交。返回 `id` 为
`run_id`，`session_id` 为内部会话 UUID。外部 sessionid 不变，不应改成内部 ID。
随后订阅 `GET /v1/runs/{id}/events`。BFF 注入服务凭据和已认证用户身份；
浏览器不持有服务令牌。Vite 开发代理仅用于本机调试，不能替代生产 BFF 认证。

SSE `id` 是按任务单调递增的持久化序号。客户端应用事件成功后推进游标，
重连携带 `Last-Event-ID`，忽略重复序号。断线不会取消任务；取消需要显式调用
`POST /v1/runs/{id}/cancel`。运行的 `terminal` 事件及 SSE `event: end` 为任务
终态；Runtime 的 `response completed` 仅代表模型响应完成，不能提前宣布任务成功。

保留 Runtime 的 message/content/response 载荷，增加以下事件：

| type | 主要字段 | 说明 |
|---|---|---|
| `tool` | tool_call_id, name, arguments, status, output? | 工具生命周期与权威结果 |
| `tool_output` | tool_call_id, stream, text | 有界 stdout/stderr 增量 |
| `approval` | tool_call_id, approval | 持久化审批及决定，待审批事件包含工具名和参数 |
| `preview_start/update/clear` | run_id, invocation_id, attempt_id, revision?, text?, reason? | TL 临时预览，可撤回，不用于触发工具 |
| `terminal` | status | completed/failed/cancelled/interrupted |

工具状态：preparing → awaiting_approval（如需要）→ running →
completed/failed/denied/cancelled/unknown。模型工具调用 ID 贯穿 Agent、
网关、审批、控制器和沙箱。未知结果禁止自动重放。非零 Shell 退出码显示失败，
不能因为 HTTP 200 而显示成功。审批批准只允许派发业务操作，不改变沙箱边界。

文本/工具边界前的事件先持久化，避免工具卡片跑到解释文本之前。
Shell 流由沙箱输出，Controller 校验执行代次后入库，Worker 不执行用户命令。
其他工具仍可以只返回最终结果；文件和浏览器结果不伪造百分比进度。

## 历史

`GET /v1/sessions/{id}/messages` 返回分页记录，使用 `seq` 作为 `after`。
每个已结束任务附有 `payload.type=run_timeline` 的助手历史记录，包含 `run_id`、
`status` 和压缩后的 `events`。同一任务有此记录时，前端优先用它重建助手部分，
避免再渲染该任务其他助手快照造成重复。

摘要保留正式文本、工具参数、结果与有界日志尾部，不保存临时预览；详细 SSE
清理后仍能读取。大结果仍通过受用户归属保护的文件记录获取下载地址。未结束的
任务可由历史中的 run_id 查询状态并从事件序号 0 重新构建当前轮。

## 本地可重复验收

在仓库根目录启动模拟服务：

```bash
# macOS / Linux
.venv/bin/python scripts/server_stream_demo.py --port 8090
```

```powershell
# Windows (PowerShell)
.venv\Scripts\python.exe scripts\server_stream_demo.py --port 8090
```

该入口仅监听 127.0.0.1，使用内存数据库，退出即清空；不执行真实 Shell、不调用
付费模型，不提供文件存储。默认本地演示令牌为
`qwenpaw-stream-demo-local-token-32`，可通过 `QWENPAW_SERVER_SERVICE_TOKEN`
覆盖。前端启动与开发代理设置见 [Native Console README](../test-tools/native-console/README.md)。
真实模型/工具服务应使用独立平台助手定义、TDSQL、Controller 与沙箱部署。

自动化验收命令（仓库根目录）：

```bash
# macOS / Linux
QWENPAW_TEST_BROWSER=1 .venv/bin/python -m pytest tests/unit/server tests/integration/test_server_tl_proxy.py tests/integration/test_server_stream_pipeline.py -q
```

```powershell
# Windows (PowerShell)
$env:QWENPAW_TEST_BROWSER = "1"
.venv\Scripts\python.exe -m pytest tests/unit/server tests/integration/test_server_tl_proxy.py tests/integration/test_server_stream_pipeline.py -q
```

```bash
cd test-tools/native-console
npm run build
npm test
npm run test:e2e
```

真实 socket 测试用同步关卡验证“模型未完成前已收到正文”“工具未完成前已收到
日志”，而非仅检查响应的 Content-Type。覆盖审批归属、断线恢复、工具调用去重、
失败状态、TL 预览，以及 SSE 清理后的历史恢复。沙箱流测试独立验证进程输出、
取消、超时和去重。

这组功能验收不等于真实 TDSQL/Kubernetes 生产容量验收；当前无 TDSQL 测试库，
对应测试必须显示跳过，不能记作通过。100 任务/500 SSE 持续 30 分钟的生产基线
需在实际部署环境另行执行。

## 本次验收记录（2026-09-18）

- 后端、TL 代理、预览与真实 HTTP 沙箱流组合测试：82 passed、17 skipped。
  未配置的数据库集成项目明确跳过；没有使用内存结果替代真实 TDSQL 验收。
- 真实浏览器调试发现并修复了审批等待期间 heartbeat 缺少消息 ID 导致历史压缩失败的问题，新增专门回归测试。
- Shell 集成测试使用实际子进程和 loopback HTTP，证明日志在命令完成前到达。
  这是本地链路测试，不能证明 Kubernetes Pod 的生产隔离和网络策略。
- 本次未调用付费模型；TL 代理测试使用受控上游。此前真实 TL 的 HTTP 502 不属于本次模拟测试已解决的范围。
- Native Console：43 项单元测试、12 项 Chrome E2E 通过；TypeScript/Vite 构建及依赖白名单检查通过。
  E2E 包含批准、拒绝、取消，验证审批可点击、工具卡片终态和异常不冒泡。
- 真实 Chrome 连接本地演示 API/Worker，完成正文、审批、日志、终态及刷新后历史恢复验证。

执行分工：`luna__stream_console` 实现前端初版，主代理接管修复并完成集成；
`luna__sandbox_stream` 实现沙箱日志流并完成取消/输出限制修正；
`luna__chat_finalize` 修复事件归并并补充回归。主代理负责事件协议、持久化、
工具 ID 贯通、审批/故障语义、真实 HTTP 测试、浏览器验收及文档。
- 刷新浏览器后重新连接，选择待审批会话并加载历史，成功恢复原 run 的审批卡片；批准后原任务完成。全程没有再次提交消息，覆盖多分钟等待期间的心跳。

### 个人模式 TL 预览修复

Native Console 的 `/api/console/chat` 请求必须显式携带
`request_context.capabilities.tl_preview=true`。TL 工具协议先收集并校验完整 JSON，
正式正文在校验后提交；未声明预览能力时，用户会看到最后一次性输出。
个人模式现在也渲染可撤回的 preview 事件，不再仅在 `/v1` 时间线显示。
新增浏览器测试保持响应流打开，验证终态到达前已显示预览，确认后清除预览并显示正式正文。
