# TL standalone provider（Python）

TL provider 在 QwenPaw Python 进程内直接调用公司网关的
`POST /chatbbc/init_session` 和 `POST /chatbbc/chat`，也可连接遵循相同协议的本地
TL proxy。无需为公司直连启动 TypeScript 服务。

## 配置

### TL 协议调试日志

`qwenpaw app --log-level debug` 会在 QwenPaw 终端及工作目录的 `qwenpaw.log`
记录 `TL_WIRE` 日志：init/chat 请求正文、HTTP 状态、非流式响应正文和完整 SSE
事件（包括解析失败的完整事件）。使用 attempt_id、request_id、session_id 关联请求。
不记录认证头；已配置凭证和常见敏感字段会脱敏。单条 payload 超过 32768 字符会
截断并标记 truncated，original_chars 为脱敏后截断前的 JSON 字符数。
这些是 QwenPaw 与 TL 服务之间的交互，不包含代理内部到上游模型的独立请求。
日志可能包含提示词、模型输出和工具参数，不应直接公开。INFO 级别不输出协议正文。
HTTP 非成功响应只记录状态；未完成的 SSE 事件不作为完整事件记录。

启动时会读取 `$QWENPAW_WORKING_DIR/tl-provider.json`（默认工作目录为
`~/.qwenpaw`）。文件不存在时，从包内 `providers/data/tl-provider.json`
生成默认文件：TL 地址 `http://127.0.0.1:8089`、模型标签 `deepseek-v4-flash`、
上下文上限 1,048,576 tokens。代理需要另行启动。

可用 `QWENPAW_PROVIDER_CONFIG=/absolute/path/tl-provider.json` 指定已有文件；
指定的文件不存在或配置无效时启动报错，不静默切换模型。文件修改后重启生效。
设 `enabled: false` 可禁用启动默认值。

优先级：agent/请求单独指定的模型 > 已保存的全局模型 > 文件的 `active_model`。
已保存的同名 provider 配置优先于文件默认值。文件默认值不会自动写成 provider
快照，因此尚未在 UI 保存配置的 provider 可以继续通过文件修改。
API key 推荐使用 `api_key_env` 引用环境变量，避免把凭证放入模板；缺少该变量时报错。

文件也支持其他模型后端，例如：

```json
{
  "version": 1,
  "enabled": true,
  "active_model": {"provider_id": "my-openai", "model": "my-model"},
  "providers": [{
    "id": "my-openai",
    "name": "My OpenAI-compatible service",
    "chat_model": "OpenAIChatModel",
    "base_url": "http://127.0.0.1:8000/v1",
    "api_key_env": "MY_MODEL_API_KEY",
    "models": [{"id": "my-model", "name": "My model", "max_input_length": 65536}],
    "generate_kwargs": {"temperature": 0.2, "max_tokens": 2048}
  }]
}
```

`generate_kwargs` 必须符合所选后端支持的参数。TL 不支持此类生成参数覆盖：
上游模型路由、采样、思考设置仍在代理的 `.env` 中配置。

在「设置 → 模型」新建自定义供应商，选择 **TL (system prompt)**。填写服务根地址，
可保留公司路径前缀，例如 `https://gateway.example/company`。客户端追加
`/chatbbc/init_session` 和 `/chatbbc/chat`，不追加 `/v1`。

一个供应商实例对应一个服务端路由，仅配置一个模型标签。标签用于本地选择与显示，
不发送给网关，也不会切换服务端实际模型。不同路由请创建不同供应商。

也可通过现有模型 API 创建（地址和元数据均为合成示例）：

```json
{
  "id": "tl-internal",
  "name": "TL internal",
  "chat_model": "TLChatModel",
  "default_base_url": "https://gateway.example/company",
  "tl_config": {
    "app_id": "internal-demo",
    "tr_code": "agent-chat",
    "tr_version": "1.0",
    "system_prompt_variable_name": "system_prompt",
    "tool_calling_mode": "system_prompt",
    "json_correction_max_attempts": 1
  },
  "models": [{"id": "internal-default", "name": "Internal route"}]
}
```

发送到 `POST /api/models/custom-providers`。认证沿用供应商的 `api_key`（目标网关的
Bearer 凭证）或 `custom_headers`，通过现有配置接口保存；不要在 `tl_config`
中放凭证。公司直连客户端不需要公网模型 key。TLS 验证默认开启。

元数据可以为空，是否接受由实际网关决定；系统变量名必须非空。每次决策和每次
允许的语法纠错都创建新会话。已有其他协议实例不能直接改成 TL，请新建实例。

「测试供应商」只验证会话初始化，返回 `verification=provider_only`；模型连接测试
执行完整 init→chat，成功返回 `live`，但不证明网关背后的模型品牌或版本。
测试使用当前表单中尚未保存的 TL 配置。

## 输入、工具与限制

系统消息和当轮工具 schema 进入 init 的系统变量。完整有序历史、工具结果及 Hint
进入 chat 的 `txt` JSON 字符串。没有原生 `tools`、`tool_calls`、`tool_choice` 或模型
路由字段，`files` 固定为 `[]`。

工具模式只接受 `docs/skills/tl-llm-standalone` 定义的 JSON envelope；整批工具名和
参数校验通过、且传输成功结束后才提交 AgentScope `ToolCallBlock`。工具仍由现有
Toolkit、Guard 和审批链执行。普通文字模式不会把 JSON/XML 示例猜成工具调用。

新版 Console 在工具模式中显示可撤销草稿；正式校验前，草稿不进入 AgentScope
消息、聊天历史或工具执行。完成、失败、取消、纠错或切换会话时清除。
不支持预览的客户端和其他渠道沿用校验完成后显示的行为。

完整传输后的非空 JSON 语法错误、工具模式下单个 call 的字段包装错误
（`call_shape`），以及具有完整 DSML calls/invoke
外层结构的响应，可共用一次格式纠正机会（`json_correction_max_attempts=0` 可关闭）。
普通 XML、正文引用、代码围栏和截断的 DSML 不进入 DSML 纠正。
纠正复用原 init 系统协议，失败正文作为不可信数据放入 chat；禁止增补调用、猜测参数
或修正未知工具名。无法无歧义转换时返回空对象，由校验器拒绝。
纠正后的整批调用仍须通过原有 JSON、工具名称、参数 schema 和 tool_choice 校验；
不直接解析或执行 DSML。提示词提高格式遵循率，但不能保证模型转换前后语义一致。
字段包装纠正仅允许无歧义地调整包装，保留工具名、调用顺序和参数值；不补参数、
不猜测冲突值、不改参数类型。与 JSON/DSML 共用一次预算，纠正失败不再重试。
网络错误、SSE 错误、未知工具、参数
不匹配均直接失败。TL 的 SDK/外层重试和跨模型 fallback 均不重放这些错误；正常
限流和并发槽保留。取消向上传播。

v1 仅支持文本，不读取附件、图片、音频或视频。`generate_kwargs` 必须为空，不支持
采样、思考、原生 JSON 模式或每轮选模型。结构化输出使用独立的
`generate_structured_output(messages, structured_model)`。

默认每次 attempt 总超时 150 秒（覆盖 init 和 chat），字节空闲超时关闭。
默认每个请求 body 上限 1 MiB、模型正文上限 4 MiB、HTTP 响应 body 上限 64 MiB、
单个 SSE 事件上限 1 MiB，均按 UTF-8 字节计量。通过 `tl_config` 可下调。

未知部署的上下文默认采用 32768 token 的本地预算，可在模型配置中明确设置。
计数包含编译后的系统、工具目录、历史 JSON 及输出余量，属于保守估算；网关没有
usage 字段，因此不伪造准确 token 消耗。

## 代码位置与验证

### 依赖升级说明

本次实现将 `jsonschema` 的最低版本从 `4.0.0` 提升至 `4.18.0`，并显式依赖
`referencing>=0.30.0`，用于 JSON Schema 校验和仅在本地解析 schema 引用。
升级时需重新安装项目依赖；若环境单独锁定了较旧版本，请同步调整版本约束。
当前测试环境使用 `jsonschema 4.26.0`、`referencing 0.37.0`。

### 实现位置

实现位于 `src/qwenpaw/providers/`：`tl_provider.py` 负责配置与检查；
`tl_chat_model.py` 负责 AgentScope 适配；`tl_formatter.py` 保留消息块；
`tl_prompt_codec.py` 编译和校验协议；`tl_transport.py` 处理 HTTP/SSE；
`tl_config.py` 和 `tl_errors.py` 提供共享类型；`tl_preview.py` 提供请求范围的
临时预览，运行时与 Console 分别负责转发和展示；`tl_utils.py` 统一识别
TL formatter 和模型包装器暴露的 formatter。

预览队列使用 `deque + asyncio.Event`，在同一事件循环内合并更新，并为每个
已接纳的草稿预留清除事件的位置。运行时与任务跟踪器共用预览排空方法：
先停止挂起的读取任务并保留已读取事件，再排空队列，确保清除早于正式输出或结束。

使用仓库 Python 环境运行离线检查：

```bash
PYTHONPATH=src:packages/qwenpawmail-mcp/src python -m pytest \
  tests/unit/providers/test_tl_transport.py \
  tests/unit/providers/test_tl_prompt_codec.py \
  tests/unit/providers/test_tl_chat_model.py \
  tests/unit/providers/test_tl_provider.py \
  tests/unit/providers/test_tl_utils.py \
  tests/unit/providers/test_tl_preview.py \
  tests/unit/providers/test_tl_preview_model.py \
  tests/unit/runtime/test_executor_preview.py \
  tests/unit/app/test_task_tracker_preview.py \
  tests/unit/agents/test_routing_text.py \
  tests/integration/test_tl_tool_roundtrip.py -q
```

测试使用 MockTransport 和无副作用加法工具，不连接真实网关。离线测试、
本地 proxy 联调、真实模型和公司内网联调是不同验收层级；本次不把离线结果视为
真实接口验证。
