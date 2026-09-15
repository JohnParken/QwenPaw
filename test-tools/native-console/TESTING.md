# QwenPaw Native Console 测试指南 (Testing Guide)

本文档专门用于指导如何使用 **`test-tools/native-console`** 开发展开各项能力测试。本测试包在**零浏览器运行时依赖（0 runtime dependencies）**约束下，完整复刻并增强了现代化 Agent 验证控制台。

本指南涵盖两种测试方式：

1. **自动化测试模式（Automated Mode）**：无需启动后端，在隔离沙箱/模拟接口下 100% 自动执行单元测试与 Playwright E2E 测试。
2. **真实后端联调模式（Live Mode）**：连接真实的本地或远端 QwenPaw 后端，重点验证 `tlprovider`（兼容 `tlproxy`）与 `deepseek-v4-flash` 模型，以及各项 Agent 高阶交互。

---

## 目录

- [一、前置环境准备](#一前置环境准备)
- [二、自动化测试模式（快速验证）](#二自动化测试模式快速验证)
  - [1. 依赖合规与代码规范检查](#1-依赖合规与代码规范检查)
  - [2. TypeScript 构建与 Vitest 单元测试](#2-typescript-构建与-vitest-单元测试)
  - [3. Playwright E2E 端到端浏览器测试](#3-playwright-e2e-端到端浏览器测试)
  - [4. 一键全量自动化验收](#4-一键全量自动化验收)
- [三、真实后端联调测试模式（实战操作）](#三真实后端联调测试模式实战操作)
  - [1. 一键脚本启动与关闭（推荐）](#1-一键脚本启动与关闭推荐)
  - [2. 手动分步启动指引与排查（可选）](#2-手动分步启动指引与排查可选)
  - [3. 详细测试场景与分步操作清单](#3-详细测试场景与分步操作清单)
    - [场景 1：连接后端与切换 Agent](#场景-1连接后端与切换-agent)
    - [场景 2：TL Provider 与 DeepSeek-V4-Flash 模型测试](#场景-2tl-provider-与-deepseek-v4-flash-模型测试)
    - [场景 3：基础流式问答与 Markdown / 代码块复制](#场景-3基础流式问答与-markdown--代码块复制)
    - [场景 4：深度推理与思维链（Thinking Block）](#场景-4深度推理与思维链thinking-block)
    - [场景 5：工具调用卡片验证（Shell / Unified Diff / 截图等）](#场景-5工具调用卡片验证shell--unified-diff--截图等)
    - [场景 6：Tool Guard 工具安全审批与策略切换](#场景-6tool-guard-工具安全审批与策略切换)
    - [场景 7：工作区检查点快照与一键回滚](#场景-7工作区检查点快照与一键回滚)
    - [场景 8：技能管理与热插拔切换](#场景-8技能管理与热插拔切换)
    - [场景 9：收件箱与后台异步任务中心](#场景-9收件箱与后台异步任务中心)
    - [场景 10：实时接口与 SSE 分帧日志审计与导出](#场景-10实时接口与-sse-分帧日志审计与导出)
- [四、常见问题排查 (FAQ)](#四常见问题排查-faq)

---

## 一、前置环境准备

1. **Node.js**：建议使用 Node.js >= 22（推荐 Node 22 或 23）。
   ```bash
   node -v
   ```
2. **包管理器**：使用标准 npm。
   ```bash
   cd test-tools/native-console
   npm ci
   ```
3. **浏览器支持**：自动化测试推荐使用本机已安装的 Google Chrome，或者使用 Playwright 下载的 Chromium：
   ```bash
   # 如本机未安装 Chrome，可安装 Playwright 内置浏览器：
   npx playwright install chromium
   ```

---

## 二、自动化测试模式（快速验证）

自动化测试运行在完全隔离的环境中，由 Playwright 和 Vitest 提供 Mock 接口，**无需启动 Python 后端，不消耗任何大模型 Token，不写入真实文件系统**。

### 1. 依赖合规与代码规范检查

确保严格遵守零运行时依赖限制（`dependencies: {}`）且代码风格一致：

```bash
cd test-tools/native-console

# 检查 package.json 是否新增了未授权依赖或运行时依赖（必须 0 运行时依赖）
npm run check:deps

# 代码风格 Prettier 校验
npm run format:check
```

### 2. TypeScript 构建与 Vitest 单元测试

编译 TypeScript 代码并执行 32 项核心单元测试（覆盖 Markdown 解析、XSS 转义、专用工具卡片、ContextMonitor、ChatStream、TL 配置校验与默认项）：

```bash
# TypeScript 类型检查与 Vite 生产构建
npm run build

# 执行 Vitest 单元测试
npm test
```

### 3. Playwright E2E 端到端浏览器测试

在真实无头浏览器中启动 `preview` 服务（端口 5180），自动完成全套用户交互用例（共 8 个端到端测试）：

```bash
# 使用系统自带 Chrome 执行（速度快，推荐）：
PLAYWRIGHT_CHANNEL=chrome npm run test:e2e

# 或使用 Playwright 自带 Chromium：
npm run test:e2e
```

**E2E 覆盖的核心用例说明**：

| 用例名称                           | 验证重点                                                                         |
| :--------------------------------- | :------------------------------------------------------------------------------- |
| **连接、Agent、模型、渠道和 Cron** | 鉴权登录、Agent 切换、Provider 配置保存、渠道修改、定时任务暂停/恢复             |
| **文件浏览、ETag 冲突和上传**      | 文件树分页、分块 ETag 读取、并发修改冲突拦截、多文件上传、二进制下载             |
| **会话创建、SSE 去重与未知工具**   | 会话生命周期、增量 delta 与快照去重、未识别事件日志记录、停止生成中断请求        |
| **401 可见、敏感字段脱敏与防 XSS** | 401 状态展示、Token/Password 自动脱敏打码、响应防 HTML 脚本注入、日志导出下载    |
| **Agent 核心能力综合验证**         | 收件箱待审批处理、技能列表开启/关闭切换、工作区快照回滚、Markdown 富文本渲染     |
| **TL Provider 读取与连接测试**     | `tlproxy` / `deepseek-v4-flash` 默认加载、配置字段校验、会话初始化测试、聊天测试 |
| **TL 仅文本限制与上传拦截**        | 选定 TL 模型后附件在发起前被前端阻断，纯文本正常提交至 Console Chat              |
| **普通 Provider 往返切换**         | 在 TL 模型与普通 OpenAI-compatible 模型之间自由平滑切换，请求路径自动适配        |

### 4. 一键全量自动化验收

执行一条命令跑通所有自动化验收项：

```bash
PLAYWRIGHT_CHANNEL=chrome npm run check
```

> 输出显示 `check:deps`、`build`、`test (32 passed)`、`test:e2e (8 passed)` 全部通过即表示自动化验收 100% 成功。

---

## 三、真实后端联调测试模式（实战操作）

当你需要连接真实的 QwenPaw 后端及大模型（如通过 `tlprovider` / `tlproxy` 连接 `deepseek-v4-flash`）开展端到端实测时，整体流量转发架构如下：

```text
┌──────────────────────────────────────┐
│ 浏览器：Native Console 测试台 (5179) │
└──────────────────┬───────────────────┘
                   │ HTTP / SSE 接口请求
                   ▼
┌──────────────────────────────────────┐
│  QwenPaw Python 后端服务 (8088)      │
└──────────────────┬───────────────────┘
                   │ POST /chatbbc/init_session & POST /chatbbc/chat
                   ▼
┌──────────────────────────────────────┐
│  tl-proxy 流量转发代理服务 (8089)    │
└──────────────────┬───────────────────┘
                   │ POST /v1/chat/completions (OpenAI 兼容接口)
                   ▼
┌──────────────────────────────────────┐
│ 上游模型 API (如 DeepSeek 官方 / 第三方) │
│ 模型：deepseek-v4-flash               │
└──────────────────────────────────────┘
```

为了开展完整的全链路实测，提供了**脚本管理（支持分离启动 / 双系统跨平台）**与**手动分步启动**两种方式：

### 1. 跨平台脚本管理（支持双系统与分离启动）

针对不同测试场景与操作系统，我们在 `test-tools/native-console` 目录下提供了完整的管理脚本集合，**100% 原生支持 macOS 与 Windows**：

| 操作目标                                                           | macOS / Linux (Shell) | Windows (CMD / PowerShell) | 跨平台统一 npm 命令     |
| :----------------------------------------------------------------- | :-------------------- | :------------------------- | :---------------------- |
| **步骤 A：启动底层服务集群**<br>`tl-proxy(8089)` + `QwenPaw(8088)` | `./start-services.sh` | `start-services.bat`       | `npm run live:services` |
| **步骤 B：启动方案 B 实战联调**<br>`Native Console(5179)`          | `./start-console.sh`  | `start-console.bat`        | `npm run live:console`  |
| **全链路一键启动**<br>依次拉起所有三阶段服务                       | `./start.sh`          | `start.bat`                | `npm run live:start`    |
| **全链路一键关闭**<br>安全停机并释放所有端口                       | `./stop.sh`           | `stop.bat`                 | `npm run live:stop`     |

---

#### 模式 1：分步解耦启动（推荐）

如果你希望将**基础服务**与**联调测试台**解耦，便于单独排查底层服务或频繁调试前端：

1. **第一步：先启动底层依赖服务**（`tl-proxy` 端口 8089 + `QwenPaw` 端口 8088）

   ```bash
   # macOS / Linux:
   ./start-services.sh

   # Windows:
   start-services.bat

   # 或统一命令:
   npm run live:services
   ```

   > 脚本将自动检测并编译 `tl-llm-proxy`，优先使用 `uv` 环境（执行 `uv run python -m qwenpaw app --port 8088`），后台拉起服务并轮询端口直至就绪。

2. **第二步：启动方案 B 实战联调控制台**（`Native Console` 端口 5179）
   ```bash
   # macOS / Linux:
   ./start-console.sh

   # Windows:
   start-console.bat

   # 或统一命令:
   npm run live:console
   ```
   > 脚本自动探测底层服务就绪状态，启动 Vite 开发服务器并在终端输出测试台访问地址：`http://127.0.0.1:5179`。

---

#### 模式 2：全链路一键启动

如果需要快速拉起所有三阶段服务（代理 -> 后端 -> 前端测试台）：

```bash
# macOS / Linux:
./start.sh

# Windows:
start.bat

# 或统一命令:
npm run live:start
```

---

#### 模式 3：全链路安全关闭

无论采用哪种方式启动，测试完成后均可通过关闭脚本一键安全释放所有资源：

```bash
# macOS / Linux:
./stop.sh

# Windows:
stop.bat

# 或统一命令:
npm run live:stop
```

**关闭脚本工作机制**：

- **macOS / Linux**：读取 `.run/*.pid` 发送 `SIGTERM` 优雅停机（5 秒超时强杀 `SIGKILL`），并使用 `lsof` 扫描清理 `5179`、`8088`、`8089` 残留孤儿进程。
- **Windows**：调用系统级 `taskkill /F /PID <pid> /T` 树状强杀关联进程，并通过 `netstat -ano | findstr` 兜底清理对应端口。
- 自动清理 `.run/` PID 临时文件。

---

#### 实时运行日志排查

所有后台服务的输出已统一重定向至 `logs/` 目录：

- **tl-proxy 代理日志**：`tail -f logs/tl-proxy.log` (Windows: `Get-Content logs\tl-proxy.log -Wait`)
- **QwenPaw 后端日志**：`tail -f logs/qwenpaw.log` (Windows: `Get-Content logs\qwenpaw.log -Wait`)
- **Native Console 日志**：`tail -f logs/native-console.log` (Windows: `Get-Content logs\native-console.log -Wait`)

---

### 2. 手动分步启动指引与排查（可选）

如需针对单个服务进行断点调试或查看前台控制台输出，可分别在 3 个独立终端窗口中按顺序启动：

#### 步骤 1：启动 tl-proxy 流量转发代理服务（端口 8089）

`tl-proxy` 作为协议转换与流量转发网桥，其核心职责为：

- **向内（对接 QwenPaw）**：暴露标准的两段式接口 `POST /chatbbc/init_session` 和 `POST /chatbbc/chat`。
- **向外（对接上游模型）**：将两段式指令组装为标准的 `/v1/chat/completions` 请求，透明转发给上游模型（如 DeepSeek 官方或兼容 API）。

```bash
# 打开终端窗口 1：进入 tl-proxy 项目所在目录
cd test-tools/tl-llm-proxy

# 确保已配置 .env (若无，可执行 cp .env.example .env)
# 启动服务:
node dist/cli.js
```

> **连通性快速验证**：
>
> ```bash
> curl -s -X POST http://127.0.0.1:8089/chatbbc/init_session \
>   -H "Content-Type: application/json" \
>   -d '{"prompt_variables":{"system_prompt":"test"}}'
> ```

#### 步骤 2：启动本地 QwenPaw 后端服务（端口 8088）

本项目已全面使用 `uv` 进行环境与依赖管理，推荐直接通过 `uv run` 启动：

```bash
# 打开终端窗口 2：在项目根目录下使用 uv 环境启动
cd /Users/yangxuezhen/git/QwenPaw
uv run python -m qwenpaw app --port 8088
```

#### 步骤 3：启动 Native Console 开发测试台（端口 5179）

```bash
# 打开终端窗口 3：进入测试工具目录
cd /Users/yangxuezhen/git/QwenPaw/test-tools/native-console
npm run dev
```

启动成功后，使用浏览器打开测试控制台：
👉 **`http://127.0.0.1:5179`**

---

### 3. 详细测试场景与分步操作清单

打开页面后，右侧常驻显示「接口与 SSE 请求日志」面板，左侧为功能主区域，可按以下步骤依次体验与验证：

#### 场景 1：连接后端与切换 Agent

1. **展开连接栏**：点击左下角连接胶囊按钮 `未连接`（或顶部连接卡片）。
2. **认证登录**：
   - 若后端未启用认证：访问令牌留空，直接点击 **「连接 / 检查」**。
   - 若后端启用了认证：输入你的访问令牌（Token），或点击“使用账号密码登录”输入用户名/密码后点击登录。
3. **确认连接状态**：
   - 看到提示 `已连接 · 鉴权已验证（或未启用鉴权）· Agent: default`，连接圆点变为绿色。
4. **切换 Agent**：
   - 点击左侧顶部 Agent 下拉框，选择不同的 Agent（如 `default` 或自定义 Agent）。观察所有会话与状态按 Agent 干净隔离。

---

#### 场景 2：TL Provider 与 DeepSeek-V4-Flash 模型测试

本控制台已默认配置使用 `tlprovider`（兼容 `tlproxy`）与模型 `deepseek-v4-flash`。

1. **核对顶部徽章**：
   - 顶栏左侧状态标签显示为 **`TL: deepseek-v4-flash`**。
2. **切换到「管理」标签页**：
   - 点击左侧或顶部导航的 **「管理」** 标签。
3. **读取 TL 配置**：
   - 滚动到 `TL Provider · chatbbc` 卡片，点击 **「读取 TL 配置」** 按钮。
   - 下拉框自动选中 `TL Proxy (tlproxy)` 或 `tlprovider`。
   - 下方表单自动填入：
     - **TL Base URL**：`http://127.0.0.1:8089`
     - **TL 模型标签**：自动选中 **`deepseek-v4-flash`**。
4. **测试连通性与观察代理转发**：
   - 点击 **「测试会话初始化」**：验证后端向 `tl-proxy` 发送 `init_session` 流程，右侧日志记录请求并显示成功信息；同时可在 `tl-proxy` 终端观察到初始化请求日志。
   - 点击 **「测试聊天（已保存配置）」**：后端会通过 `tl-proxy` 向 DeepSeek 发送一次真实轻量测试对话。此时观察：
     - **Native Console 日志**：显示请求耗时与成功状态；
     - **`tl-proxy` 终端日志**：打印出接收到的 `chatbbc` 两段式载荷以及转发给 DeepSeek 的 `/chat/completions` 请求与实时 chunk 流量。
5. **激活绑定**：
   - 点击 **「用于当前 Agent」** 按钮，当前 Agent 即锁定使用 `deepseek-v4-flash` 作为主模型。

---

#### 场景 3：基础流式问答与 Markdown / 代码块复制

1. 切换到 **「聊天」** 标签页。
2. 点击左上角的 **「新建会话」** 按钮，生成新会话。
3. 点击聊天输入框下方的快捷测试预设芯片 **「基础问答」**（或手动输入任意问题）：
   > `你好，请介绍一下 QwenPaw Agent 的主要功能和你能使用的工具。`
4. 点击 **「发送」**（或按快捷键）：
   - **流式增量效果**：观察文字平滑打字输出，右侧日志实时累积 SSE 事件。
   - **Markdown 排版**：输出渲染各级标题、粗体、无序列表、行内代码与引用块。
   - **代码块复制**：若回复中含有代码，观察围栏代码块右上角的语言标记与 **「复制」** 按钮，点击后提示“已复制”。

---

#### 场景 4：深度推理与思维链（Thinking Block）

1. 在聊天框中点击预设芯片 **「深度推理 (R1)」**（或输入需要复杂逻辑推演的问题）：
   > `请详细推演并解答经典的农夫、狼、羊、白菜过河问题，给出每一步的状态变换和思考逻辑。`
2. 点击发送后注意观察：
   - 消息气泡上方出现独立的 **思考过程模块**（带呼吸动画与思考时长计时：如 `思考中 (3.2s)`）。
   - 思考结束后，模块变为可折叠面板 `🧠 思考过程 (5.8s)`。
   - 点击表头可自由收起或展开完整思考内容。

---

#### 场景 5：工具调用卡片验证（Shell / Unified Diff / 截图等）

1. **验证终端命令执行 (Shell Tool)**：
   - 点击预设芯片 **「Shell 工具」** 或发送：`请执行终端命令查看当前目录下的文件列表。`
   - Agent 调用 Shell 工具时，界面呈现**黑底高亮终端卡片**，显示执行的命令（带复制按钮）、当前目录以及标准输出。
2. **验证文件读写差异 (File I/O Diff Tool)**：
   - 点击预设芯片 **「文件读写工具」** 或发送：`请在当前工作区创建一个 demo.txt 并写入一段文本，然后再读取它。`
   - 界面呈现 **Unified Diff 统一差异对比卡片**，绿色清晰高亮展示新增的内容行。
3. **验证多模态与浏览器预览**：
   - 当调用浏览器或截图类工具时，工具卡片自动解析图像 base64/URL，提供内嵌缩略预览，点击可放大查看。
4. **验证上下文容量监控 (Context Monitor)**：
   - 顶栏右上角实时更新当前轮次的 Token 统计与上下文占用百分比进度条（如 `2.1k / 128k (1.6%)`），超过 80% 变为警戒黄，超过 90% 变红。

---

#### 场景 6：Tool Guard 工具安全审批与策略切换

1. **策略切换**：
   - 顶栏右侧提供 **「审批策略」** 下拉框：
     - `SMART`（默认）：智能拦截高危命令。
     - `STRICT`：所有工具执行均拦截需审批。
     - `AUTO`：免密自动执行。
     - `OFF`：关闭安全守卫。
2. **触发高危拦截**：
   - 确保策略为 `SMART` 或 `STRICT`。
   - 点击预设芯片 **「触发安全审批」** 或发送：`请执行终端命令 cat /etc/hosts`。
3. **审批交互操作**：
   - 对话流暂停，弹出醒目的黄色安全审批卡片：`🛡️ 工具安全执行审批请求 (Tool Guard)`。
   - 提供 3 级操作选项：
     - **「批准单次执行 (Exact)」**：仅批准当前精确命令一次。
     - **「批准同类执行 (Similar)」**：批准该工具同类型后续操作。
     - **「拒绝 (Deny)」**：拒绝执行，Agent 收到拒绝原因并调整策略。
   - 点击任一操作，观察审批指令发送并继续后续流程。

---

#### 场景 7：工作区检查点快照与一键回滚

1. 切换到 **「基础文件」** 标签页。
2. 在右上侧的 **「工作区检查点快照」** 区域：
   - 点击 **「创建检查点」** 按钮，在弹窗中输入快照描述（如 `测试危险修改前`），点击确认。
   - 检查点列表中出现一条新的快照记录（含生成时间和哈希）。
3. 随意修改或保存当前工作区的一个文件。
4. 点击快照行右侧的 **「回滚」** 按钮：
   - 弹出确认提示后点击确认。
   - 界面显示 `已成功回滚工作区到检查点`，文件即刻恢复为修改前状态。

---

#### 场景 8：技能管理与热插拔切换

1. 切换到 **「技能」** 标签页。
2. 页面自动拉取并展示系统已安装的全部技能（Skills）卡片（包含技能名称、描述与开关状态）。
3. 点击任意技能右侧的 **开关滑块**：
   - 实时向后端发送启用/禁用状态更新请求。
   - 观察开关即时切换并给予操作成功的 Toast 通知。

---

#### 场景 9：收件箱与后台异步任务中心

1. 切换到 **「收件箱」** 标签页。
2. **查看通知与待审批项**：
   - 标签栏展示未读数角标。
   - 列表聚合未读消息与待审批请求，支持单项点击处理或一键“全部标记已读”。
3. **提交后台异步长任务**：
   - 在下方的“提交后台任务”输入框中填入复杂任务指令（如 `深入分析当前代码架构并整理报告`）。
   - 点击 **「提交为后台长任务」**。
   - 任务被提交至后端后台执行，返回任务 Run ID，支持随时在 Trace 面板中追踪执行进度。

---

#### 场景 10：实时接口与 SSE 分帧日志审计与导出

1. 无论在哪个标签页，右侧面板均实时记录所有 HTTP 请求与 SSE 流式事件。
2. **检查敏感信息脱敏**：
   - 点击展开任意一个登录请求（`/api/auth/login`）或模型配置请求（`/config`）。
   - 展开 JSON 数据，确认 `token`、`password`、`api_key`、`secret` 等字段均被处理为安全掩码，没有明文泄露。
3. **日志检索与导出**：
   - 在顶部的日志筛选框输入关键字（例如 `/chat` 或 `401`），列表即时过滤。
   - 点击面板顶部的 **「导出」** 按钮，浏览器立即下载标准的 `native-console-logs.json` 文件，便于提交缺陷排查。
   - 点击 **「清空」** 按钮可清理当前调试日志。

---

## 四、常见问题排查 (FAQ)

### Q1: 点击「连接 / 检查」提示网络错误或无法连接？

- **排查**：确认后端服务是否正常在 `8088` 端口运行。
- **解决**：如果你的后端在其他端口（如 `8000`），请使用带环境变量的方式重新启动开发服务：
  ```bash
  QWENPAW_API_TARGET=http://127.0.0.1:8000 npm run dev
  ```

### Q2: 聊天发送附件时提示 `TL v1 仅支持文本输入，请移除附件后发送`？

- **原因**：这是符合架构规范的预期行为。TL Provider 协议规范定义为纯文本系统提示词交互通道，不支持二进制附件直传。
- **解决**：在附件选择框中移除选中的文件，直接发送纯文本指令即可正常对话；若必须测试多模态文件上传，可在「管理」页面切回普通模型 Provider（如 `demo` 或视觉模型）。

### Q3: 运行 E2E 测试时报浏览器或通道超时？

- **排查**：部分 macOS 或 Linux 系统下沙箱可能限制了本地回环端口监听。
- **解决**：优先指定 Chrome 渠道运行测试：
  ```bash
  PLAYWRIGHT_CHANNEL=chrome npm run test:e2e
  ```
  如果未安装 Chrome，先执行 `npx playwright install chromium` 安装内置浏览器内核。

### Q4: 如何在修改代码后验证是否破坏了依赖规则？

- 任何时候只要运行：
  ```bash
  npm run check:deps
  ```
  该脚本会严格审查 `package.json`，确保没有引入任何第三方浏览器运行时依赖，保持验证包的高内聚与纯原生特性。
