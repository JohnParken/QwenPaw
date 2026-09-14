# 云端 Runner / PC Web 精简

## 部署定位

默认安装面向 `qwenpaw app` 提供的 PC Web 和 Agent Harness。根目录
`docker-compose.yml` 构建 `deploy/Dockerfile.web`，使用无头 Chromium，直接
运行 Web 服务，不启动桌面环境。执行 `docker compose up --build -d` 后，
通过 `http://127.0.0.1:8088` 访问；云服务器可通过 SSH 隧道转发该端口。
已有认证环境变量和数据、秘密、备份卷保持有效。

`src/qwenpaw_cloud` 是另一个入口：现有实现仍限于 P0 本机、测试数据库、
离线模型和 NullMemory。它的租约、Scope、checkpoint 和 Runner 边界保留；
本次依赖裁剪不代表 P1 Linux/Kubernetes、多租户生产接入已经完成。
现有 Console 也不是该 P0 Core/File API 的直接客户端。

## 组件取舍

| 范围 | 处理 | 原因 |
| --- | --- | --- |
| Agent、Runtime、模型适配、上下文压缩、工具调用 | 保留 | Harness 执行主链路 |
| Skills、文件/Shell/代码工具、MCP/Driver、子代理 | 保留 | 云端仍需要完成实际任务 |
| 会话、检查点、审批、安全规则、流式输出 | 保留 | 状态恢复与 Web 交互所需 |
| Console React UI、编辑器、文件与工具结果渲染 | 保留 | PC Web 客户端能力 |
| Playwright、Chromium、Node、Git、字体 | 保留 | 无头网页自动化、MCP 和代码任务仍会使用 |
| IM SDK | 移到 `channels` extra | 仅服务 Web 不需要安装各 IM 平台客户端 |
| pywebview、mss、Textual | 移到 `desktop` extra | 桌面窗口、整屏截图和终端 UI 不属于云端 Web 服务 |
| transformers、onnxruntime、modelscope、huggingface_hub、Ollama SDK | 移到 `local` extra | 前两者无项目直接引用，模型下载与 Ollama SDK 均按本地能力加载 |
| XFCE、Xvfb、D-Bus 服务、Supervisor、vim、envsubst | 不进入 Web 镜像的直接安装/启动清单 | Web 服务直接启动，无头浏览器不需要桌面会话 |
| ReMe 与 auto-fin/daily-paper | 保留 | 当前配置会直接加载两个插件，不能只删包而保留配置 |
| 已删除的 Tauri 桌面发行物、构建链和原生 computer-use bundle | 从当前源码、镜像和文档入口移除 | 当前交付目标是 PC Web / 无头 Runner，不再提供已删除的桌面发行物和原生屏幕操作能力 |

源码大小不是运行时开销的可靠指标。内置 Office/PDF Skills 仍属于 Agent
处理用户文件的能力；不因为服务部署在云端而删除。

## 安装与兼容

```sh
# 默认 Web/Harness
uv pip install .

# 使用 IM 渠道时补装
uv pip install '.[channels]'

# 使用桌面窗口、截图或 TUI 时补装
uv pip install '.[desktop]'

# 本地模型下载与 Ollama embedding SDK
uv pip install '.[local]'

# 原完整功能集合
uv pip install '.[full]'
```

Web 镜像默认 `QWENPAW_ENABLED_CHANNELS=console`。额外渠道需要同时安装
对应 extra、调整渠道白名单并配置该渠道。移入 extra 的功能源码仍保留。

`deploy/Dockerfile.web` 是唯一的主服务镜像定义，不启动 Supervisor，使容器
停止信号直接到达 QwenPaw。旧桌面镜像和入口已经删除。
Compose 的 `init: true` 保留，用于回收子进程。

直接从源码 `pip install .` 不会自动构建 React 页面；完整 PC Web 部署优先
使用上述 Docker 构建，它会构建 Console 并将静态资源放入 Python 包。

迁移到无头镜像时，已有配置若显式设置 `browser.headless=false`，需调整为
`auto` 或 `true`。浏览器请求有头模式、整屏截图、桌面控制需要桌面运行环境。
保留 Playwright Python 包不代表宿主机已经安装浏览器；Web 镜像安装 Chromium。

桌面截图工具现在默认关闭。桌面部署需要安装 `desktop` extra，并在工具设置
中显式启用；已有配置中保存的启用值不会被覆盖，迁移到云端时应将其关闭。
Web 镜像使用 uv 安装 Python **3.12.13**，不再依赖基础镜像的系统 Python，
构建时会校验实际解释器版本。首次构建需要下载对应的 Python 运行时。

## 依赖锁与验收边界

`cloud/requirements-web.lock` 是精简后的 Web 运行依赖，沿用原锁文件中仍需
保留的版本，不包含 test/cloud/desktop/channels/local extras。此次在
macOS arm64、Python 3.12.13 上解析；Web Docker 构建将它作为约束文件，
由 Linux 解析器补齐该平台专属依赖，而不是用 `--no-deps` 跳过解析。

```sh
uv pip compile pyproject.toml --constraint cloud/requirements.lock \
  --python .venv/bin/python --output-file cloud/requirements-web.lock
uv pip install --constraint cloud/requirements-web.lock .
```

`cloud/requirements.lock` 和 `cloud/reports` 是已有 P0 验收基线，包含历史
完整依赖。它们不应作为精简环境的安装清单，也不能把旧验收报告视为本次
Runtime 的验收结果。源码或依赖改变后，已有 Runtime 指纹与恢复资格需按原
协议重新验证。

完整 PostgreSQL/Seatbelt 验收仍遵循 `cloud/README.md`。裁剪不改变租约、
数据库、认证、沙箱或可恢复外部副作用的现有限制。

## 本次验证（2026-09-14）

- 默认直接依赖由 58 项减为 39 项；仍需保留的版本沿用原约束。
- macOS arm64 / Python 3.12.13 的 Web 锁文件包含 181 个包；Linux x86_64 /
  Python 3.12 解析成功，增加 `jeepney`、`secretstorage` 两个系统密钥存储依赖。
  原 P0 的 255 项还含 test/cloud extras，不能把全部差额归因于功能裁剪。
- 隔离环境中确认 IM、桌面、本地模型相关包缺席，Web、Workspace、Agent
  导入成功，31 个内置工具、18 个 hook 以及命令和模式正常注册。
- 启动 smoke、Workspace bootstrap、取消保存与 P0 fixture Runner 测试共
  37 项通过。首次源码运行缺少内置邮件包的导入路径，补充
  `PYTHONPATH=src:packages/qwenpawmail-mcp/src` 后两项受影响测试通过。
- 本地 wheel 构建、安装与 `uv pip check` 成功；CLI 缺包提示、脚本语法、
  首次初始化/已有配置/自定义端口分支、修改文件的 Black/Flake8 检查通过。
- 未运行完整 Docker 构建（本机没有 Docker）、前端构建、真实模型调用或
  PostgreSQL/Seatbelt 原生验收；未测量镜像体积，不提供未经实测的体积降幅。

## 审计修复与镜像验收（2026-09-15）

- 桌面截图默认关闭，并验证默认工具筛选不包含它、显式允许后仍可选用。
  截图工具测试文件 3 项通过，相关 Flake8 检查通过。
- Dockerfile 改为安装并断言 Python 3.12.13，满足锁定 NumPy 的 Python ≥3.12
  要求；不再直接安装系统 `python3` / `python3-pip` / `python3-venv`。
- 新增真实容器验收脚本：构建镜像、初始化隔离容器、等待 `/api/healthz`
  报告 default Agent 已加载、读取 Console JavaScript、实际运行无头 Chromium，
  然后验证正常停止及使用已有配置重新启动。脚本不挂载用户目录、不传模型
  凭据、不暴露宿主机端口；结束后移除本次容器，保留镜像与验收日志。

```sh
python3 deploy/verify_web_image.py \
  --evidence-dir /private/tmp/qwenpaw-web-acceptance-new
```

证据目录必须是新目录。需要可访问的 Docker daemon；使用 `--build-arg`
可以覆盖基础镜像。输出 `result.json`、构建和容器日志，以及启动、浏览器、
重启探针结果。此验收不调用真实付费模型，不证明云端 P0 原生恢复协议。

本次尝试因本机没有 Docker CLI 而在构建前停止，`checks=[]`，没有任何镜像
验收通过记录。两项代码修复已完成，真实镜像验收仍需容器环境。
