# 多用户 Server 模式

> **P0 update:** Current deployment uses TDSQL. Follow [tdsql-storage.md](tdsql-storage.md) for storage, startup, migration and tests. PostgreSQL instructions and benchmark records below describe the previous prototype and are not TDSQL acceptance results.

Server 模式把 QwenPaw 拆成共享的 API、Worker、Controller 和按会话创建的 Sandbox。现有前端访问 BFF，BFF 访问 API；API 将任务和事件入 PostgreSQL，Worker 领取任务并调用模型，Controller 通过 Kubernetes API 管理工具 Pod。文件对象存储在 S3 兼容存储中。Redis 用于跨 API 实例通知。每个会话的工作区使用 PVC 的独立 `subPath`，路径为 `workspace_root/<session UUID>`；Sandbox 不挂载数据库、模型凭据或 Kubernetes ServiceAccount。

## 前提与启动

安装 Server 依赖并构建镜像：

```bash
pip install '.[server]'
docker build -f deploy/server/Dockerfile -t qwenpaw-server:local .
```

API、Worker、Controller 和 migrate 进程需要 PostgreSQL DSN、至少 32 个字符的服务令牌和内部令牌，以及助手定义文件：

```bash
export QWENPAW_SERVER_DATABASE_URL='postgresql://user:password@postgres/qwenpaw'
export QWENPAW_SERVER_SERVICE_TOKEN='请替换为至少32字符的随机值'
export QWENPAW_SERVER_INTERNAL_TOKEN='请替换为另一组至少32字符的随机值'
export QWENPAW_SERVER_DEFINITION_PATH=/config/assistant.json
```

首次部署或升级先执行一次迁移：

```bash
qwenpaw serve migrate
```

然后分别运行：

```bash
qwenpaw serve api                 # BFF，默认 0.0.0.0:8090
qwenpaw serve worker              # 共享 Agent Worker
qwenpaw serve controller          # Kubernetes Controller，默认 :8091
qwenpaw serve sandbox             # 仅在工具 Pod 内，默认 :8092
```

`sandbox` 必须设置 `QWENPAW_SANDBOX_TOKEN`，且长度至少 32；可用 `QWENPAW_SANDBOX_ROOT` 覆盖默认 `/workspace`。它不应作为独立公网服务运行。

## 配置

`ServerConfig` 只读取 `QWENPAW_SERVER_<字段名大写>`，只采集已声明的环境变量；配置模型冻结且定义 JSON 使用 `extra=forbid`。必填项是 `DATABASE_URL`、`SERVICE_TOKEN`、`INTERNAL_TOKEN`、`DEFINITION_PATH`。其余变量如下（括号内为默认值）：

```text
REDIS_URL                 （无）
CONTROLLER_URL            (http://qwenpaw-controller:8091)
MODEL_API_KEY             (空)
CONCURRENCY               (10，1-100)
PER_USER_CONCURRENCY      (4，1-100)
LEASE_SECONDS             (60，>=15)
APPROVAL_TIMEOUT          (600，>=10)
IDLE_SECONDS              (900，>=30)
NAMESPACE                 (qwenpaw)
SANDBOX_IMAGE             (qwenpaw-server:local)
WORKSPACE_PVC             (qwenpaw-workspaces)
WORKSPACE_ROOT            (/workspaces)
S3_BUCKET                 (qwenpaw-files)
S3_ENDPOINT               （无，S3 兼容服务时设置）
S3_REGION                 (us-east-1)
MAX_UPLOAD_BYTES          (20971520)
```

助手定义是版本化快照。`version` 已写入 `qp_server_definitions` 后，同版本只能对应完全相同的 JSON；修改助手定义应递增 `version`，再滚动 API/Worker。不要直接修改已存版本。

## BFF 认证与 API

API 不接受浏览器直接登录。每个请求必须带：

```text
Authorization: Bearer <QWENPAW_SERVER_SERVICE_TOKEN>
X-QwenPaw-User: <由你的 BFF 验证后的用户 ID>
```

`X-QwenPaw-User` 最长 256 字符；提交体中的 `usrid` 必须完全相同。示例：

```bash
BASE=http://localhost:8090
AUTH="Authorization: Bearer $QWENPAW_SERVER_SERVICE_TOKEN"
PRINCIPAL="alice"

curl -sS -X POST "$BASE/v1/runs" -H "$AUTH" -H "X-QwenPaw-User: $PRINCIPAL" \
  -H 'Content-Type: application/json' \
  -d '{"usrid":"alice","sessionid":"s1","channelid":"web","request_id":"req-1","message":"你好","attachments":[]}'

curl -N "$BASE/v1/runs/<run_id>/events" -H "$AUTH" -H "X-QwenPaw-User: $PRINCIPAL" \
  -H 'Last-Event-ID: 0'
```

可用路由包括运行提交/查询/取消、运行 SSE（`/v1/runs/{id}/events`）、会话及消息查询、审批决定、文件上传/查询/删除、会话导入文件和删除用户记忆。SSE 使用持久化事件序号；断线后用最后收到的 `id` 作为 `Last-Event-ID` 重连。`/health` 返回 `{"status":"ok","mode":"server"}`。

## 数据隔离与工具边界

所有运行、会话、消息、审批、文件和记忆查询都按 `user_id` 校验；运行还按 `session_id` 关联。文件对象键由服务生成（`uploads/<UUID>`），上传上限由 `MAX_UPLOAD_BYTES` 控制。Sandbox 仅允许助手定义中 `execution: "sandbox"` 且通过 JSON Schema 校验的工具；Pod 以非 root、无特权、只读根文件系统运行，并受清单中的 NetworkPolicy 限制。Controller 是唯一需要 Pod RBAC 的组件。

## Kubernetes 部署

`deploy/server/kubernetes.yaml` 提供 Namespace、ConfigMap、Controller ServiceAccount/Role、RWX PVC、迁移 Job、API/Worker/Controller Deployment 与 Service，以及 Sandbox NetworkPolicy。先准备一个名为 `qwenpaw-server-env` 的 Secret，至少包含`DATABASE_URL`、`SERVICE_TOKEN`、`INTERNAL_TOKEN` 对应的三个 `QWENPAW_SERVER_*` 变量（定义路径由清单设置），并确保镜像、PostgreSQL、Redis（如启用）、S3 和 RWX 存储已可用：

```bash
kubectl apply -f deploy/server/kubernetes.yaml
kubectl -n qwenpaw wait --for=condition=complete job/qwenpaw-migrate --timeout=180s
kubectl -n qwenpaw rollout status deployment/qwenpaw-api
kubectl -n qwenpaw rollout status deployment/qwenpaw-worker
kubectl -n qwenpaw rollout status deployment/qwenpaw-controller
```

迁移由数据库 advisory lock 串行执行，并校验已应用 SQL 的 checksum；若已应用迁移文件被修改，迁移会失败。Controller 的 Pod RBAC、`qwenpaw-workspaces` 的 `ReadWriteMany` 能力和集群内可拉取的 `sandbox_image` 是必要条件。

## 备份与恢复

Server 模式没有内置的备份/恢复 CLI。需要同时保护 PostgreSQL 和 S3 对象；PVC 工作区也属于持久数据。示例流程（先暂停写入并确认没有运行中的任务）：

```bash
pg_dump --format=custom --file=qwenpaw-server.dump "$QWENPAW_SERVER_DATABASE_URL"
aws s3 sync "s3://$QWENPAW_SERVER_S3_BUCKET" ./qwenpaw-s3-backup
# 按你的存储平台备份 qwenpaw-workspaces PVC
```

恢复到已停止的服务后，使用 `pg_restore` 导入同一数据库，恢复 S3 对象和 PVC，再运行 `qwenpaw serve migrate` 并启动 API、Worker、Controller。数据库、对象和 PVC 必须来自一致时间点；代码不会替你协调三者，也不会自动备份或恢复外部 Redis。

## 容量与验证边界

仓库中的 `scripts/server_load_test.py` 默认目标是持续 1800 秒（30 分钟）、100 个并发运行槽和 500 个 SSE 订阅者；它会检查 SSE 序号重复/倒退、重连和终止事件。可执行：

```bash
python scripts/server_load_test.py --base-url http://localhost:8090 \
  --token "$QWENPAW_SERVER_SERVICE_TOKEN" --duration 1800 --runs 100 --subscribers 500
```

这组数值是当前生产容量验收目标，不是代码内硬上限；实际容量取决于数据库、Redis、模型、S3、网络和 Kubernetes。已在本机执行真实 PostgreSQL/pgvector、Playwright Chromium 和 stdio MCP 组件测试；真实 Kubernetes/S3、外部模型和 HTTP MCP 的完整链路尚未验收。

## 接入细节

- `POST /v1/runs` 返回数据库任务记录，`id` 为 `run_id`，`session_id` 是内部 UUID。外部三元组 `(usrid, channelid, sessionid)` 只在提交时解析；读取历史、导入文件使用内部 UUID。
- `request_id` 在同用户下唯一；相同会话、渠道和载荷的重试返回原任务（仍绑定原配置版本），不同载荷返回 409。
- `GET /v1/sessions/{id}/messages?after=<seq>&limit=100` 按消息序号分页。SSE 的序号独立于消息序号。SSE `data` 保留 Runtime 载荷并增加 `persisted_at`，另有 `type=approval/terminal` 事件，最后以 `event: end` 返回终态。BFF 应关闭代理缓冲，透传 `Last-Event-ID`；断开 SSE 不取消任务。
- `POST /v1/approvals/{id}/decision` 请求体为 `{"approved":true}` 或 `false`；只有本人可操作。审批超时会取消任务。批准不改变工具目录、Pod 权限或网络规则。
- `POST /v1/files` 使用 multipart 字段 `file`，返回文件 ID；查询 `/v1/files/{id}` 返回有效期 300 秒的下载 URL。S3 使用标准 AWS SDK 凭据链，部署需配置 `AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY` 或适用的工作负载身份。
- `POST /v1/sessions/{id}/files` 请求体为 `{"file_id":"..."}`，要求会话空闲；随任务提交的 `attachments` 在任务开始时导入。文件在沙箱中的路径为 `attachments/<file UUID>`。显示文件名不参与路径构造。
- `publish_file` 工具将沙箱文件发布为本人可下载的对象引用；超过 64 KiB 的工具响应也会存为私有 JSON 文件。单个发布文件限制 20 MiB，同时受上传配置约束。
- `DELETE /v1/files/{id}` 删除对象和登记；已导入的会话副本仍属于工作区，使用沙箱文件操作删除。`DELETE /v1/memory` 清除本人记忆并取消尚未完成的抽取任务。

## 记忆、工具与当前实现范围

助手 JSON 支持 `embedding_model`。生产向量检索需要在**第一次迁移之前**安装 pgvector，并填写模型供应商支持的 embedding 模型名；配置后启动会检查向量表，无法使用时失败，不静默降级。未配置时是明确的关键词检索模式，示例配置默认采用此模式以免猜测平台的 embedding 模型。公共知识按定义版本索引，私人记忆检索同时校验 `user_id`。当前采用独立的抽取队列/存储适配器，不启用旧个人 ReMe/Scroll 插件；没有迁移旧记忆。

首批工具目录为 Shell、文件、浏览器、MCP、记忆和知识检索。Python/已审核脚本可在沙箱通过 Shell 执行；尚未实现旧内置工具全部名称/schema 的兼容层，也未接入旧 Skill 自动发现及安装流程。平台可在 Controller 的 `QWENPAW_SANDBOX_MCP_JSON` 配置 MCP 服务器，结构为 `{"servers":{"name":{"command":"...","args":[]}}}`；HTTP 服务器使用 `transport: "streamable_http"` 和固定 `url`。终端用户不能提交端点或启动命令。每个沙箱复用自己的 MCP 连接。

同会话任务以持久入队序号排序，数据库另有单活跃任务约束。用户并发限制跨 Worker 共享。任务租约失效后先确认旧 Pod 删除，再标记中断并释放会话；没有自动重放。取消等待最长受心跳间隔和 Pod 终止时间影响。回收保留 PVC 文件，浏览器和进程状态不保留。

## 运维与验收记录

API `/health` 用于进程存活，`/ready` 检查数据库表；`/internal/metrics` 需要 BFF 服务令牌，提供任务/工具状态计数、排队时间和活跃沙箱数量。模型用量、首事件直方图、Pod 资源、告警和仪表盘仍需接入部署环境的观测系统；当前不具备完整生产监控闭环。

清单默认 2 个 Worker、每个 10 个槽位，只有 20 个运行槽。100 并发容量测试需要扩大 Worker 数量或槽位，并同步配置数据库连接预算、节点资源和存储。Kubernetes 必须使用支持 NetworkPolicy 的 CNI，并按集群真实的 Pod/Service 网段补充禁止出站范围。先验证迁移 Job 成功再接入流量；每次升级需重新创建迁移 Job。回滚仅切回代码/配置版本，不回退数据表；旧任务保持原定义版本，排空或明确中断后停止旧 Worker。

压测脚本每个槽位固定用户与会话，连续提交多轮，避免不断创建新的沙箱；它报告连接错误、终态和持久化事件到客户端的时延（要求时钟同步）。Mock 模型可用 `python scripts/server_mock_model.py --port 8093` 启动；纯文本定义设置 `model=mock`、`base_url=http://<mock-host>:8093/v1`、`auto_memory=false`、`tools=[]`。测试工具负载时可加 `--tool-name shell --tool-arguments '{"command":"sleep 1"}'`，仅在专用验收定义中将该工具设为免审批。Mock 不调用外部模型。

2026-09-17 本机测试：服务端 28 项通过，包括真实数据库、向量隔离、审批、租约失效、取消竞争、Redis 故障补读、文件归属、浏览器重建和 stdio MCP 复用。Runtime/Workspace 回归中发现 7 项审批测试失败，已在未修改的 HEAD 基线复现。尚未执行进程 SIGKILL 的完整集群演练、真实 S3/PVC 一致备份恢复和 100 个工具 Pod 的 30 分钟测试。

100 任务槽/500 SSE 的本机多轮短测：5 秒升压后持续 60 秒，1,932 个任务全部完成，没有记录错误；提交 P95 94ms、持久化事件接收 P95 200ms。原始指标见 [server-load-smoke.json](server-load-smoke.json)。API、Worker 和 Mock 模型运行于同一本机 Python 进程，PostgreSQL 使用本机独立进程。先前的大连接池压测受到客户端空闲连接扫描开销干扰，已通过客户端性能分析确认并改为每用户小连接池；默认升压时间可用 `--ramp-seconds` 调整。短测未覆盖 30 分钟内存稳定性、多副本进程故障、真实模型与工具 Pod，因此当前交付可用于联调，不能标为完成全部生产验收。
