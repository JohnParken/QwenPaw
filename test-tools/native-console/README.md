# 原生 Web Console 核心流程验证包

独立的 TypeScript + HTML + CSS 应用，用于验证在**直接依赖仅允许指定工程工具**的约束下重写 QwenPaw Web Console 的可行性。不引入 React、UI 库、聊天 SDK、编辑器、Markdown 库或现有 Console/插件源码。应用代码的第三方运行时依赖为零。

这是一套可操作的核心流程验证界面，尚不是现有 Console 的全功能替代品。真实模式调用现有 QwenPaw 后端；自动化模式由 Playwright 在浏览器请求层提供隔离的模拟接口，不改动真实配置。

👉 **详细测试步骤与用例指引请参阅专门的 [测试指南 (TESTING.md)](TESTING.md)**。

## 多用户使用真实 TL

在本目录执行 `npm run service:tl`，启动真实 TL 的多用户 `/v1` 服务和前端。
配置方式、已有代理复用和生产部署见 [多用户 TL 接入](../../docs/multi-user-tl.md)。

个人模式联调使用 `npm run live:start`（`live:services` 只启动底层服务、`live:console`
只启动前端、`live:stop` 全部停止）。它现在会同时启动多用户 `/v1` 服务（8092）：
前端默认选中「服务聊天 /v1」，缺少该服务时页面只会收到开发代理的 `HTTP 500: ""`。
端口可用 `QWENPAW_TL_PROXY_PORT`、`QWENPAW_PERSONAL_PORT`、`QWENPAW_SERVICE_PORT`、
`QWENPAW_CONSOLE_PORT` 覆盖；`/v1` 服务日志在 `logs/service-api.log`。

## 多用户服务流式聊天（模拟验收）

在**仓库根目录**开启终端一：

```sh
.venv/bin/python scripts/server_stream_demo.py --port 8090
```

另开终端二，从**仓库根目录**执行：

```sh
cd test-tools/native-console
QWENPAW_SERVICE_TARGET=http://127.0.0.1:8090 \
QWENPAW_SERVICE_TOKEN=qwenpaw-stream-demo-local-token-32 npm run dev
```

打开终端输出的地址，连接面板选择「服务聊天 /v1」并连接，然后新建会话、发送消息、批准工具。可观察逐字正文、审批卡片和逐行工具日志；取消后等待服务确认终态；「加载历史」恢复已保存工具时间线或重新订阅尚未结束的任务。

演示使用真实 API/Worker、内存存储及模拟模型和工具，无需 TL 或数据库，不执行真实命令，关闭后数据清空。连接真实服务时替换目标和令牌；平台配置模型及审批权限，服务模式不使用本地 Agent/审批策略下拉框。服务令牌仅在开发代理持有，不注入浏览器。

正文和工具按事件顺序展示；工具卡片支持参数、状态、结果、Shell 增量日志和折叠。断线按持久序号重连，不重发消息或工具操作。TL 临时预览在确认、失败或取消后撤回，不能作为已执行结果。

完整事件契约和验收范围见 [流式工具文档](../../docs/streaming-tool-console.md)。下文的本地 Console/TL 管理操作适用于「本地 Console」模式。

## 启动

```sh
cd test-tools/native-console
npm ci
npm run dev
```

打开 <http://127.0.0.1:5179>。默认选择多用户 `/v1`，开发代理目标为 `http://127.0.0.1:8092`；需要先启动服务并配置匹配的令牌，或使用 `npm run service:tl` 一键启动。

个人模式保留兼容；连接个人后端时显式选择 `local`：

```sh
QWENPAW_CHAT_MODE=local QWENPAW_API_TARGET=http://127.0.0.1:8088 npm run dev
```

也可以将 `.env.example` 复制为 `.env.local` 后修改并重启。代理目标只在开发服务器使用，不会注入浏览器代码。页面访问令牌、账号密码与 Provider API Key 不写入本地存储；刷新页面后需要重新输入。登录后可以切换后端返回的 Agent。

真实模式下，点击保存、上传、创建任务、发送消息会操作所连接后端。请选择测试 Agent 和测试工作区。默认新建定时任务为暂停，上传同名文件采取 `rename`。

## 本地联调：一键启动与分步启动

上面的 `npm run dev` 只启动前端。前端本身不需要后端也能打开，但**连接面板、聊天、管理页、文件页都需要后端**：`/api` 代理指向 8088（个人后端），`/api/service` 代理指向 8092（多用户 `/v1` 服务）。后端没起来时，连接会失败，收件箱/技能等标签会收到开发代理返回的 `HTTP 500: ""`——这看起来像前端崩溃，实际是代理目标无人监听。

`scripts/manage-live.mjs` 提供了跨平台（macOS/Linux + Windows）的启动器，零外部依赖。

### 一条命令启动全部

```sh
cd test-tools/native-console
npm ci                 # 首次
npm run live:start     # 依次启动 tl-proxy → QwenPaw 后端 → /v1 服务 → 前端
```

看到「全链路联调测试环境已全部就绪」后打开 <http://127.0.0.1:5179>。

| 服务                | 地址                    | 用途                                 |
| ------------------- | ----------------------- | ------------------------------------ |
| Native Console 前端 | <http://127.0.0.1:5179> | 验证界面                             |
| QwenPaw Python 后端 | <http://127.0.0.1:8088> | 本地模式：管理页、收件箱、技能、文件 |
| 多用户 `/v1` 服务   | <http://127.0.0.1:8092> | 服务聊天模式                         |
| TL 流量转发代理     | <http://127.0.0.1:8089> | TL Provider 上游                     |

启动器会复用**已在监听**的端口，所以重复执行是安全的：已运行的服务不会被重启，只补齐缺失的部分。

### 分步启动

| 命令                    | 作用                                                                                                             |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `npm run live:services` | 只启动底层三项：tl-proxy + QwenPaw 后端 + `/v1` 服务                                                             |
| `npm run live:console`  | 只启动前端测试台                                                                                                 |
| `npm run live:start`    | 四阶段全量启动（上面两个的合集）                                                                                 |
| `npm run live:stop`     | 优雅停止全部服务并释放端口                                                                                       |
| `npm run service:tl`    | 前台运行「TL Proxy + 真实 TL 的多用户 `/v1` API/Worker + 前端」；`Ctrl+C` 只停止本次启动的子进程，不影响其他实例 |

典型用法是先 `npm run live:services` 起后端，再用 `npm run dev` 起前端，这样前端日志直接打在终端里。

### 端口与日志

端口可用环境变量覆盖，便于与已占用的默认端口并存：

```sh
QWENPAW_TL_PROXY_PORT=9089 QWENPAW_PERSONAL_PORT=9088 \
QWENPAW_SERVICE_PORT=9092 QWENPAW_CONSOLE_PORT=5279 \
npm run live:start
```

日志与 PID 都落在本目录：`logs/`（`tl-proxy.log`、`qwenpaw.log`、`service-api.log`、`native-console.log`）与 `.run/*.pid`。后端启动失败时先看 `logs/qwenpaw.log`：

```sh
tail -f logs/qwenpaw.log
```

### 停止

```sh
npm run live:stop
```

停止后端口 8088/8089/8092/5179 会一并释放。也可以直接运行脚本，它们与上面的 npm 命令等价：

```sh
./start.sh            # = npm run live:start
./start-services.sh   # = npm run live:services
./start-console.sh    # = npm run live:console
./stop.sh             # = npm run live:stop
```

Windows 用同名 `.bat`：`start.bat` / `start-services.bat` / `start-console.bat` / `stop.bat`。

### 验证路径

启动后按下面顺序点一遍即可覆盖主要流程（两种模式都可用，用连接面板的「聊天模式」下拉框切换）：

1. **服务聊天 /v1**（默认）：点「连接 / 检查」→ 页头应显示 `服务: <平台模型>` → 「+ 新建」→ 发消息 → 观察逐字流式正文、工具卡片、思考折叠块 → 「加载历史」验证回放 → 生成中「停止生成」验证取消。
2. **本地 Console 管理**：切到 local 后连接 → 管理页读取模型配置 / 渠道列表 / 定时任务 → 工作区文件浏览、读取、改后保存（ETag 保护）、下载 → 收件箱与任务、智能体技能两个标签。
3. **日志抽屉**：顶部「协议日志」与「模型诊断」支持筛选、展开、导出；Debug 勾选后诊断日志才由后端产生（后端还需 `QWENPAW_MODEL_DEBUG=1`）。

### 连接不上时的排查

| 现象                                             | 原因与处理                                                                                                                                                                                     |
| ------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 连接报 `HTTP 500: ""`                            | 代理目标没起来。确认 8088/8092 在监听：`npm run live:services`                                                                                                                                 |
| 收件箱/技能标签 500                              | 同上，这两个标签走 `/api` → 8088                                                                                                                                                               |
| `服务聊天 /v1` 连接失败                          | 8092 未就绪；看 `logs/service-api.log`。该服务也需要 `QWENPAW_SERVICE_TOKEN` 与它一致                                                                                                          |
| 后端 45 秒未就绪                                 | 看 `logs/qwenpaw.log` 里的 traceback                                                                                                                                                           |
| `Failed to initialize cache at ~/.cache/uv`      | 受限环境（容器/沙箱）不允许 uv 写家目录，给启动器换个缓存目录：`UV_CACHE_DIR=$PWD/.uvcache npm run live:start`                                                                                 |
| 后端写 `~/.qwenpaw` 报 `Operation not permitted` | 受限环境不允许写家目录，把后端状态重定位到工作区内：`QWENPAW_WORKING_DIR=$PWD/.qwenpaw QWENPAW_SECRET_DIR=$PWD/.qwenpaw/secret npm run live:start`（这样也不会污染你真实的 `~/.qwenpaw` 配置） |

`QWENPAW_WORKING_DIR` 会把后端配置、Agent 工作区和密钥全部放到指定目录，适合一次性验证或隔离多套环境。

## TL Provider 适配

连接 QwenPaw 后，进入「管理页面 → TL Provider · chatbbc」。页面从后端 `/api/models` 筛选 `TLChatModel`，读取实际 `base_url`、`tl_config` 和模型标签；不使用现有 React Console 中的演示身份默认值。仓库默认配置为 `tlproxy` / `deepseek-v4-flash`，实际展示以后端为准。

连接关系：浏览器 `5179` → QwenPaw 后端 `8088` → TL 服务（默认 `8089`）。`QWENPAW_API_TARGET` 应指向 QwenPaw 后端；TL 服务地址填写在专用表单的 **TL Base URL**，不要将前端代理直接改成 TL 服务。

1. 点击「读取 TL 配置」，选择已配置的 TL Provider。`app_id`、`tr_code`、`tr_version` 可留空；其他限额和超时按后端字段校验。
2. 修改后点击「保存 TL 配置」。工具调用模式固定为 `system_prompt`；不发送采样、thinking 或模型发现参数。API Key 可选，留空时保留后端原值。
3. 「测试会话初始化」使用当前表单，调用 QwenPaw 的 Provider test API，再由后端调用 TL `init_session`。成功只表示初始化成功，不表示聊天已验证。
4. 「测试聊天（已保存配置）」使用后端已保存的配置，调用模型测试 API；真实模式下会实际发送一次测试聊天。失败状态不会显示为成功。
5. 点击「用于当前 Agent」，保存 Agent 级模型选择，然后返回「核心聊天」。发送前重新核对当前生效 Provider，仍请求 `/api/console/chat`，不从浏览器直接调用 `chatbbc`。

TL 线路只支持文本；文档附件会先由 QwenPaw 解析为文本再发送，支持格式和限制见多用户 TL 接入文档。模型 ID 是该 endpoint 的本地标签，不是上游选模参数。TL 流空闲超时为 0 时不额外设置浏览器空闲截止时间，由后端管理请求时限；大于 0 时前端使用该值加 5 秒缓冲。所有配置和测试请求仍进入网页脱敏日志。

本包适配现有 TL Provider，不自动创建或修改后端 `tl-provider.json`；后端没有 TL Provider 时会明确提示。TL 页面自动化测试使用隔离接口，不连接真实 TL 服务。

TL 适配验收：`PLAYWRIGHT_CHANNEL=chrome npm run check` 通过，包含 21 项单元测试和 7 项浏览器测试（原有 4 项 + TL 3 项）；构建、白名单及格式检查通过。已检查桌面 TL 表单和 390px 窄屏，无页面异常或横向溢出。真实 TL 服务与真实模型尚未联调。

## 范围和业务流程

| 功能          | 已实现流程                                                     | 验收标准                                                                                                              |
| ------------- | -------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| 登录和 Agent  | 认证状态 → 密码登录或已有令牌 → verify → Agent 列表与切换      | 鉴权失败可见；请求携带 Bearer 与 `X-Agent-Id`；切换清空旧 Agent 的会话、文件版本和配置视图                            |
| 模型管理      | 读取 Provider/当前模型；保存 Provider URL/Key；设置 Agent 模型 | 使用 `/models`、`/models/active` 和 Provider config 接口；scope 固定为 agent，body 带 agent_id；错误不显示成功        |
| 渠道管理      | 读取渠道列表 → 读取单渠道 → 修改 JSON → 保存                   | 未读取当前渠道不能保存；配置必须是 JSON 对象；不提交整个渠道字典；后端校验错误可见                                    |
| 定时任务      | 列表 → 创建暂停的文本通知任务 → 暂停/恢复                      | 创建包含 schedule/timezone/dispatch；默认 enabled=false；暂停/恢复后刷新列表                                          |
| 文件浏览      | workspace/project 根目录 → 目录列表 → 子目录 → 后续分页        | 传递 root/path/cursor 和选中会话的 `X-Chat-Id`；切换根目录清除旧游标                                                  |
| 文件读写      | 分块读取文本 → 编辑 → 带 If-Match 保存                         | 所有块 ETag 一致；分页必须前进；冲突保持编辑内容；改变路径、根或会话后必须重新读取；编辑上限 2 MiB                    |
| 文件传输      | 多文件上传；二进制下载                                         | 上传 multipart 字段为 files；同名 rename；下载保留原始字节；日志仅显示文件元数据                                      |
| 会话          | 列表 → 新建 → 选择 → 历史                                      | 使用后端返回的 chat UUID 与 session_id，不混用；新建显式传递 user_id/channel                                          |
| 聊天          | 文本/单附件 → POST 流 → 内容增量 → 完成                        | 原生 POST fetch 消费 SSE；UTF-8、CRLF、多行 data 分片不丢失；delta 追加，快照替换；完成快照不重复；未知事件保留到日志 |
| 停止/异常恢复 | 停止接口 → 取消浏览器流；异常后加载历史                        | 后端返回 stopped=true 才主动取消流；中断/超时/错误有提示；未收到完成事件时不声称成功；不自动重发消息                  |
| 请求日志      | 页面查看 → 筛选 → 展开 → 清空/导出                             | 记录方法、路径、时间、状态码、耗时、请求头/体、响应体、SSE 事件；字段脱敏；日志有上限                                 |

当前管理界面用于验证 API 业务闭环，渠道配置采用 JSON 编辑而非复刻完整动态表单。文本内容以 `textContent` 展示，不执行响应里的 HTML。核心聊天现在提供工具卡片与交互式审批；多用户服务模式另支持持久化事件重连与历史回放。

以下不在此包的验收范围：完整插件兼容、Monaco/Lexical 等价编辑器、Markdown/GFM/公式/Mermaid、图表与 3D、拖拽与虚拟列表、Tauri 桌面桥接、完整国际化、全部管理模块、自动重连与跨页面消息队列。基础文本编辑不代表 Monaco 能力等价。

## 网页日志

所有应用 API 请求都经过 `src/api.ts`，包括文件下载、上传、登录、停止和 SSE。右侧请求日志无需打开浏览器开发者工具。

- 保留最近 100 个请求；每请求最多保留最后 100 条 SSE 事件，同时记录总事件数。
- 字符串摘要截断到 12,000 字符，数组最多 150 项；页面按动画帧合并日志刷新。
- `password`、`token`、`authorization`、`api_key`、`secret`、`cookie`、`credential` 等字段以及已知凭据值脱敏。二进制内容不进入日志。
- 业务正文、文件文本和其他非凭据字段仍会出现在摘要中。导出前可以查看、筛选或清空；筛选只影响显示，导出包含全部保留记录。
- 凭据不持久化，日志也只保存在当前页面内存。脱敏是已知字段和值的处理，不承诺识别自然语言里的所有秘密。

## 自动化验证

```sh
npm run check:deps
npm run build
npm test
npx playwright install chromium
npm run test:e2e
```

已有 Google Chrome 时可省去 Chromium 下载：

```sh
PLAYWRIGHT_CHANNEL=chrome npm run test:e2e
# 或执行全部检查
PLAYWRIGHT_CHANNEL=chrome npm run check
```

Playwright 在 `127.0.0.1:5180` 启动构建预览，测试拦截所有 `/api/` 请求；未知 API 返回失败，不透传后端。测试不要求模型账户、不消耗模型额度、不写入真实文件或配置。`npm run test:e2e` 需要先 build；`npm run check` 会自动 build。

Vitest 覆盖传输和消息归并的边界条件；Playwright 覆盖真实页面交互及请求契约。模拟接口通过说明本验证包符合已提取的协议，**不能替代真实后端、真实模型和真实文件系统的联调验收**。

2026-09-15 初版执行记录：`PLAYWRIGHT_CHANNEL=chrome npm run check` 通过（白名单检查、TypeScript/Vite 构建、7 项单元测试、4 项浏览器测试），`npm run format:check` 通过。另使用独立 Chrome 检查了 1440px 桌面和 390px 窄屏：无页面异常，窄屏无横向溢出。构建 JS 22.21 kB（gzip 9.22 kB），CSS 5.19 kB（gzip 1.87 kB）；这些是本验证包产物大小，不是与现有 Console 的等功能性能对比。真实后端联调尚未执行。

## 真实后端验收清单

使用专用测试 Agent，依次执行：

1. 登录/令牌连接；切换 Agent，核对请求日志的 Agent 头和 401 处理。
2. 读取模型配置，保存测试 Provider 和 Agent 模型，再读取确认；修改测试渠道后读取确认；创建暂停任务并验证暂停/恢复状态。
3. 浏览测试目录，读取多块文本、保存并重新读取。通过另一个客户端修改文件后，验证旧 ETag 保存报冲突且本地编辑仍保留。上传同名文件，核对 rename 结果；下载二进制核对内容。
4. 新建会话，发送中文、多行文本和附件；核对增量正文、最终结果及后端历史一致。生成期间停止，确认后端真的停止；断开连接后通过历史查看已保存结果。
5. 展开/筛选/导出日志，核对请求、响应和事件字段。确认密码、访问令牌及 API Key 不出现于日志；检查工具错误、HTTP 错误和早断流提示。

开发运行不自动执行这些有状态操作。本次自动化验收与真实联调结果应分开记录。

## 依赖边界与版本限制

只安装当前验证所需的白名单子集：`typescript ^5.7.3`、`@types/node ^24.7.1`、`vite 5.1.8`、`vitest 0.32.0`、`jsdom 19.0.0`、`@playwright/test 1.61.1`、`prettier ^3.1.1`。其他允许工具未使用即不安装。`package-lock.json` 固定实际解析结果，`check:deps` 校验直接依赖名称和声明范围且禁止运行时依赖/overrides。

约束覆盖项目直接依赖，允许上述工具自身的传递依赖。Vite 5 用于应用构建，Vitest 0.32 使用其自己的 Vite 4；`vitest.config.ts` 与应用配置隔离，不共享 Vite 5 插件或类型。Node 类型包不是 Node 运行时；建议使用 Node 22+，本次环境为 Node 23.10.0。

2026-09-15 安装审计报告 4 项依赖漏洞（low/moderate/high/critical 各 1），涉及 Vite、Vitest 及传递依赖。遵守硬性版本约束未自动升级或 override。开发/预览仅监听回环地址，测试使用 `vitest run` 且不开启 Vitest UI/API；这不等于消除旧工具的全部风险，不应将开发服务器用于公网服务。生产静态产物需由正式服务托管；`preview` 仅用于验证。

## 文件结构

页面按「装配根 → 壳层 → 各视图 → 各功能模块」分层。`main.ts` 只负责构建共享服务、
挂载外壳并按依赖顺序接线，不再承载任何具体流程。

```text
src/main.ts              装配根：服务实例、挂载外壳、模块接线、初始状态
src/shell.ts             应用外壳：侧边栏、页头、连接面板、两个日志抽屉

src/core/
  dom.ts                 元素查询、节点构造、下拉选项、下载、逐帧合并
  state.ts               唯一可变状态源（会话、文件、流、审批轮询）
  app.ts                 busy 锁、run/action 包装、通知与聊天模式
  tokens.css             设计令牌、元素重置、通用工具类
  shell.css              外壳布局、侧边栏、页头、连接面板

src/views/               每个视图的模板与样式同名成对，便于同处维护
  chat.ts    chat.css    视图 1：核心会话
  files.ts   files.css   视图 2：工作区文件（含检查点面板样式）
  inbox.ts               视图 3：收件箱与任务（面板样式在 src/panels.css）
  skills.ts              视图 4：智能体技能
  management.ts/.css     视图 5：管理与配置

src/features/
  connect.ts             连接、登录、清除凭据、聊天模式切换
  chat.ts                流式渲染、折叠时间线、工具卡片、审批、两种模式发送
  sessions.ts            会话列表/新建/选择、侧边栏、服务会话
  files.ts               目录浏览、分块读取、ETag 保存、上传下载
  management.ts          模型、Provider、渠道、定时任务
  logs.ts                协议日志与模型诊断两个抽屉
  tabs.ts                侧边栏页签切换
  logs.css               两个抽屉的样式

src/panels.css           收件箱、模态框、技能面板样式
src/style.css            样式清单：只声明 @import 顺序，构建后合并为单个 CSS

src/api.ts               统一请求、日志、脱敏、SSE 分帧
src/chat.ts              QwenPaw 文本增量与快照归并
src/markdown.ts          受限 Markdown 渲染
src/tools-render.ts      工具卡片渲染
src/tl.ts / service.ts / inbox.ts / checkpoints.ts / skills.ts / context-monitor.ts
                         既有的独立视图与服务模块
tests/                   Vitest 传输与协议边界测试
e2e/                     Playwright 隔离接口与页面流程验收
scripts/                 直接依赖白名单检查
```

重构保持了行为等价：元素 id、class、`data-testid`、文案与请求契约均未改动，
48 项单元测试和 17 项浏览器测试在重构前后同样通过。内联 `style="..."` 已全部
改为 `core/shell.css`、`views/*.css` 中的具名类，浏览器计算样式逐元素比对一致。
`src/style.css` 使用普通 `@import`，Vite 会内联为单个产物样式表。

### 模型日志与 Debug

顶部 **模型日志** 面板显示 TL Provider 的实际请求、响应及工具协议转换。先开启 **Debug** 再发送消息；后端须设置 `QWENPAW_MODEL_DEBUG=1` 并重启。详细用法、事件说明、脱敏与保留限制见 [模型诊断文档](../../docs/model-debug.md)。
