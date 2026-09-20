# 多用户 Server v1

本文是 QwenPaw 多用户服务的当前基线。主接口是 `/v1`；个人模式的
`/api/console/chat` 只作为兼容接口。存储使用 TDSQL/MySQL 兼容数据库和 S3
兼容对象存储，具体连接、迁移、表前缀和备份约束见
[tdsql-storage.md](tdsql-storage.md)。历史 PostgreSQL 压测记录不属于当前验收结果。

## 运行形态

| 形态 | 组成 | 数据与边界 |
| --- | --- | --- |
| 本地 TL | `server_tl_local`（内存） | `noController`、`noShell`、`noBrowser`、`noMCP`；退出即清空 |
| 生产服务 | API + Worker + Controller + TDSQL + S3 | Kubernetes、PVC 工作区；Controller 即使纯聊天也保留，用于租约失效和失败恢复 |
| 个人兼容 | 传统 App + `/api/console/chat` | 单用户本地配置和工具；不提供 `/v1` 多用户隔离语义 |

生产聊天由 Worker 调用模型；沙箱工具请求才经过 Controller/Sandbox。浏览器不直接持有服务令牌。
API、Worker、Controller、migrate 必须使用同一 TDSQL 数据库和表前缀；Sandbox 不挂载
数据库凭据或 Kubernetes ServiceAccount 令牌。长期记忆按用户隔离；外部
`(usrid, channelid, sessionid)` 映射到内部会话 UUID，历史、状态和沙箱按该 UUID 隔离。
运行、审批和文件访问均校验用户归属。

## 端口与网络

| 组件 | 默认端口 | 说明 |
| --- | ---: | --- |
| 生产 API | 8090 | BFF 的 `/v1` 目标 |
| Controller | 8091 | 集群内；内部令牌保护 |
| 本地 TL API/Worker | 8092 | `server_tl_local` |
| Sandbox | 8092 | 独立网络边界，不能作为公网服务 |
| TL Proxy | 8089 | TL 上游代理 |
| Native Console | 5179 | 联调前端 |
| 个人兼容服务 | 8088 | App 与 `/api/console/chat` |

Sandbox 使用单独的网络策略；不要把 Sandbox 与 API/Controller 通过同一公网入口暴露。

## 配置与启动

```bash
pip install '.[server]'
docker build -f deploy/server/Dockerfile -t qwenpaw-server:local .
```

统一配置模板为 [deploy/server/.env.example](../deploy/server/.env.example)。复制到版本库外
的私有文件后填写配置；`serve` 不会自动加载 `.env`。从仓库根目录执行：

```bash
set -a
. /absolute/path/to/server.env
set +a
.venv/bin/qwenpaw serve check --json
```

`check` 只校验环境字段、TDSQL DSN 和助手定义，不连接任何服务，不输出凭据。通过不代表
数据库或模型可用。生产至少配置以下 `QWENPAW_SERVER_*` 变量：

```text
DATABASE_URL       mysql://user:password@tdsql:3306/qwenpaw
SERVICE_TOKEN      至少 32 字符的随机值
INTERNAL_TOKEN     另一组至少 32 字符的随机值
DEFINITION_PATH    /config/assistant.json
```

完整 TDSQL 配置、S3、PVC 和安全约束见 [tdsql-storage.md](tdsql-storage.md)。首次部署或
升级必须先构建并发布镜像，再执行一次迁移，确认成功后按 API → Controller → Worker
顺序滚动发布；Worker 在 Controller 就绪前不得接收任务：

```bash
qwenpaw serve migrate
qwenpaw serve api                 # 独立进程，0.0.0.0:8090
qwenpaw serve controller          # 独立进程，集群内 :8091
qwenpaw serve worker              # Controller 就绪后启动独立进程
qwenpaw serve sandbox             # 只在工具 Pod 内启动
```

Kubernetes 清单位于 `deploy/server/kubernetes.yaml`。该单文件同时声明迁移 Job 和三个
Deployment，`kubectl apply -f` 本身不保证迁移先于 Deployment；发布脚本必须把工作负载
保持为 0，先单独创建/运行迁移并等待成功，再创建或扩容 API、Worker、Controller。
清单内置的 `qwenpaw-platform` ConfigMap 只是示例；使用 TL 或其他定义时，迁移前必须
替换该 ConfigMap 的 `assistant.json`，并确认 Job 与三个 Deployment 挂载的是同一文件，
避免内置定义静默覆盖选择的版本。定义文件的 `version` 是不可变快照；修改定义先递增
版本，再滚动 API/Worker，已提交运行继续使用原快照。

## 认证与 `/v1`

所有 `/v1` 请求都需要：

```text
Authorization: Bearer <QWENPAW_SERVER_SERVICE_TOKEN>
X-QwenPaw-User: <经过 BFF 验证的用户 ID>
```

`POST /v1/runs` 的 `usrid` 必须与 `X-QwenPaw-User` 完全相同；否则返回 `403`。未通过
服务令牌认证返回 `401`，缺少用户头返回 `400`。接口字段、状态码、SSE 事件和示例见
[v1-api.md](v1-api.md)。

健康检查：`GET /health` 返回 `{"status":"ok","mode":"server"}`；`GET /ready` 检查
数据库就绪；`GET /internal/metrics` 只接受服务令牌。

## 发布前验收门槛

必须在真实目标环境完成并留存证据：TDSQL 迁移与回滚策略评审、TDSQL/S3/PVC 一致备份
恢复、Controller 失败恢复和租约失效、API/Worker 多副本、真实模型与工具 Sandbox、
NetworkPolicy/RBAC、SSE 断线重连、文件归属与审批隔离，以及目标并发和 30 分钟稳定性
压测。历史 PostgreSQL benchmark、模拟模型、本地内存 runner 和短时 smoke test 只能用于
开发联调，不能关闭这些生产验收门槛。当前文档不执行部署。
