# 多用户 TL 接入

TL 是 `/v1` 服务使用的模型协议。当前有三种使用形态：本地 `server_tl_local`、生产
多用户服务、个人 `/api/console/chat` 兼容模式。

## 本地 `server_tl_local`

前提是仓库 `.venv` 已安装 `.[server]`，两个 `test-tools` 项目已安装依赖，TL Proxy
已构建，且 `test-tools/tl-llm-proxy/.env` 已配置真实上游。用 `QWENPAW_SERVICE_PORT`、
`QWENPAW_CONSOLE_PORT` 或 `QWENPAW_SERVER_DEFINITION_PATH` 覆盖默认端口和定义文件。

从仓库根目录执行：

```bash
cd test-tools/native-console
npm run service:tl
```

默认启动 TL Proxy `8089`、本地 TL API/Worker `8092` 和 Native Console `5179`。本地配置
是 `deploy/server/assistant.tl.local.json`，退出后会话、任务和文件清空。该 profile 明确
使用 `noController`、`noShell`、`noBrowser`、`noMCP`：它只验证 TL 请求、SSE 和前端联调，
不代表生产工具能力或 TDSQL 验收。

已有实例会被复用而不是让本次启动失败：定义 `base_url` 指向的 TL Proxy 和已在监听的
`QWENPAW_CONSOLE_PORT` 前端都直接沿用，只有 `/v1` 服务必须由本次启动持有。前端代理的
令牌在它自己启动时确定，因此服务令牌优先取 `QWENPAW_SERVER_SERVICE_TOKEN`，其次取
`test-tools/native-console/.env.local` 中的 `QWENPAW_SERVICE_TOKEN`。需要本次启动独占
代理与端口时设 `QWENPAW_TL_EXTERNAL=0` 并先停止已有实例。

`/v1` 服务未运行时，浏览器只会看到开发代理返回的 `HTTP 500: ""`，不代表服务端崩溃；
先看启动终端是否打印 `Local /v1 service did not become ready` 或代理
`EADDRINUSE`，再确认 `http://127.0.0.1:8092/ready` 返回 `{"status":"ready"}`。

个人模式联调用 `npm run live:start`（仅底层服务用 `live:services`，仅前端用
`live:console`，停止用 `live:stop`）。它同样会启动 `/v1` 服务的第 3 阶段，因为前端
默认选中「服务聊天 /v1」；`/v1` 未就绪时 `live:console` 会在启动前给出提示。端口用
`QWENPAW_TL_PROXY_PORT`、`QWENPAW_PERSONAL_PORT`、`QWENPAW_SERVICE_PORT` 和
`QWENPAW_CONSOLE_PORT` 覆盖，与 `service:tl` 保持同一套变量；令牌优先取
`QWENPAW_SERVER_SERVICE_TOKEN`，其次取 `test-tools/native-console/.env.local` 中的
`QWENPAW_SERVICE_TOKEN`，并注入前端代理，两侧始终一致。

以下独立启动命令从仓库根目录执行：

```bash
QWENPAW_SERVER_SERVICE_TOKEN=local-tl-development-token-32-characters \
  .venv/bin/python scripts/server_tl_local.py \
  --definition deploy/server/assistant.tl.local.json
```

前端服务模式使用 `QWENPAW_SERVICE_TARGET=http://127.0.0.1:8092` 和同一令牌。

## 生产 TL

生产使用 `deploy/server/assistant.tl.json`，由 API 和 Worker 读取同一版本化定义，接入
TDSQL、S3、Kubernetes、PVC 和 Controller。Controller 即使定义只包含聊天，也必须部署，
因为 Worker 租约失效、Sandbox 清理和失败恢复依赖它。助手定义中的 `base_url` 指向
Worker 可访问的 TL 服务根地址；Pod 内不能使用本机开发默认的 `127.0.0.1:8089`。
上游凭据只配置在 TL Proxy/服务端环境中，不写入前端或助手定义。
统一环境模板和离线检查命令见 [配置与启动](multi-user-server.md#配置与启动)。

`deploy/server/kubernetes.yaml` 中的 `qwenpaw-platform` ConfigMap 是非 TL 示例；部署 TL
前必须用该 TL 定义替换 `assistant.json`，并让迁移 Job、API、Worker、Controller 挂载同一
版本。该清单会同时创建迁移 Job 和工作负载，发布脚本必须先保持工作负载为 0，等待迁移
成功后再扩容，不能把单次 `kubectl apply -f` 当作迁移闸门。

发布顺序是构建镜像、运行 `qwenpaw serve migrate`、确认迁移 Job 成功，再滚动 API、
Controller、Worker；Worker 在 Controller 就绪前不得接收任务。流量切换前完成
[multi-user-server.md](multi-user-server.md) 的生产验收门槛。

## 个人兼容模式

个人模式继续使用传统 App 的 `POST /api/console/chat`，SSE 返回 native `AgentRequest`
协议事件；默认服务端口为 `8088`。它可以使用个人 TL Provider 配置，但不读取生产平台的
多用户定义，也不提供 `/v1` 的服务令牌、用户隔离和持久化运行语义。

生产 `/v1` 的请求和事件合同见 [v1-api.md](v1-api.md)。前端默认选择服务模式；需要个人
本地模式时显式设置 `QWENPAW_CHAT_MODE=local`，联调服务目标和服务令牌按部署环境配置。

## 端口

| 组件 | 端口 |
| --- | ---: |
| 生产 API | 8090 |
| Controller | 8091 |
| 本地 TL API/Worker | 8092 |
| Sandbox（独立网络） | 8092 |
| TL Proxy | 8089 |
| Native Console | 5179 |
| 个人兼容服务 | 8088 |
| 个人兼容服务 | 8088 |
