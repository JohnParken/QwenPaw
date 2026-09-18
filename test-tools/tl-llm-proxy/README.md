# tl-llm-proxy-test

独立的、仅用于测试的 TL 两段式代理；它不是 QwenPaw 生产依赖，也不依赖 QwenPaw 源码、UI 或 DOM。代理固定连接一个 OpenAI-compatible 上游实例，并暴露 `POST /chatbbc/init_session` 与 `POST /chatbbc/chat`。

## 本地运行

需要 Node.js 24（或 package.json 声明的 Node 22.22.1）。复制 `.env.example` 为 `.env`，填入上游配置：

```sh
npm ci
npm run build
npm start -- --env-file .env
npm pack
```

`npm test` 使用注入的 fake Provider，不访问真实模型；`npm run test:live` 才会访问配置的真实上游。默认监听 `127.0.0.1:8089`，端口 0 适合测试。QwenPaw standalone 的 base URL 直接填 `http://127.0.0.1:8089`，不要加 `/v1`。

可同时启动两实例，例如 DeepSeek：`UPSTREAM_PROVIDER=deepseek UPSTREAM_MODEL=deepseek-chat TL_PROXY_PORT=8090`；Qwen：`UPSTREAM_PROVIDER=qwen UPSTREAM_MODEL=qwen3.8-flash TL_PROXY_PORT=8091`。两者各自固定 endpoint、模型和 key，不能由 TL 请求改路由。

## 配置

必填：`UPSTREAM_PROVIDER`（`deepseek`、`qwen`、`openai-compatible`）、`UPSTREAM_MODEL`、`UPSTREAM_BASE_URL`（HTTP(S)，无凭证/query/fragment）、`UPSTREAM_API_KEY`。常用可选项：`TL_PROXY_HOST`、`TL_PROXY_PORT`、`UPSTREAM_THINKING`（provider-default/enabled/disabled）、`UPSTREAM_MAX_TOKENS`、`SYSTEM_PROMPT_VARIABLE_NAME`（默认 system_prompt）、`LEGACY_ROLE_TEXT`（默认 true）、`LOG_LEVEL`（默认 debug）、`AUTH_MODE`（local 或 bearer）、`TEST_ACCESS_TOKEN`（bearer 必填）、`CORS_ALLOWED_ORIGINS`（逗号分隔）。

边界与生命周期配置：`UPSTREAM_TIMEOUT_MS`、`STREAM_IDLE_TIMEOUT_MS`、`REQUEST_BODY_TIMEOUT_MS`、`SHUTDOWN_TIMEOUT_MS`、`MAX_REQUEST_BYTES`、`MAX_RESPONSE_BYTES`、`MAX_UPSTREAM_WIRE_BYTES`、`MAX_DOWNSTREAM_WIRE_BYTES`、`MAX_SSE_EVENT_BYTES`、`MAX_DOWNSTREAM_EVENT_BYTES`、`SESSION_TTL_MS`、`MAX_SESSIONS`、`MAX_INFLIGHT_REQUESTS`、`MAX_LOG_QUEUE_BYTES`。

## 协议边界

init 只保存 prompt variables；成功返回 TL success envelope 和 `data.session_id`。chat 的 `txt` 是完整 user 正文，`stream` 缺省或 true 返回 `event: chunk`，最后一个 `event: done`；false 返回原样 `data.txt`。纯文本、Markdown、XML、JSON（包括不完整 JSON）均透明传递。`files: []` 和全空占位兼容，真实附件返回 400。原生 `tools`、`tool_calls`、`response_format` 等控制字段返回 400；同名文字出现在正文中仍是普通文字。上游原生工具响应返回 502，不转换成正文。代理不累计历史、不下载附件、不执行工具。

`local` 只适用于 loopback；`bearer` 要求 `Authorization: Bearer $TEST_ACCESS_TOKEN`。默认 debug 记录四向 header/body/event 与增量正文，凭证会掩码；`LOG_LEVEL=info` 关闭正文日志。日志用于本地合成数据调试，勿将真实 key/token 或 debug 输出暴露给共享环境。

不支持公司 SSO、真实附件、原生工具调用转换、自动重试、动态模型路由或 QwenPaw standalone 客户端职责。未知/过期 session、非法 body、超限和容量耗尽会返回明确 HTTP 错误；stream 中途失败不会发送 done。

## 与 QwenPaw standalone 的匹配范围

当前 standalone 可直接把 base URL 指向本代理：两者匹配 init/chat 路径、请求元数据、`prompt_variables`、`session_id/txt/files/stream`、非流式成功信封，以及 `chunk`/`done` SSE。代理只把系统提示词与本轮 `txt` 组成上游消息；standalone 负责历史压缩、工具正文协议、校验、纠错和本地工具执行。

已知差异：本代理采用测试环境的 HTTP 错误映射，尚未用公司错误 fixture 核对；standalone 还能读取 `end` 和 `[DONE]`，本代理下游固定发送 `done`；代理上游默认要求 `finish_reason: stop` 后再收到 `[DONE]`；代理上游 deadline 默认 120 秒，standalone 整个 init/chat attempt 默认 150 秒；代理上游单 SSE 事件默认 256 KiB，standalone 下游事件上限默认 1 MiB。公司 SSO、附件、多副本 session 和真实 provider 的模型可用性/思考参数不在离线匹配范围内。
