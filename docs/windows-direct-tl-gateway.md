# Windows 本地直连真实 TL 网关

本文说明如何在 Windows 上从源码运行 QwenPaw，并让 TL 供应商**直接**调用网关的
`POST /chatbbc/init_session` 与 `POST /chatbbc/chat`：

- 不再启动本地 Node TL proxy（`127.0.0.1:8089`）。
- 不让 Windows 系统代理或 `HTTP_PROXY` / `HTTPS_PROXY` 参与请求。
- 适用于内网 http（非 https）网关地址。

协议本身不需要改动：`TLTransport` 本来就按上述两个固定路径直接发请求，本地 proxy 只是
一个可选的对端实现。需要改的只有「地址、业务元数据、鉴权、代理行为」四项配置。

## 1. 前置条件

| 组件 | 版本 | 校验命令 |
| --- | --- | --- |
| Python | 3.11 – 3.13，3.13 不含 | `python --version` |
| Node.js | 22 或 24（构建 Console 用） | `node --version` |
| Git | 任意较新版本 | `git --version` |

只有浏览器里的 Console 需要 Node；如果已经有可用的 `console/dist`，运行阶段不需要 Node。

## 2. 从源码构建并安装

在 PowerShell 中执行（`pwsh` 或 Windows PowerShell 均可）：

```powershell
git clone <你的仓库地址> QwenPaw
cd QwenPaw

# 1) 构建 Console 前端
cd console
npm ci
npm run build
cd ..

# 2) 安装 Python 包（可编辑安装，改代码立即生效）
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e .
```

关于 Console 静态文件：`qwenpaw app` 会按顺序查找
`src/qwenpaw/console/index.html` → `<repo>/console/dist/index.html` → `cwd/console/dist`。
**从仓库根目录启动时第二步就会命中 `console/dist`，因此 Windows 上不必手工拷贝**。
如果你在别的目录启动，或想固定路径，可以显式指定：

```powershell
$env:QWENPAW_CONSOLE_STATIC_DIR = "C:\path\to\QwenPaw\console\dist"
```

## 3. 先探测真实网关

不要跳过这一步：地址或报文格式不对时，在 Console 里只会表现为一次失败的模型调用。
仓库提供了探测 + 配置 + 启动一条龙的脚本：

```powershell
pwsh -File scripts\windows\run_direct_gateway.ps1 `
  -BaseUrl http://10.1.2.3:8080 `
  -AppId my-app -TrCode agent-chat -TrVersion 1.0
```

脚本按顺序做四件事：

1. 校验 `BaseUrl` 是 http(s) 服务根地址，且没有误带 `/v1` 或 `/chat/completions`；
2. 清空当前进程的 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`，并把网关主机写入
   `NO_PROXY`；
3. 真实调用一次 `init_session`（校验数值 `code=0` 与 `data.session_id`），再调用一次
   非流式 `chat`（校验 `data.txt` 为字符串），并把响应摘要打印出来；
4. 探测通过后写入 `%USERPROFILE%\.qwenpaw\tl-provider.json` 并执行 `qwenpaw app`。

只探测、不写配置、不启动：加 `-ProbeOnly`。已有配置不想被覆盖：不要加 `-Force`
（脚本默认保留现有文件并提示）。

> 提示：`-TrVersion` 等参数区分大小写，PowerShell 参数名不区分；`-BaseUrl` 结尾的 `/`
> 会被自动去掉。若网关有路径前缀（例如 `http://gw.corp.example/company`），按原样传入，
> 客户端只会在其后追加 `/chatbbc/...`。

## 4. 关键配置

### 4.1 地址

`tl-provider.json` 中 `providers[].base_url` 填网关服务根地址：

```json
{
  "id": "tl-gateway",
  "name": "TL Gateway",
  "base_url": "http://10.1.2.3:8080",
  "chat_model": "TLChatModel",
  "is_custom": true,
  "models": [
    {
      "id": "internal-route",
      "name": "internal-route",
      "max_input_length": 32768,
      "max_input_length_configured": true
    }
  ],
  "tl_config": {
    "app_id": "my-app",
    "tr_code": "agent-chat",
    "tr_version": "1.0",
    "system_prompt_variable_name": "system_prompt",
    "tool_calling_mode": "system_prompt",
    "json_correction_max_attempts": 1,
    "trust_env": false,
    "timeout_seconds": 150.0,
    "stream_idle_timeout_seconds": 0.0,
    "max_request_bytes": 1048576,
    "max_response_bytes": 4194304,
    "max_wire_response_bytes": 67108864,
    "max_sse_event_bytes": 1048576
  }
}
```

要点：

- **不要追加 `/v1`**，客户端只追加 `/chatbbc/init_session` 和 `/chatbbc/chat`；
- `models[].id` 只是本地标签，不会发给网关，也不切换服务端模型；
  一个供应商只配一个模型标签；
- `max_input_length` 建议按网关真实上下文填写；未显式配置时本地按 32768 保守估算。
  每个决策都新建会话，因此历史压缩完全由客户端负责。

### 4.2 不走代理

`tl_config.trust_env` 为 `false`（默认值）时，TL 的 httpx 客户端使用直连套接字，
不读取 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`，也不读取平台代理配置。

这一点对**内网 http 网关**尤其重要：明文 HTTP 请求一旦被 `HTTP_PROXY` 捕获，就会发往
公司正向代理，通常表现为 502/407 或代理自身的错误页，而不是网关的业务错误。

需要临时用环境变量兜底（不启动脚本时）可以在启动前设置：

```powershell
$env:NO_PROXY = "10.1.2.3,gateway.corp.example"
```

只有当你确实必须经正向代理访问网关时，才把 `trust_env` 改成 `true`。

### 4.3 鉴权

免鉴权内网：`app_id` / `tr_code` / `tr_version` 会被原样发送，凭据留空即可
（这三个字段是业务元数据，不是身份凭证）。

若网关需要凭据，两种方式都已在现有实现中支持，**不需要改协议代码**：

- **Bearer**：在 Console「设置 → 模型」里给该供应商填 API Key，客户端会加
  `Authorization: Bearer <key>`（只在未自定义同名头时）；
- **自定义请求头**（如 `X-App-Id`、`Cookie`、SSO 票据）：在同一页的
  `custom_headers` 里逐条填写，客户端对每个请求都带上。

`tl_config` 只放协议元数据与本地预算，不要把凭据写进 `tl_config`。

## 5. 启动与验证

```powershell
# 正常启动
qwenpaw app

# 需要看真实请求/响应报文时
qwenpaw app --log-level debug
```

浏览器打开 <http://127.0.0.1:8088/>，「设置 → 模型」选中 `TL Gateway` /
`internal-route`，可先点「连接测试」：它只验证 init_session（返回
`verification=provider_only`）；模型连接测试会跑完整的 init → chat（返回 `live`）。

debug 级别下，`qwenpaw.log` 会记录 `TL_WIRE` 事件：请求正文、HTTP 状态、非流式响应
正文和完整 SSE 事件。先确认日志里的 URL 是 `http://<内网地址>/chatbbc/...`，
再确认响应 `code` 为数值 0。认证头不会记录，已配置的凭证会脱敏；日志可能包含提示词与
模型输出，不要公开。

## 6. 排错对照表

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `init_session is unreachable` / ConnectTimeout | 地址不通或走错网段 | 确认能 `ping`/`Test-NetConnection` 到网关；确认已连内网或 VPN |
| 返回 407 / 代理错误页 | 请求被正向代理接管 | 确认 `trust_env: false`；清掉 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`；必要时设 `NO_PROXY` |
| HTTP 404 | 路径不对 | `base_url` 只填服务根地址，不要带 `/chatbbc/...`、`/v1` |
| HTTP 200 但 `data.session_id` 缺失 | 打到的不是 chatbbc 网关 | 用脚本的 `-ProbeOnly` 打印真实响应，确认对端接口 |
| `business code` 非 0 | 网关业务拒绝 | 核对 `appId` / `trCode` / `trVersion` 是否与网关模板一致 |
| `chat` 响应没有 `data.txt` | 对端返回格式不同 | 把真实响应贴出来，需要按它调整 `tl_transport.py` 的解析分支 |
| 流式一直等到超时 | SSE 事件名或终止事件不同 | debug 日志里看实际 SSE 事件名；客户端只接受 `chunk` / `done` / `end` / `[DONE]` |
| 启动报 `Invalid default provider configuration` | `tl-provider.json` 结构或字段不合法 | 只允许文中的字段；`version` 必须为 1；`active_model` 必须指向已配置的 provider/model |
| 首次启动报文件系统错误 | 工作目录所在分区不支持硬链接 | 把 `QWENPAW_WORKING_DIR` 指到 NTFS 路径后重试 |

工作目录与配置文件位置：

- 默认工作目录：`%USERPROFILE%\.qwenpaw`（即 `~/.qwenpaw`），配置文件
  `%USERPROFILE%\.qwenpaw\tl-provider.json`；
- 自定义工作目录：设置 `QWENPAW_WORKING_DIR`，配置文件随工作目录移动；
- 配置独立路径：设置 `QWENPAW_PROVIDER_CONFIG` 指向绝对路径的 json
  （此时文件不存在会直接启动报错，不做静默回退）；
- 配置改动需要重启 `qwenpaw app` 生效；
- 已在 Console 保存过的同名供应商快照优先于文件默认值；要回到文件配置，先在 Console
  删除该供应商，或换一个 `providers[].id`。

## 7. 相关文档

- 协议、工具模式与资源预算：[tl-provider.md](tl-provider.md)
- 客户端固定报文与成功信封样例：
  [tl-llm-standalone/tl-client-contract](skills/tl-llm-standalone/references/tl-client-contract.md)
- 多用户 `/v1` 服务形态（Worker 容器内如何使用 TL）：[multi-user-tl.md](multi-user-tl.md)
- 本地 proxy 联调（本文明确不使用的路径）：[tl-port-multisync.md](tl-port-multisync.md)
