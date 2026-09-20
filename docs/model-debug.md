# 模型报文与 Debug 日志

Native Console 的“模型日志”展示当前 TL Provider 实际发送与接收的报文，而不是浏览器提交给 QwenPaw 的聊天请求。个人 Console 和 `/v1` 服务均支持。打开页面上的 Debug 开关后重新发送消息，展开日志查看 JSON；可按事件/级别筛选、清空或导出。

## 开启

服务端需设置 `QWENPAW_MODEL_DEBUG=1`。这是服务进程级开关，不允许浏览器修改 Worker 环境变量。请求也必须明确启用 Debug：

- 个人 Console：`meta.request_context.capabilities.model_debug=true`，与现有 `tl_preview` 能力一起发送。
- `/v1/runs`：增加可选字段 `debug: true`，默认关闭；该字段绑定到任务快照。

本地 v1 一键启动（在 `test-tools/native-console` 执行）：

```sh
QWENPAW_MODEL_DEBUG=1 npm run service:tl
```

个人后端（在仓库根目录执行；已有进程需重启）：

```sh
QWENPAW_MODEL_DEBUG=1 .venv/bin/python -m qwenpaw app --port 8088 --log-level debug
```

`server_tl_local.py` 在开启该环境变量后启用后端 DEBUG 输出。其他部署同时配置应用日志级别为 debug，以查看 SSE 投递等详细日志。ERROR 日志不依赖 Debug 开关。日志输出到服务进程的标准错误，可由现有日志采集器收集。

## 日志内容

| 事件 | 内容 |
| --- | --- |
| `request` | TL init_session / chat 实际请求，含完整 URL、最终生成的请求头、系统提示词和用户报文 |
| `response_headers` / `response_body` | HTTP 状态、类型及非流式响应 |
| `model_response` | 拼接后的模型响应文本、终止方式及截断标记 |
| `tool_prompt_conversion` | 消息/工具 schema 转为 TL 系统提示词和用户报文 |
| `tool_response_conversion` | 模型返回的工具协议 JSON 转为内部工具/文本 blocks |
| `tool_dispatch` / `tool_result` | v1 工具网关派发参数、调用 ID、结果与状态 |
| `sse_event` | TL 原始 SSE 帧解析后的事件与 data（前端按 HTTP 请求采样） |
| `json_correction` | JSON 格式纠错的后续输入与尝试次数 |
| `error` | 失败阶段、错误类型、请求/尝试标识；HTTP、SSE、转换和执行异常 |

后端另输出 SSE 开始、批量持久化、投递游标、断开/结束，以及投递异常、Worker 心跳异常等日志。用户取消记录为取消，不当作 ERROR。

## 隔离、保留和限制

- `model_log` 事件附带 `run_id`、`invocation_id`、事件 ID、时间和级别；HTTP 事件还带 `request_id`、`attempt_id`，用于关联排查。
- 使用请求作用域传递日志，沿原有已授权 SSE 流返回。v1 读取日志仍校验任务归属；不存在全用户日志接口。
- 个人模式日志为实时旁路，断线后不补发；v1 日志进入任务事件表，按原有事件保留策略处理，可通过 SSE 游标重放。它们不会加入聊天历史或模型上下文。
- 密钥、授权头、Cookie、自定义请求头的值、常见密码字段，以及 URL 中的凭据和查询参数会脱敏。提示词、附件提取文本和模型正文仍属于业务数据，仅在需要排查时开启；导出文件也包含这些业务数据。
- 单条报文最多 32,768 字符，每任务最多接收 128 条模型诊断，每个 HTTP 请求只向前端发送前 8 条 SSE 样本。完整模型响应另有独立记录（过长时也会截断）。队列满时优先保留正常预览生命周期，诊断可丢弃，不阻塞生成。
- 本次报文采集覆盖 TL Provider；其他模型 Provider 不会伪造模型报文。前端原有“协议日志”继续记录浏览器与后端之间的请求。
