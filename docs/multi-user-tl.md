# 多用户服务使用真实 TL Provider

在仓库根目录执行：

```sh
cd test-tools/native-console
npm run service:tl
```

在前台启动 TL Proxy、真实 TL 的 `/v1` API/Worker（8092）、Native Console（5179），
自动选择服务模式并注入同一份本地服务令牌。打开地址后点「连接 / 检查」，发送消息
才会调用真实模型。Ctrl+C 仅停止本次启动的子进程；占用端口不会被清理。

前提：仓库 `.venv` 已安装服务依赖，两个 test-tools 项目的依赖已安装，
TL Proxy 已 build，且 `test-tools/tl-llm-proxy/.env` 已配置实际上游。
已有 TL 服务或代理时用 `QWENPAW_TL_EXTERNAL=1 npm run service:tl`，
确保平台配置的 base_url 指向它。

## 平台配置

- 本地文件：`deploy/server/assistant.tl.local.json`，包括 `model_protocol: "tl"`、
  模型标签、`base_url`、`tl_config`。所有开发用户共享此配置，不读取个人 Agent 设置。
- 生产样例：`deploy/server/assistant.tl.json`，包含会话沙箱工具。将 base_url 改为
  Worker 可访问的 TL 地址，通过 `QWENPAW_SERVER_DEFINITION_PATH` 指定部署后的文件。
  API/Worker 使用同一 TDSQL 存储，按原 `qwenpaw serve` 入口部署。
- 修改配置时更新 `version`（如 local-tl-v2），不可覆盖已经入库的同版本配置。
  所有 API 副本发布同一新版本，新任务使用新配置，已提交任务保持原快照。
- TL 客户端鉴权：`QWENPAW_SERVER_MODEL_API_KEY`。上游模型密钥与实际选模仍在
  TL Proxy 的 `.env`，不能靠修改模型标签切换真实上游。密钥不写入助手定义或前端。
- 本地使用内存数据库与有界文件存储（总量 64 MiB），退出后数据清空；支持受控记忆工具，禁止 Shell/浏览器等沙箱
  工具。完整工具链使用正式 Controller/沙箱部署。本地 runner 不连接独立 Controller，
  因为独立进程不能共享它的内存任务状态。
- 使用 `QWENPAW_SERVICE_PORT`、`QWENPAW_CONSOLE_PORT` 调整端口，
  `QWENPAW_SERVER_DEFINITION_PATH` 指定其他 TL 平台配置。

服务模式管理页显示平台模型及配置版本；原来的 TL Provider 编辑表单属于个人模式。
平台配置当前通过文件管理，普通聊天凭据不能修改全平台模型。

## 与模拟演示的区别

`server_stream_demo.py` 的模型和工具是模拟的；`server_tl_local.py` 使用真实的
`TLChatModel` / `TLTransport` 与 init_session → chat 协议。启动不会自动发起付费聊天。
本地数据仍是内存数据，不能据此声称真实 TDSQL 或生产容量验收通过。

## 独立启动

先启动配置好的 TL 服务，再从仓库根目录执行（令牌须至少 32 字符）：

```sh
# macOS / Linux
export QWENPAW_SERVER_SERVICE_TOKEN=local-tl-development-token-32-characters
.venv/bin/python scripts/server_tl_local.py --definition deploy/server/assistant.tl.local.json
```

```powershell
# Windows (PowerShell)
$env:QWENPAW_SERVER_SERVICE_TOKEN = "local-tl-development-token-32-characters"
.venv\Scripts\python.exe scripts\server_tl_local.py --definition deploy/server/assistant.tl.local.json
```

另一个终端：

```sh
# macOS / Linux
cd test-tools/native-console
QWENPAW_CHAT_MODE=service QWENPAW_SERVICE_TARGET=http://127.0.0.1:8092 \
QWENPAW_SERVICE_TOKEN=local-tl-development-token-32-characters npm run dev
```

```powershell
# Windows (PowerShell)
cd test-tools/native-console
$env:QWENPAW_CHAT_MODE = "service"
$env:QWENPAW_SERVICE_TARGET = "http://127.0.0.1:8092"
$env:QWENPAW_SERVICE_TOKEN = "local-tl-development-token-32-characters"
npm run dev
```

## 本次验证

2026-09-18：本地 runner 测试 4 项、真实 HTTP TL Proxy 集成测试 2 项通过；
Native Console 构建、43 项单元测试、12 项浏览器测试通过，并额外验证了服务模式的
平台版本展示及个人 TL 表单隐藏。启动器以已有 TL 服务模式实测，前端代理成功读取
`model_protocol=tl` 的平台配置。未发送真实付费聊天；真实上游可用性及此前的 502
需实际聊天单独验证。


## TL 附件解析

个人聊天与 `/v1` 均支持后端将 TXT/MD/CSV/JSON/LOG、PDF、DOCX、XLSX 提取为文本，
以参考资料加入用户消息；TL 线路仍只传文本，不会传原始附件、路径或外部 URL。
单文件上限 10 MiB，解析文本上限 100,000 字符，超长内容返回截断标记；
多用户任务的附件文本总量也限制为 100,000 字符。图片、音视频、扫描 PDF 的 OCR
目前不支持，会明确报错。含宏、嵌入对象或外部引用（包括部分超链接）的文档也会拒绝。PDF 依赖 pypdf，已加入项目依赖。

个人模式使用 `/api/console/attachments/parse`；服务模式先上传 `/v1/files`，
任务提交时校验文件归属并解析，结果绑定任务快照，避免模型看到其他用户附件。
本地服务重启后文件和会话都会清空；生产继续使用对象存储。

若服务模式出现 404，检查代理目标是否误指向 `qwenpaw-office`：它可能占用 8090。
现在本地多用户服务默认 8092。修改 Vite 环境变量后需重启前端开发服务；
不会自动终止占用其他端口的应用。

2026-09-18 附件适配验证：Native Console 43 项单测、14 项浏览器测试及构建通过；
服务端与附件回归通过，真实 HTTP TL Proxy（模拟上游）2 项通过。
运行中的 5179 前端已验证个人附件解析 200、v1 健康检查 200、上传 201、下载 200、删除 204。
真实 TDSQL 和真实模型上游不在本次附件验收范围。
