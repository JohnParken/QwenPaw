# QwenPaw 多用户云端长程任务 Harness：实施基线 v5

> **核心架构：共享 Harness API + PostgreSQL 持久任务表 + 常驻 Worker 池 + 单 Pod/单 Worker/单 Slot + 每次 Attempt 独立进程与私有可写环境 + 云端工作区快照。**
> **Kubernetes 仅承载 Worker Pod，不充当业务工作流引擎或任务编排控制面。**
> **本次决定：文件服务首期内置 Harness，但保持契约、元数据和存储驱动独立；以后可迁移到 BFF。首期 Worker 经 File API 中继内容；生产数据面独立部署，受限直传作为后续显式扩展。**
> **P1 修订：当前用户为可信用户；Kylin Linux Advanced Server V10（Tercel）/ Kubernetes v1.21.7 以 Pod 资源、网络和 Attempt 清理为执行边界，不要求任务级强沙箱。**

| 项目 | 说明 |
|---|---|
| 整理日期 | 2026-09-11 |
| 用途 | 后续技术评审、任务拆分、编码改造、联调与验收的统一基线 |
| 依据 | 本次对话中逐步收敛的需求、架构解释及源码核查结果 |
| 上游分析基线 | QwenPaw **v2.2.0**；这不是“当前最新版”的声明 |
| 文档状态 | 设计与实施计划；尚未完成实际代码改造、沙箱兼容性验证、部署或压测 |
| 版本关系 | 在 v4 上整合 Standalone Session、Workspace 生命周期与 Memory 决策，并重构文件服务边界；本文替代 v3/v4，不再依赖末尾增补覆盖前文 |
| 阅读约定 | “必须”是拟实施系统的验收要求，不表示上游已经具备；接口、模块和配置示例均为本项目拟新增，除非明确标注为原生能力 |
| 实施细化（2026-09-11） | 新增 Runner 生命周期、状态机事务表和能力矩阵 v1；本基线同步纳入等待/暂停规则、Memory 契约与 P2 文件提交链路。细化文档也是待实现要求 |
| 原则修订（2026-09-11） | 纳入单槽优先、生产文件数据面分离、必填 tenant/服务鉴权、两级公平调度、分级事件、UNKNOWN 回退及后续直传/Park/CAS/Memory Delta；同步更新三份细化契约 |
| 本机开发配置 | 明确支持 macos-dev，无虚拟机/Docker 前置；本机业务验证与 Linux 生产隔离验收分开，开工判断见第 20.3 节 |
| P1 范围修订（2026-09-14） | [Kylin V10 可信任务执行边界](P1_Trusted_Kylin_Execution_Boundary.md) 是 P1 权威范围；强沙箱降为未来接入不可信用户前的独立安全阶段 |

## 阅读导航

| 主题 | 章节 |
|---|---|
| 范围与总体设计 | [1. 目标与非目标](#s01) · [2. 决策清单](#s02) · [3. 术语](#s03) · [4. 总体架构](#s04) |
| 多用户与容量 | [5. 工作区隔离](#s05) · [6. Worker 与多执行槽](#s06) |
| 执行边界 | [7. 沙箱位置与生命周期](#s07) · [8. 文件、进程和资源隔离](#s08) · [9. Runtime 受控联网](#s09) |
| 状态与可靠性 | [10. 云存储和工作区版本](#s10) · [11. QwenPaw Runner](#s11) · [12. 任务领取与租约](#s12) · [13. 长程任务与恢复](#s13) |
| 服务契约 | [14. API 与事件协议](#s14) · [15. 持久数据模型](#s15) · [16. 完整执行时序](#s16) |
| 代码与交付 | [17. 上游改造落点](#s17) · [18. 代码模块与配置](#s18) · [19. 部署与运维](#s19) · [20. 实施阶段和任务](#s20) |
| 文件服务与未来拆分 | [24. 文件契约、可靠性、迁移与专项验收](#s24) |
| 实施细化契约 | [Runner 生命周期与恢复](QwenPaw_Multiuser_Runner_Lifecycle_v1.md) · [状态机与事务表](QwenPaw_Multiuser_State_Transactions_v1.md) · [首期能力矩阵](QwenPaw_Multiuser_Capability_Matrix_v1.md) |
| P1 可信执行边界 | [Kylin V10（Tercel）/ Kubernetes v1.21.7](P1_Trusted_Kylin_Execution_Boundary.md) |
| 本机开发与开工条件 | [macos-dev](#macos-dev) · [P0 开工判断](#p0-readiness) · [待落实事项](#development-decisions) |
| 验收与待定事项 | [21. 验收矩阵](#s21) · [22. 待验证项与风险](#s22) · [23. 实施约束摘要](#s23) · [来源](#sources) |

---

<a id="s01"></a>
## 1. 目标、范围与非目标

### 1.1 业务目标

把 QwenPaw 的 Agent、工具、Skills、MCP、会话和记忆能力改造成可被可信网页前端通过 BFF 使用的多用户长程任务服务。用户既可不创建工作区直接对话，也可创建工作区、上传资料、持续对话、提交后台任务、观察进度、补充输入、取消任务并下载产物。

交互可以采用“项目/工作区 + 对话 + 文件 + 任务”的网页模式。这里是产品形态类比，**不声称复现或知晓 ChatGPT 的内部基础设施实现**。

工作区与会话是持久业务对象，不是常驻进程。任务可由不同 Worker 执行；连续性由外部存储中的已提交状态提供，而不是依赖同一个 Agent 对象始终存活。

### 1.2 已明确的需求边界

- **K8s 仅用于部署云端执行环境。** 业务服务不按用户或 Run 动态创建 Pod、Job、PVC、Service，不开发 Operator、CRD、Kubernetes Provisioner。
- **前端经过可信 BFF。** 用户登录、注册、SSO、认证及业务角色授权不在本次设计范围内。
- **仍然必须实现数据分域与执行能力限制。** 可信 BFF 不等于模型生成的代码、上传文件、网页或第三方工具可信；不设计登录不等于让执行环境持有平台全部权限。
- **共享执行资源，但不共享可写用户运行环境。** P0—P3 一个 Worker Pod 只有一个 Slot，顺序服务不同可信用户；每个 Attempt 使用新的私有可写环境。
- **P1 使用 Pod 作为可信任务执行边界。** Runtime 在 Worker Pod 内以新进程运行，资源由 Pod/container cgroup 约束，网络由 Pod 级 NetworkPolicy 约束；不宣称抵御恶意用户或容器逃逸。

**文件模块当前归 Harness 部署与维护。** BFF 首期仅转发上传/下载及业务请求；不是现在就把文件存储迁移到 BFF。这里的“独立解耦”指可单独替换和迁出的模块边界，不要求首期额外部署微服务。

### 1.3 首期不做

不把 Hub 全量改造成云端控制平台；不强制迁移 Hub 的数据库；不改造完整桌面 Console；不引入必需的工作流引擎；不把 Redis、Kafka、Temporal 作为启动前置条件；不覆盖模型训练、本地大模型部署或 GPU 调度。

不承诺任意 Python 协程、Shell 进程、浏览器句柄或远程工具在任意位置无损恢复；不承诺通用的外部副作用 exactly-once；不把高并发或绝对安全当作未经测试的现成能力。

<a id="s02"></a>
## 2. 收敛后的架构决策

本节区分三类内容：**需求边界**是对话中明确限定的方向；**实施默认**是当前方案建议采用的默认实现；**待验证**必须通过技术验证后决定，不能作为已验证事实。

| 编号 | 决策 | 分类 |
|---|---|---|
| D01 | K8s 只部署执行环境，不参与业务 Run 编排 | 需求边界 |
| D02 | 网页经可信 BFF 调用，不建设登录认证授权体系 | 需求边界 |
| D03 | 用户、工作区、Session、Run 与 Pod 解耦，不固定绑定 | 实施默认 |
| D04 | 共享 Harness API，应用层持久任务表，Worker 主动领取 | 实施默认 |
| D05 | P0—P3 一个常驻 Worker Pod 一个 Slot；P4 多槽仅在隔离与资源验证通过后开启 | 实施默认 |
| D06 | 一个执行槽同时运行一个 Attempt；P1 每 Attempt 创建独立 Runner 进程和私有可写目录，不要求任务级强沙箱 | 需求边界 |
| D07 | 不跨用户复用有状态 QwenPaw 解释器、浏览器 profile 或可写 HOME | 实施默认 |
| D08 | P1 QwenPaw Runtime 位于单槽 Worker Pod 内；工具不因取消强沙箱而自动扩大开放范围 | 实施默认 |
| D09 | P1 网络采用 Pod 级默认拒绝与显式允许；同 Pod 工具共享该网络权限的残余风险由可信用户前提接受 | 实施默认 |
| D10 | PostgreSQL 保存任务事实；对象存储保存持久内容；本地是工作副本 | 实施默认 |
| D11 | Workspace 模式按工作区串行写入；Standalone 模式按 Session 串行写入 | 实施默认 |
| D12 | 默认不跨工作区共享记忆、文件、缓存或检索结果 | 实施默认 |
| D13 | 任务生命周期独立于 HTTP/SSE；断开网页不隐式取消任务 | 实施默认 |
| D14 | 等待输入先提交状态，再释放计算槽；首期保留工作区逻辑占用 | 实施默认 |
| D15 | 恢复基于已提交边界，使用 Attempt、租约代数、工作区版本和工具账本 | 实施默认 |
| D16 | 不把整个 Bucket、数据库、Worker 管理能力暴露给 Runner；全局凭据仍只由可信服务持有 | 实施默认 |
| D17 | Pod 资源、安全上下文、网络策略和 Attempt 清理不允许被 Agent 配置、审批模式或工具参数关闭 | 实施默认 |
| D18 | 单 Slot、Pod 资源、kubelet PID 限制、CNI NetworkPolicy 和逐工具能力范围须实测 | 待验证 |
| D19 | Workspace 可选；Session 支持 standalone / workspace | 需求边界 |
| D20 | 文件管理首期在 Harness 内，后期计划迁到 BFF 文件模块 | 需求边界 |
| D21 | 文件元数据与授权只走 File API；首期字节中继，后续可扩展受限传输授权 | 实施默认 |
| D22 | 文件元数据由文件模块独占；Workspace/Run/Memory 语义仍归 Harness Core | 实施默认 |
| D23 | 从首期采用跨模块幂等、引用保留和补偿，不依赖跨模块数据库事务 | 实施默认 |
| D24 | tenant_id 必填；首期 Workspace 单 owner，不开放多人共享；actor 单独审计 | 实施默认 |
| D25 | BFF 服务身份与用户上下文必须验证；身份头不作为认证依据 | 实施默认 |
| D26 | 生产文件数据面与 Core 独立进程/实例组，首期中继，受限直传后续另验 | 实施默认 |
| D27 | PG 保存权威事件和消息快照；暂态 Token 尾部可丢失并明确提示 | 实施默认 |
| D28 | P2 实现两级配额原子预留与加权公平领取；不以 SKIP LOCKED 代替公平性 | 实施默认 |
| D29 | Park & Yield、私有 CAS、Memory Delta 后续按需；全面 Workspace 并发合并独立立项 | 实施默认 |
| D30 | UNKNOWN 保持事实，按工具冻结失败/限时对账/低风险预授权重试/可选跳过；不假定已撤销 | 实施默认 |
| D31 | 支持本机 macos-dev：进程级 Worker/单 Slot；协议相同，能力差异显式报告；Linux 验收不得由本机结果替代 | 实施默认 |
| D32 | 首期任务分发使用项目内 Python + PostgreSQL 实现，暂不引入 Prefect、Temporal、LangGraph、DBOS、Procrastinate、Celery 等调度/编排框架 | 需求边界 |
| D33 | 首期控制面与 Python Worker 独立进程、通过版本化网络协议交互；控制面未来可替换为 Java，Worker 不依赖 Core ORM、业务数据库或共享目录 | 需求边界 |

### 2.1 相对前序版本的明确修订

| 容易误解的旧表述 | 以本版为准 |
|---|---|
| 每用户一个 Runtime Pod/PVC | 常驻通用 Worker 池，工作区状态外置；不动态建立用户专属 Pod |
| 一个 Pod 一个任务 | P0—P3 单 Slot 常驻池；不按 Run 创建 Pod；后续多槽须额外验证 |
| 独立 Python 进程就实现强安全隔离 | P1 只把新进程用于状态清洁；资源和网络边界由 Pod 提供，且仅面向可信用户 |
| P1 必须启用 QwenPaw 原生 sandbox | 当前 P1 不要求任务级强沙箱；若接入不可信用户，必须新增独立强沙箱阶段 |
| Runtime 与工具天然拥有不同网络权限 | P1 同 Pod 共享网络权限；通过工具范围和 Pod 级允许列表收敛，残余风险明确记录 |
| 取消强沙箱就可以开放全部工具 | 工具开放仍按能力矩阵推进，P1 不自动开放 Shell、Browser、MCP 或插件 |
| 清空一个目录即可复用下一用户 | 销毁任务可写环境、进程树和临时能力；清理未确认不得复用 |
| Pod 重启后自然续跑 | 由应用层恢复协议重建环境，不依赖 Pod 自动恢复内存状态 |
| 所有 Session 都必须归属 Workspace | Workspace 可选；Standalone Session 也有版本、并发与恢复边界 |
| 当前把全部文件能力移到 BFF | 当前仍内置 Harness；先建立可迁移的契约和私有实现 |
| Supervisor 下载文件意味着直接使用 OSS SDK | Supervisor 通过 File API 下载；SDK 只在文件模块的驱动内 |
| 文件模块与业务模块共库就可以任意联表和同事务写入 | 可共数据库实例，但必须独立 schema/Repository/事务所有权 |

<a id="s03"></a>
## 3. 术语与职责

| 术语 | 本文定义 | 不能混同为 |
|---|---|---|
| Harness | 负责 Agent 任务生命周期、执行适配、进度、状态、恢复与约束的应用层系统 | Kubernetes 或某个大模型 |
| Workspace / 工作区 | 一组持久的项目文件、会话、记忆与运行配置的业务空间 | Pod、Linux 目录名或全局用户身份 |
| Session | 一段连续对话；可 standalone，也可归属 Workspace | 一次任务或一个长期进程 |
| Run | 用户一次提交的逻辑任务，可包含等待输入和多次执行尝试 | HTTP 连接或原生 chat_id |
| Attempt | Run 的一次实际执行尝试；恢复通常创建新的 Attempt | 新的业务任务 |
| Worker Supervisor | 常驻可信程序，领取任务、分配槽、管理 Runner 进程、续租、清理 | 负责业务规划的 Agent |
| Slot / 执行槽 | 可同时承载一个 Attempt 的逻辑容量单位 | 天然安全边界 |
| Task Sandbox | 面向不可信执行者的可选系统级强边界；当前可信用户 P1 不启用 | 单纯的工作目录、Pod 资源限制或提示词 |
| Tool Sandbox | 比任务边界更窄的工具执行环境 | 任意工具都会自动获得的特性 |
| QwenPaw Runner | 本项目拟新增的无界面执行适配入口 | 上游已有的同名 CLI 或稳定 SDK |
| QwenPaw Runtime | 加载工作区和会话，运行 Agent、工具、记忆与生命周期逻辑 | 模型权重服务 |
| Workflow | 组织具体步骤的业务执行逻辑，可以包含 Agent | Worker 或执行槽 |
| File Service / 文件模块 | 管理文件上传、不可变版本、内容流、引用保留、存储映射和物理回收 | Run 调度器、Workspace 状态机或文档解析 Agent |
| FileGateway | 业务侧使用的稳定文件服务契约，可经进程内或 HTTP 适配调用 | OSS/S3 SDK 的同义包装 |
| Storage Broker | 前版对可信存储代理的统称；v5 将其持久文件职责收敛到 File Service | Supervisor 内的全局对象存储客户端 |
| Fencing / 租约代数 | 服务端递增标识，用于拒绝旧执行者的提交 | 能自动撤销外部 API 副作用的机制 |

**首期推荐直接执行 QwenPaw Agent，不另建必须的工作流引擎。** 有界片段与检查点可以由 Runner 协调；以后需要固定流程或多 Agent 编排时，它们仍由 Worker 执行，Worker 不负责重新实现业务规划。

<a id="s04"></a>
## 4. 总体架构与信任边界

### 4.1 首期：Harness 所属模块，生产控制面与数据面分进程

```text
可信网页 → BFF → Harness 应用
                  ├── Harness Core / Run、Event、Workspace、Session API
                  │      ├── harness schema：任务、租约、版本、事件
                  │      └── FileGateway ───────────┐
                  │                                ▼
                  ├── File API / File Service（可独立迁出的模块）
                  │      ├── files schema：文件版本、上传记录、保留引用
                  │      └── BlobStorePort → OSS/S3 等持久存储驱动
                  └── 内部 Worker API
                           ▲
                           │ 领取、续租、提交状态
                     Worker Supervisor
                       ├── FileApiClient → Harness 内的 File API
                       └── 单 Slot → Attempt Runner 进程 / 私有可写目录
                                            └── QwenPaw Runtime / 已批准工具
```

首期文件字节路径：`BFF/Worker → Harness 所属 File API → 存储驱动 → 对象存储`。图表示模块归属，开发可同进程；生产 File API 使用独立进程/实例组和连接预算。Run API 不把文件字节塞进任务 JSON。一个入口地址可按路由分发到不同进程，不代表数据面必须占用 Core 的事件循环。

### 4.2 后期：只迁移文件服务的部署边界

```text
BFF
 ├── 网页业务 API → Harness Core → harness schema
 └── File API / File Service → files schema → BlobStorePort → 对象存储
          ▲
          └── Harness FileGateway / Worker FileApiClient
```

迁移的是 File API、文件应用逻辑、文件元数据、上传会话、引用保留与存储驱动。Harness 的 Workspace Revision、Session、Memory 逻辑、Run/Attempt、租约及检查点发布不迁移。BFF 文件模块不负责解释 QwenPaw Memory，不拥有工作区 Head 的写入权。

### 4.3 职责分工

| 组件 | 负责 | 不负责 |
|---|---|---|
| BFF（首期） | 网页上下文、流式转发文件、Run 与事件交互 | 不直连文件存储，不接管文件权威元数据 |
| Harness Core | 业务范围、Workspace/Session 生命周期、任务、Memory 版本、检查点发布 | 不使用存储 SDK、不拼 bucket/key、不直接操作文件表 |
| File Service（首期内置） | 上传/下载内容流、不可变 FileRef、校验、引用集合、存储驱动与 GC | 不修改 Workspace Head、不调度 Agent、不反向等待当前 Run |
| Supervisor | 通过 File API 准备输入/工作副本、安全收集输出、管理 Runner 进程和 Attempt 清理 | 不持有存储 SDK 或底层存储凭据，不提供任意文件代理给 Agent |
| Runner | 在单槽 Worker Pod 内以新进程运行 QwenPaw，使用已准备的本地内容 | 不管理平台文件服务，不领取其他任务 |
| ExecutionBoundary | 验证 Pod 资源/安全上下文/网络前提，管理私有目录、进程终止和清理确认 | 不提供面向恶意租户的强沙箱保证 |

Core 与 File Service 可共数据库实例，但各自独占 Repository/schema；首期生产分进程，使用 HttpFileGateway，进程内 FileGateway 保留给开发与契约测试。Worker 从首期使用版本化 FileApiClient；进程内外适配都遵循相同的超时、错误、幂等和提交语义。

### 4.4 信任边界

平台控制面、Supervisor 与文件服务是可信组件；模型输出、工具、可变 Skills 与上传内容按不可信数据处理。Scope 由可信服务固定，不能由 Agent 任意改写。进度 IPC 与工具 stdout/stderr 分离，文本输出不能变成平台管理指令。

只有 File Service 的存储适配器持有底层全局凭据。开发可共进程，但不能把模块边界冒充安全隔离；首期生产文件数据面独立进程/实例组，Core 不持有该凭据。后续直传授权仅是范围受限的临时能力，不扩大 Worker 管理权。

### 4.5 Tenant、所有权与服务鉴权（P2 前冻结）

tenant_id 是必填的数据与配额隔离域；Organization 首期由上游映射到 tenant，不另建组织系统。user_id 表示租户内用户，Workspace 必须有一个非空 owner_user_id；一个 owner 可以有多个 Workspace，不对 owner_user_id 单独加唯一约束。actor_user_id 单独记录调用者。首期不支持多人共享，未来通过成员权限扩展；Run/Session/FileRef/Memory 的关联校验均包含 tenant 与 owner。

BFF→Harness 使用 mTLS 或等价工作负载身份验证调用服务，并使用 BFF 签发的短期用户上下文令牌；令牌固定 issuer、audience=Harness、tenant、user、操作范围与有效期。Harness 按配置允许的签名算法和签发者验证，支持密钥轮换和有界时钟偏差，拒绝过期、错误 audience/issuer 或缺失 tenant；再独立验证请求资源归属。签名上下文不意味着可任意访问该 tenant 所有资源。

BFF 清除/覆盖客户端身份头，Harness 不接受裸 X-User-Id 作为身份来源；重试依赖业务幂等键，不将短期身份令牌当业务去重键。Worker、文件协调器使用独立工作负载身份与权限，BFF 无权 claim Worker，Runtime 无权调用管理接口。生产入口须限制到批准服务，即便仅内网也执行上述验证；不允许认证失败回退为单用户/default。此契约不扩建登录/注册/SSO 系统。

<a id="control-worker-boundary"></a>
### 4.6 控制面与执行面解耦：首期边界与 Java 演进

首期分别提供 control-api、worker、runner、file-service 启动入口；这些是拟实施进程角色，不是已有命令。Core 与 Worker 必须独立进程，macos-dev 可同机不同端口，生产支持分机部署；File Service 仍遵循既有开发/生产部署约束。同仓库不意味着共享进程或数据库访问权。首期 Core 使用 Python，未来可由 Java 实现相同协议，不要求现在实现 Java 或引入调度框架。

| 边界 | 控制面 Core | Python Worker / Supervisor |
|---|---|---|
| 调度 | 公平选择 Run，原子检查配额/Scope/版本并建立 Attempt | 报告空闲槽和能力，通过 Core claim 获取执行指令 |
| 执行归属 | 签发租约、检查代数、决定失租与恢复 | 续租、本地 watchdog；失联按期限停止，不自行延长租约 |
| 生命周期 | 接受用户输入、取消和暂停意图，决定业务状态 | 启动/恢复 Runtime、停止写入、导出、关闭和报告边界 |
| 持久提交 | 校验 FileRef 封存、租约和 revision，发布 Head 与终态 | 通过 File API 上传，发起提交/查询；完成报告不直接等于 Run 成功 |
| 清理 | 记录确认或隔离，决定是否允许后继执行 | 确认沙箱/后代进程退出和临时能力撤销，失败停用槽 |

Worker 不持有业务 PostgreSQL DSN/账号，不导入 Core ORM/Repository，不直接执行 SKIP LOCKED 或修改任务表。调度查询、恢复扫描和事务锁序均归 Core。Worker 只依赖独立协议模型/客户端与执行组件；Runner 不继承 Worker 服务凭据。跨部署数据使用 FileRef、Manifest 和逻辑路径，不依赖共享目录；禁止以 pickle、Python 类实例、异常对象或回调作为远程协议。

首期采用 HTTP + JSON + OpenAPI 定义 Worker API，与 File API 独立版本管理。规范及固定请求/响应样本是跨语言契约，不能只把 Python 类型注解当成协议。至少覆盖以下操作，具体 URL/schema 在 P0 冻结最小集、P2/P3 按版本扩展：

| 操作 | 必须明确的语义 |
|---|---|
| register / capabilities | 进程实例 ID、协议/Runtime/profile 版本、能力报告和槽容量；自报能力仍受 Core 配置允许范围约束 |
| claim / claim 查询 | request_id 幂等；响应丢失查回原分配，不再次占额度；无任务返回空结果与退避建议 |
| heartbeat / control | 请求续租，响应租约期限及带 ID 的停止/取消指令；重复指令可安全处理 |
| operation begin/result/query | 意图、执行授权、请求摘要与已知/未知结果；网络重试不再次分发副作用 |
| checkpoint submit/query | 稳定 commit_id、完整 FileRef 集合、expected_revision；按文件提交协议返回权威结果 |
| boundary / cleanup | 继续、等待、暂停、完成、失败与清理状态；等待/终态由 Core 条件提交确认 |

所有变更请求携带稳定 request_id；执行相关请求带 attempt_id/lease_epoch，Core 校验已认证 Worker 与分配关系，不能仅信任 worker_id。复用第 4.5 节的工作负载认证原则，Worker 凭据仅允许其获授操作，不拥有 BFF 用户身份签发权限。协议固定 UTC 时间格式、ID/64 位代数的跨语言编码、枚举、空值语义、错误码、重试条件、请求体上限和幂等保留期。首次连接校验版本兼容，不兼容拒绝领取；必需字段/能力不可忽略。客户端与服务端使用同一套契约用例。

Core 使用数据库时间判断租约；Worker 根据续租请求发送时刻及返回有效时长，以单调时钟计算保守截止时间并扣除安全余量，不能把迟到响应当作重新获得完整租期。停止新调用与终止执行期限在协议中固定；过期响应不复活已停止 Attempt。Core 重启/断网期间不得无限离线执行，重连后查询提交事实并重新校验执行权限。

Java 升级前须通过协议、事务语义与故障测试。默认停止新领取、排空或安全挂起活动 Attempt、撤销旧 Core 写入权限后切换；数据库采用兼容的扩展/收缩迁移。需并行运行时必须有不重叠的调度归属和强制写入隔离，不能仅靠路由约定让两个实现各自推进同一 Run。HTTP 兼容不自动等于数据库语义兼容；Worker 无需因控制面语言改变而重写。

<a id="s05"></a>
## 5. Session、Workspace 生命周期与 Memory

### 5.1 Workspace 是可选能力

```text
user_id
 ├── standalone Session → Run → Attempt
 └── Workspace
      ├── 项目文件 / Workspace Memory / Agent 配置
      └── workspace Session → Run → Attempt
```

`sessions.workspace_id` 可空，`session_scope = standalone | workspace`。不存在 Workspace 时不自动创建隐藏的用户工作区。Standalone 支持多轮对话、附件、产物与长任务，不默认拥有 Workspace Memory 或跨 Session 共享文件。

普通对话保存 Conversation、明确保留的附件/Artifact 和必要恢复状态；临时工作副本任务后销毁。需要恢复长任务时，仍必须保存后续步骤所依赖的中间文件和检查点，不能因为没有 Workspace 就假设运行中间状态无需持久化。

Workspace 模式在同工作区内共享批准的文件与项目记忆，Session 历史分别保存。可选 Promote 操作在无活动写入时将选定 Session/文件显式迁入工作区；固定迁移前后的作用域和引用记录，不按模型参数自动迁移。

### 5.2 执行上下文与写入范围

```text
ExecutionContext
  user_id
  tenant_id                   # 必填隔离域
  owner_user_id / actor_user_id
  session_scope
  session_id
  workspace_id                 # standalone 时为空
  execution_scope_type         # workspace 或 session
  execution_scope_id           # 非空：workspace_id 或 standalone session_id
  run_id / attempt_id
  expected_scope_revision
  allowed_file_versions        # FileRef 集合
  agent_profile_version / execution_policy_ref
```

由可信 API/Supervisor 固定，模型不能选择任意 owner、工作区或文件版本。`agent_profile_id` 是配置选择，不是租户边界。[S03]

Workspace 模式按工作区单写者；Standalone 模式按 Session 单写者，不能因为没有 workspace_id 就丢失会话并发控制。Run 等待输入后释放计算槽，但保留其逻辑 execution_scope 占用；这个占用与活跃 Attempt 租约不同。等待超时和取消策略在上线前冻结。

所有数据库关联、文件读写、Memory 检索、缓存、事件、浏览器 profile 与 MCP 状态都携带作用域。同用户不同工作区也不默认共享。RLS 可作为额外防线，但须避免可绕过的数据库角色。[S19]

### 5.3 创建、Reset、Delete 与执行清理

持久生命周期为 `CREATING → ACTIVE → DELETING → DELETED`；BUSY 从 Run 与逻辑占用派生，Reset 使用独立业务 operation。

| 操作 | Harness Core 的职责 | File Service 的职责 |
|---|---|---|
| 创建 Workspace | 创建元数据、初始 Revision 与清单引用，不创建 Pod/Slot | 保存初始化内容及其引用；无需运行 Agent |
| 上传并导入文件 | 上传完成后将确定的 FileRef 纳入新 Revision | 接收字节、校验、创建不可变版本；不自行推进 Head |
| Reset | 获取写入维护权，创建继承保留项的新 Revision | 保留新版本内容；按策略释放不再保留的旧引用 |
| 删除文件 | 创建不再包含该文件的新 Revision | 不删除仍被历史版本、Run、产物引用的内容 |
| 删除 Session | 删除对话的业务可见性与关联；默认不删除已经提交的 Workspace Memory | 按保留规则释放相关文件引用 |
| 删除 Workspace | 进入 DELETING，禁止新任务，安全处理活动 Run，解除业务引用 | 按引用与保留期执行可重试 GC，回报状态 |
| Execution Cleanup | Supervisor 销毁本次沙箱、进程与本地临时资源 | 不等于删除 Workspace 或清除持久文件 |

活动写入或 WAITING_INPUT 的逻辑占用存在时，Reset 默认返回冲突；“停止并清理”先登记维护 operation，阻止新领取，取消/结束有效 Attempt，再在版本条件下发布 Reset。不得在取消与 Reset 之间留下新任务抢占窗口。

Reset 清除当前 Head 的选定内容，不等于立刻擦除历史快照；删除 Session 也不代表已经清除由它提炼的记忆。界面必须区分逻辑删除、历史保留与物理清除，不承诺即刻不可恢复。DELETED 与物理回收完成分别通过 `purge_status` 等字段表达。

Standalone Session 的删除同样阻止新任务、处理活动 Attempt、释放文件/检查点引用；不能仅删除一条 Session 行。

### 5.4 Workspace Memory 的一致性

Memory 属于 Workspace 持久状态，不属于 Worker、Slot、Pod 或长期存活的 Agent。首期保留 QwenPaw 原生可导出 Memory 格式，随工作区检查点保存；不必先建设全局 Memory Service。

明确区分 Conversation（某 Session 历史）、Workspace Memory（项目长期知识）和 Runtime Context（Attempt 临时上下文）。默认无跨工作区 User Memory。

运行期间 Memory Write 进入 Attempt 私有状态或 Delta；安全边界上与文件及 Conversation 一起提交。恢复必须加载某个 Revision 固定的 `file_revision + conversation_revision + memory_revision`，不能各自选择“最新”。

Memory Search 通过可信上下文固定范围，Agent 调用 `memory.search(query)` 不传任意 workspace_id。建议条目保存来源 Session/Run、创建者、revision 等 provenance；自动提炼的内容不是无条件可信事实，也不作为系统权限策略。

Memory 的语义、筛选和 revision 归 Harness/Runner；File Service 仅持有其导出内容的不可变 FileRef，不解释或改变记忆。以后引入向量数据库时，原始记忆是事实源，索引是可重建派生状态；应绑定作用域及记忆版本，仅查询匹配的已提交索引，落后时回退或明确等待，不能读取未来/其他工作区的记忆。

首期后端必须实现 `probe/quiesce/export_snapshot/validate_snapshot/restore_snapshot/close`，具体输入、静止令牌、失败处理与索引恢复见 [Memory 契约](QwenPaw_Multiuser_Runner_Lifecycle_v1.md#6-memory-后端快照契约)。停止后台写入或一致导出失败时不发布新 checkpoint。每个 Workspace revision 保留 `session_id → conversation_revision/FileRef` 映射，只替换当前 Session 条目，避免其他会话历史被覆盖；Memory 与文件在同一 Core 提交中发布。Standalone 使用显式空 Memory 状态，不创建隐式用户记忆。

性能顺序：先复用未变化的 Memory FileRef、分离源数据与可重建索引、只上传变化内容；不要求每工具全量快照。后续可选 base+delta 须包括删除、顺序、格式和基线引用，限定链长/体积并定期合并；不直接复制活动 SQLite WAL。是否独立 Memory Service 由导出/上传/恢复实测预算决定，详见 [快照体积与增量](QwenPaw_Multiuser_Runner_Lifecycle_v1.md#61-快照体积与增量演进)。

### 5.5 工作区等待体验与让渡路线

首期仍采用单写者与有限等待；展示占用 Run、原因和截止时间，允许取消后运行其他任务。后续显式 Park & Yield 将等待分支持久保留、停止沙箱、进入 PARKED 后释放逻辑占用；PAUSED 仍保留占用，两者不同。unpark 重新获取占用并比较 revision，变化则重新规划、显式合并或独立分支，不覆盖当前 Head。

不能仅因写入路径不相交就自动合并：读集、Memory、配置、删除也可能冲突；通用 Shell 无法证明读集时视为依赖整个 Workspace。首期不开放全面 Session 并发写 Workspace。后续 park/unpark 的输入竞态和版本检查见 [状态机细则](QwenPaw_Multiuser_State_Transactions_v1.md#52-park--yield后续独立能力首期关闭)。

<a id="s06"></a>
## 6. Worker、多执行槽与高并发

### 6.1 部署和并发关系

Linux 生产 P0—P3 默认每个常驻 Worker Pod 一个 Supervisor、一个 Slot，通过增加池副本扩容，不按 Run 动态创建 Pod/Job。macos-dev 用本地 Worker 进程承载相同单 Slot 语义，无 Pod 要求。可保留 max_concurrent_runs 配置，但 P4 前生产只允许 1；P4 多槽需资源、进程、文件、网络隔离与清理均实测通过。Pod 与 Worker 是不同层次的概念。[S09]

```text
Supervisor
  Slot A → Attempt A → 新任务沙箱
  Slot B → Attempt B → 新任务沙箱
  Slot C → Attempt C → 新任务沙箱
```

Supervisor 通过异步管理同时监控多个子进程；同步、CPU 密集或可能长时间阻塞的文件操作不得阻塞续租与取消通道。异步只提高管理能力，不等于增加 CPU，也不产生隔离。

### 6.2 准入与公平性

首期采用项目内 Python 调度模块与 PostgreSQL 持久任务表。Worker 有空闲槽时调用 Core claim；Core 在短事务内完成资格检查、配额预留、Scope 占用和 Attempt/租约建立，再由 Supervisor 启动沙箱。数据库事务内不运行模型、启动进程或上传文件。轮询采用有界退避；内存通知只能优化唤醒，不能充当权威队列。故障扫描、续租、取消和恢复继续遵循现有 Run/Attempt 协议，不把第三方框架选型或 PoC 作为开工前置。此决定仅限定平台任务分发，不替换 QwenPaw 已有 AgentScope Runtime；以后若评估框架，须明确替换职责与状态归属，避免叠加两套权威调度状态。

只有同时满足空闲槽、内存/CPU/临时磁盘预算、模型并发预算、工作区单写者、用户额度和版本兼容性，Worker 才领取任务。不预取大量任务后在本地长期排队。

队列与额度由应用层管理；按用户公平轮转或等价策略避免单用户占满执行池。设置最大排队量、文件大小、执行预算、超时及恢复次数。模型限流、下游 MCP 容量也是准入条件，不只看 CPU。

P2 采用两级 tenant/user quota 与加权虚拟完成时间轮转：先选择可调度用户，再选其最早可运行 Run；领取事务原子预留两级额度并推进调度位置。首期统一 cost=1，只保证启动机会的近似公平；后续再按 profile 成本校正，不声称资源公平。窗口函数计数或 SKIP LOCKED 本身不能防止超额或保证公平。恢复参加同一队列，等待释放执行额度但保留等待额度，Token/上传在实际消费前预留。算法字段、锁序和结算见 [公平调度](QwenPaw_Multiuser_State_Transactions_v1.md#11-公平调度与配额原子预留p2)。

### 6.3 不同并发指标必须分开

| 指标 | 占用执行槽吗 |
|---|---|
| 在线网页数、历史查询数 | 否 |
| SSE 订阅数 | 订阅本身不占槽 |
| 已提交但排队的 Run 数 | 否 |
| 正在运行或准备运行的 Attempt 数 | 是 |
| 已提交状态并暂停等待输入的 Run 数 | 可以不占槽 |

均匀配置时，配置并发上限约为 `Pod 数 × 每 Pod 槽数`；真实可用并发取决于资源、任务类型与下游能力。吞吐量还取决于占槽时长。Agent 等待模型但进程和浏览器仍驻留时，不能把槽当作已经释放。

可按轻量文本、通用工具、浏览器/重计算拆执行池。单 Pod 以 1、2、4、8 等档位逐步测试只是测试策略，不是未经压测的推荐生产数值。

### 6.4 多槽的资源与故障边界

容器总资源限制不会自动平均分给每个子进程。每 Attempt 的内存、CPU、进程数、输出和临时磁盘限制需要实际执行后端支持；一个任务 OOM 不能任意拖垮同 Pod 的其他任务。[S11][S14]

如果所在环境不能提供所需的独立限制，限制单 Pod 槽数并横向扩展，不以增加 privileged 或暴露容器引擎 socket 作为快捷修复。一个多槽 Pod 故障会影响多个 Attempt，需要用持久恢复而不是扩大退出等待时间掩盖风险。

P4 准入须实测用户命名空间/安全策略、cgroup v2 委派或等价资源限制、网络分域、OOM 故障范围及任务清理。Bubblewrap 不必然需要宿主 CAP_SYS_ADMIN，但嵌套运行受目标集群策略约束；给 Pod 配置 gVisor/Kata 也不自动拆出每 Slot 的强边界。单槽只是缩小故障域，不是物理隔离，仍需 Runtime/Supervisor 边界和跨 Attempt 清理。

<a id="s07"></a>
## 7. 执行边界、可选强沙箱与生命周期

> **P1 范围覆盖：** [Kylin V10 可信任务执行边界](P1_Trusted_Kylin_Execution_Boundary.md) 已将当前 P1 收敛为单 Pod/单 Worker/单 Slot、Pod 资源与网络约束、每 Attempt 新进程和私有目录、清理失败停用 Worker。下文 7.1—7.4 的任务级强沙箱用于未来不可信用户/任意代码阶段，不是当前 P1 门槛；7.5 的生命周期在 P1 中由 ExecutionBoundary 负责落实。

### 7.1 任务级强沙箱：未来不可信用户边界

在 Supervisor 准备好当前工作区后、QwenPaw 首次初始化前创建。边界覆盖整个 Runtime 及其启动的本地 MCP、浏览器、Shell、Python 等执行进程。

```text
可信 Supervisor（平台能力不进入任务）
   │
   └── Attempt 专属任务沙箱
         ├── QwenPaw Runtime / Agent
         ├── 当前工作区文件与状态
         └── 工具执行子环境
```

任务不能看到其他槽的工作区、Supervisor 配置或全局凭据。外层任务策略由服务端固定，不接受 Agent、工作区配置文件或工具审批模式将其关闭。

### 7.2 工具级权限收缩

任务沙箱内的 Runtime 可以维护当前会话状态，但执行用户代码的 Shell/Python 通常只需项目文件和临时目录。高风险工具应再进入更窄的沙箱或受限工具执行服务：默认不暴露 Runtime 内部 `/state`，不继承 Runtime 的网络凭据和控制通道。

如果工具函数直接在 Runtime 进程内执行，它继承的是 Runtime 访问能力；一个 Python 包装器或路径检查不等于独立系统边界。必须逐一盘点文件工具、Shell、REPL、本地 MCP、浏览器和自定义 Skills，标记为“进程内受控代码”“受限子进程”或“远程服务”。未纳管的高风险路径在首期禁用。

远程 MCP 对远端资源的访问由服务范围与工具代理约束；本地文件沙箱不能自动约束远程服务的副作用。

### 7.3 外层强隔离与多槽不是同一件事

Bubblewrap/命名空间、Landlock/AppArmor、seccomp、cgroup 可以组成执行限制；对任意代码和不互信用户，应评估 gVisor、Kata 或 MicroVM 等更强后端。[S12][S13][S14][S15][S16]

**此处尚未选定生产沙箱后端。** 给整个 Worker Pod 配置 gVisor，不代表每个 Slot 自动成为不同的 gVisor 沙箱；多客户是否处于同一沙箱、哪些文件被映射、哪些进程可见仍要单独验证。若威胁模型要求每任务独立的强沙箱，执行后端必须真的提供这种粒度；不能只画多槽结构而没有相应隔离能力。[S16]

不要求业务服务动态创建 K8s Job。沙箱后端的基础设施配置由执行平台实现；不满足多槽隔离能力时可降低密度。**同时满足“固定 Worker 池、多槽、高强度隔离”是需要 PoC 的工程条件，不是已经证实的部署组合。**

### 7.4 SandboxManager 的未来契约

| 操作 | 要求 |
|---|---|
| `probe_capabilities` | 检测文件、进程、网络、资源控制能力和后端版本 |
| `validate_policy` | 声明约束能否真正执行；不能执行的必需项直接拒绝 |
| `create` | 创建 Attempt 独占文件视图、临时层、进程和网络环境 |
| `launch` | 在边界内启动新 Runner；不继承无关 FD/环境 |
| `restrict_tool` | 为选定工具施加更窄的文件/网络/资源权限 |
| `request_stop` | 停止新工具操作，尝试在安全边界结束 |
| `terminate` | 强制终止本次沙箱全部进程与子进程 |
| `destroy` | 卸载、删除临时层、撤销临时访问能力，确认可复用 |

这些是职责接口，不是已验证的 QwenPaw SDK。创建、停止、销毁操作必须可重复调用；不能在清理超时后未经检查就把槽标为空闲。

### 7.5 生命周期

```text
空闲槽
  → 准入与领取
  → 工作区物化
  → 创建并验证沙箱
  → 启动 Runner
  → 执行 / 保存 / 等待输入 / 终止
  → 收拢后台进程与写入
  → 提交状态和产物
  → 销毁沙箱与临时能力
  → 清理确认后槽回到空闲
```

沙箱通常与 Attempt 对齐。暂停后恢复或 Worker 故障后接管，创建新 Attempt 和新沙箱。工作区与会话持续存在，沙箱不必持续存在。

<a id="s08"></a>
## 8. 文件、进程、凭据与资源隔离

### 8.1 建议的沙箱内文件视图

“不能操作工作区之外”应实现为：**持久业务写入仅在当前工作区及其专属状态目录；运行依赖只读；临时空间独占；其他数据与平台能力不可访问。** Runtime 不可能在完全不读取运行库的条件下工作。

| 沙箱内路径/资源 | Runtime | 工具子环境 | 持久化策略 |
|---|---|---|---|
| `/workspace` | 读写 | 按工具允许读写 | 工作区快照 |
| `/state/qwenpaw` | 维护当前会话和记忆 | 默认不可见 | 一致性状态导出 |
| `/inputs` | 只读 | 只读 | 原始附件单独保存 |
| `/opt/runtime`、批准的公共 Skills | 只读 | 按需只读 | 固定镜像/版本 |
| 独立 HOME、`/tmp`、`/dev/shm` | 本次独占 | 每工具按需进一步隔离 | 默认丢弃 |
| 其他工作区与槽目录 | 不可见 | 不可见 | 不挂载 |
| Supervisor 配置、平台密钥、管理 socket | 不可见 | 不可见 | 不提供 |

采用固定逻辑路径便于跨 Worker 恢复。底层本地目录可为 `/executions/<attempt_id>/...`，但不把 `/executions` 父目录暴露进沙箱。各沙箱可都使用 `/workspace`，实际映射不同目录。

仅设只读根文件系统不等于全部数据都保密；可读内容同样要最小化。系统目录从干净运行镜像或筛选模板提供，不整包暴露 Supervisor 的 `/etc`、`/run`、`/sys` 或凭据挂载。

### 8.2 工具与导入导出的路径安全

所有路径访问绑定当前工作区，不使用 `startswith('/workspace')` 作为安全判断。需处理绝对路径、`..`、符号链接、硬链接、特殊文件、命名管道及检查后替换的竞态。

Linux 可采用以工作区目录 FD 为根的访问方式，评估 `openat2()` 的 `RESOLVE_BENEATH`、`RESOLVE_NO_MAGICLINKS`，并按产品需要禁用符号链接；这些属于平台相关实现，需要实际兼容性验证。[S17]

**可信产物收集器也是边界。** Agent 可以制造链接或在打包时修改文件；Supervisor 不能跟随不可信链接读取自身文件系统。应在停止写入后安全遍历受限根目录，校验文件类型、大小、数量和校验和。只接受已批准的工作区内容，不能接受 Runner 任意提出的宿主路径。

上传归档在无平台凭据的暂存环境解压，限制路径和链接、文件数量、总解压体积；通过校验后再导入。使用库的安全过滤选项仍需额外约束，不能直接解压到可信配置或活动工作区中。[S18]

### 8.3 进程与继承资源

Supervisor 用新的 `exec/spawn` 进程启动 Runner，避免 fork 后继续使用已导入 QwenPaw 的解释器状态。进程树、PID 可见性、信号、调试/ptrace、IPC 与本地 socket 的访问需在执行后端约束。

构造最小环境变量集合；关闭无关继承 FD；只传递本任务允许的 IPC。Landlock 等限制不会自动收回施加限制前已经打开的文件能力，因此要在启动和 FD 传递层显式处理。[S13]

进程组停止只是一个清理手段，不保证能捕获所有脱离进程组的子进程。清理应以实际沙箱/资源组为单位，覆盖浏览器、后台 Shell、REPL 和本地 MCP；无法确认无残留时停用槽。

### 8.4 凭据与策略

默认不向 Runtime 暴露 PostgreSQL 连接串、完整 Bucket 密钥、Worker 领取接口凭据、云管理凭据、K8s token 或容器引擎 socket。模型和工具尽量经代理；确需直接访问时只提供本任务范围、限时且可撤销的能力，不能跨用户复用。

沙箱 policy 来自服务端只读配置，不从可写工作区加载并允许覆盖。用户快照可以包含用户数据与任务配置，但不能覆盖强制执行策略、平台端点或信任根。

一般快照和产物不保存凭据。确需跨任务保留浏览器登录状态时，作为明确启用的私密状态单独处理，加密、限制范围和保留周期；该能力不是首期默认。

### 8.5 资源控制与复用

每 Attempt 控制 CPU、内存、进程数、执行时长、文件规模、临时盘、日志与输出体积。cgroup 相关能力需要执行环境配置，不由业务容器假定拥有任意创建/修改权限。[S14]

只读镜像和批准公共依赖可复用；可写 HOME、包安装目录、浏览器 profile、运行缓存和 IPC 必须独占。允许安装依赖时只能写本次环境，不得修改后续用户共享的 Python/Node 运行库。

私有 CAS 是后续优化：缓存绑定 tenant/scope/digest，每次命中仍校验 FileRef 授权与摘要；可信组件写缓存、任务取得私有副本。使用 reflink/COW 或复制，禁止把共享缓存 inode Hardlink 到可写工作区。首期只共享公共只读基础内容；缓存损坏可重取，不能替代持久引用或跨用户去重授权。

任务结束销毁可写层，而不是只清理几个已知文件。失败清理应进入 `QUARANTINED` 或等价的槽状态，交由执行平台回收，不接下一用户。

<a id="s09"></a>
## 9. Runtime 受控联网设计

### 9.1 原则：沙箱不是全面断网

文件、进程和网络限制可以分别配置。Runtime 在沙箱内仍需调用模型、Embedding、批准的远程 MCP，并使用事件/取消/检查点通道。

创建独立网络命名空间本身不会提供可用外连。仅开启 Bubblewrap `--unshare-net` 可能使原本可用的外部服务不可达；执行后端还需建立允许通道并验证 DNS、TLS、流式连接和控制通信。[S12]

不得在“全部网络开放”和“全部断网”之间二选一；目标是**必要通信可用、非必要通信不可达**。

### 9.2 默认网络访问矩阵

| 发起方 | 目标 | 默认策略 | 备注 |
|---|---|---|---|
| Runtime | 指定模型/Embedding 服务或模型网关 | 允许 | 限定服务、操作、模型与预算 |
| Runtime | 批准的远程 MCP/工具网关 | 允许 | 不接受任意 MCP 地址 |
| Runtime | 本任务控制与事件通道 | 允许 | 不能使用 Worker 管理/领取能力 |
| Runtime | PostgreSQL、全局对象存储管理接口 | 拒绝 | 由 Supervisor/Broker 代理 |
| 离线 Python/文件工具 | 外部网络 | 默认拒绝 | 需要时使用独立网络 profile |
| 浏览器/搜索工具 | 受控公网出口 | 允许 | 限定用途并阻止访问内部管理面 |
| 任意任务进程 | 云元数据、其他 Worker、非必要内网、平台管理接口 | 拒绝 | 内网模型服务按确切目标例外允许 |
| 本地 stdio MCP | 通过 stdio 与 Runtime 通信 | 允许 | MCP 自身的外部访问另行约束 |

MCP 的 stdio 传输本身不依赖 TCP；远程 HTTP 传输需要相应网络能力。但 stdio 工具内部若调用外部 API，仍然需要受控出站规则。[S21]

### 9.3 Runtime 与工具不能仅靠配置约定区分权限

如果 Runtime 和 Shell 共享同一网络命名空间，Shell 通常能尝试相同网络连接；把两个不同的布尔值写入配置不会自动形成边界。工具级网络 profile 必须由独立子沙箱、强制策略或受限工具执行服务落实。

设置 `HTTP_PROXY`/`HTTPS_PROXY` 只是一种客户端配置，不是唯一边界。必须测试关闭代理、直接 IP、不同端口和替代客户端后是否仍能绕过限制。网关不能成为访问任意内部 URL 的开放代理；由服务端解析允许的服务标识，限制目的地址与操作。

对浏览器联网，出口策略应处理重定向、解析变化、IPv4/IPv6、内网和 link-local 地址，避免只检查最初 URL。具体域名、端口和网络后端在 P0 确定。

### 9.4 同 Pod 多槽下的 localhost、DNS 与策略粒度

同 Pod 容器默认共享网络命名空间；标准 NetworkPolicy 以 Pod 为选择与实施粒度，不能直接给同 Pod 的不同 Slot 分别赋权。因此，多槽隔离不能只依赖 Pod NetworkPolicy。[S09][S10]

创建任务自己的网络环境后，`127.0.0.1` 指向该环境，不再自动到达外面的 Supervisor 或 sidecar。原来通过 localhost 的模型代理、Console PoC、MCP 和控制接口必须重新配置可达通道。

可以采用被明确授权的本地 IPC 或执行后端提供的受限端点，但不能把含全局管理能力的 socket 挂进去。被传入的 FD 本身是一项能力，需要控制消息范围。

默认拒绝外连时，允许指定 DNS 解析通道或由代理负责解析；不要为解决 DNS 问题重新开放所有出站流量。[S10]

### 9.5 模型网关和控制通道的要求

模型网关需兼容实际使用的流式响应、长连接、超时、取消、错误码和工具调用格式；不缓冲到模型回答结束才返回。限制请求体、并发和预算，日志不记录密钥及不必要的敏感内容。

Supervisor 的任务领取和租约接口不应直接暴露给 Runtime。Runtime 只得到本 Attempt 的事件、控制和快照协商能力。Supervisor 持续续租与 Agent 是否产生 Token 解耦，避免长工具调用被误判失联。

### 9.6 网络启动检查

必须同时通过正向与负向测试：模型/Embedding/批准 MCP 可用；SSE/事件/取消可用；不允许的内网、管理接口、其他任务和直连绕过被阻止。策略或后端不可用时失败关闭，不能自动改为全网络直通。

<a id="s10"></a>
## 10. 文件服务、云存储与工作区一致性

### 10.1 两个必须分开的边界

**文件服务边界**面向内容保存、不可变版本、传输与引用保留，当前属于 Harness 内置模块、将来可归 BFF。

**执行文件系统边界**面向沙箱的本地目录、路径检查、工具读写和进程权限，始终位于 Worker/SandboxManager。文件 API 迁移不移动沙箱，也不把每次 `open()` 转成远程 HTTP。

| 层 | 所属模块 | 保存内容 |
|---|---|---|
| Harness PostgreSQL 逻辑 schema | Harness Core | Session、Workspace、Run/Attempt、租约、事件、checkpoint 与 FileRef 关联 |
| 文件元数据 schema | File Service | file_id/version、upload、存储映射、大小摘要、引用集合、GC 状态 |
| 对象存储 | 仅由 File Service 的 BlobStorePort 访问 | 文件内容、状态导出、不可变 Manifest、产物 |
| Worker 本地磁盘 | Supervisor/任务沙箱 | Attempt 独占的可写副本、缓存、临时数据，不是持久事实源 |

两个 schema 可以先位于同一 PostgreSQL 实例，但不跨模块联表、共享 ORM 对象、外键级联或跨模块写事务。业务侧仅持有不透明 FileRef；物理 location、bucket/key、Provider SDK 和全局凭据不进入 Core、Run JSON 或 Manifest；首期 Worker 同样不持有。后续受限直传仅允许 FileApiClient 短暂使用 opaque transfer grant，不持久记录 locator 或签名。

### 10.2 FileRef 与访问方式

```json
{
  "file_id": "file_001",
  "version": "fv_01"
}
```

`file_id + version` 一经 READY 即绑定不可变内容；文件元数据另含大小、类型、摘要和用途。名称不是路径，不使用临时下载 URL、ETag 或用户文件名充当业务版本标识。

从首期采用内容流代理：`Worker → Harness File API → 底层存储`。以后为 `Worker → BFF File API → 底层存储`。HTTP 下载直接返回内容，不返回存储 URL 或重定向；上传也经 File API，不提供让 Worker 直连存储的分片签名地址。

受限直传是后续显式契约扩展，默认关闭：File API 签发绑定 Scope/Attempt、单对象、动作、期限及完整性条件的传输授权，统一客户端执行传输；不是文件服务故障时绕过接口的回退。具体不可变发布和凭据限制见第 24.6 节。[S27]

### 10.3 本地执行与物化

Supervisor 先从 Core 获得已提交清单 FileRef 与版本，再通过 FileApiClient 获取内容，校验范围、大小和摘要，在受限暂存目录恢复。恢复归档及路径始终按不可信数据处理。文件 API 不把客户端提交的任意 URL/绝对路径透传给高权限代码。

Workspace 模式恢复工作区文件、相关 Session 与 Memory。Standalone 模式仅恢复该 Session 及本 Run 的必要状态和输入；用于下一检查点的中间结果仍要保存。物化后映射到独立任务沙箱；不将远程存储伪装成完整 POSIX 主工作盘。[S25]

本地 `emptyDir` 等只用于工作副本；恢复不依赖原 Pod 存活。[S23] 远程 Memory/向量库需另有版本化一致性协议，不能只复制本地目录就宣称完成恢复。

### 10.4 Manifest 示例（拟定 schema）

```json
{
  "schema_version": 2,
  "checkpoint_id": "cp_018",
  "parent_checkpoint_id": "cp_017",
  "tenant_id": "tenant_01",
  "user_id": "u_01",
  "owner_user_id": "u_01",
  "actor_user_id": "u_01",
  "session_scope": "workspace",
  "workspace_id": "ws_01",
  "session_id": "session_01",
  "run_id": "run_01",
  "attempt_id": "attempt_02",
  "execution_scope": {"type": "workspace", "id": "ws_01"},
  "base_revision": 17,
  "state_refs": {
    "conversation": {"file_id": "f_conv", "version": "fv_27"},
    "memory": {"file_id": "f_mem", "version": "fv_13"},
    "files_manifest": {"file_id": "f_files", "version": "fv_18"}
  },
  "execution_state": {
    "completed_steps": ["collect", "extract"],
    "next_step": "analyze",
    "pending_operation_ids": []
  },
  "version_refs": {
    "qwenpaw": "2.2.0",
    "runner_image_digest": "REQUIRED_REAL_DIGEST",
    "agent_profile": "research_agent_v1",
    "skills_manifest": {"file_id": "f_skills", "version": "fv_01"}
  }
}
```

Standalone 时 `workspace_id=null`、`execution_scope.type=session`、id=session_id、memory 默认为空，base_revision 指 Session 状态版本。清单不能携带 OSS key 或自动执行的反序列化代码。示例字段属于新增协议，实际适配后冻结，生产拒绝占位镜像值。

### 10.5 提交协议：内容保存、引用保留、业务发布

1. Core 登记带稳定 `operation_id/checkpoint_id` 的提交意图；当前 Attempt、scope revision 和生命周期必须有效。
2. 到达安全边界，停止新工具调用，收拢后台写入，一致导出文件、Conversation、Memory；本地数据库使用一致性备份/导出，不随意复制活动 WAL。
3. Supervisor 通过 File API 上传不可变内容及 Manifest；完成校验后取得 READY FileRef。上传临时对象受未完成上传会话保护。
4. Core/受限协调器通过 FileGateway 为完整引用闭包建立持久 `reference_set`；包含 Manifest 自身以及它递归依赖的内容，不能只保留最外层 JSON。文件服务不推断 Agent 的清单语义，调用方显式列出引用集合。
5. 文件服务确认集合持久保留后，Core 在自己的短事务中校验 `attempt_id + lease_epoch + lease_until + expected_scope_revision + scope lifecycle`，原子更新 Head/Session 状态、checkpoint、逻辑步骤和事件。
6. 发布成功才视为可恢复；响应丢失时按同一 commit_id 查询或幂等重试。产物只有内容 READY、引用保留且业务发布成功才对外可见。

这里没有 Core 与 File Service 的联合事务。第 4 步成功、第 5 步失败时，内容暂时多保留而不丢失；后台对账在确认 operation 已 ABORTED 且无任何已提交使用关系后释放。不能仅凭暂时查不到 Head 或时间到期就回收可能正在提交的集合。

释放流程由 Core 在自身事务中写入 outbox，异步幂等通知 File Service；文件服务无需回调 Run API 才能执行每次读写。即使两模块首期共库，仍按这一协议实现，避免拆分时重写一致性逻辑。

此核心链路从 **P2** 实施，覆盖 Run 输入接受、基础 Workspace revision 与产物发布；P3 在同一链路上增加一致恢复快照。提交状态统一为 `PREPARING → PINNED → COMMITTED` 或 `PREPARING/PINNED → ABORTED`，与 outbox 的 PENDING 分开。锁序、迟到 pin、释放墓碑和补偿条件见 [文件提交事务](QwenPaw_Multiuser_State_Transactions_v1.md#3-文件提交统一协议p2-必须实现)。

### 10.6 生命周期、文件类型与 GC

文件模块区分原始上传、输出产物、工作区内容、Conversation/Memory 导出、Manifest 和临时内容；不是所有已存文件都允许在网页下载。Runtime/工具不能获得内部状态的任意下载入口。

原始文件上传只是保存字节，不等于文档解析、向量化或写入 Memory。解析在受限工具/任务中进行，派生结果独立引用原件。

Workspace Reset/Delete、文件移除和 Session 删除由 Core 决定业务语义并释放引用；File Service 执行物理回收，不直接改 Workspace Head。内容寻址优化首期限定业务范围，不做跨用户去重；即使同一文件被多个 revision 引用，也只在全部引用释放、无在途上传且保留条件满足后 GC。

不可变临时上传可到期回收；已提交 reference-set 不能仅因短期下载能力到期而失效。文件服务不可用时，任务暂缓物化或提交并报告可重试错误，不绕过接口直连存储，也不把未持久结果标记为成功。

<a id="s11"></a>
## 11. QwenPaw Runner 的执行适配

### 11.1 新进程、先配置、后导入

QwenPaw 的 `constant.py` 在模块加载时解析工作目录、私密目录并加载 `.env`。因此，由 Supervisor 构建最小环境和固定逻辑路径，在沙箱内通过新进程首次导入 QwenPaw；不能在同一已初始化解释器中切换 `user_id` 或修改环境变量后复用全局对象。[S01]

建议映射 `QWENPAW_WORKING_DIR` 到当前任务私有状态根，项目工具根固定为 `/workspace`；具体目录关系、Agent 配置与项目根的接入点要在 P0 核对。`QWENPAW_SECRET_DIR` 不得指向 Supervisor 的凭据目录，运行时只获得确有必要且受限的本任务能力。

执行镜像不得包含真实开发环境 `.env` 或平台密钥。避免从可写工作区恢复能够覆盖强制 policy 的配置；系统策略与用户数据分开。

### 11.2 Runner 必备职责

| 能力 | 要求 |
|---|---|
| 初始化 | 加载固定版本 Agent/Skills/模型配置，建立当前工作区与会话 |
| 执行 | 运行一次请求或有界逻辑片段，不依赖网页连接 |
| 事件 | 输出结构化、可去重的本任务事件；日志独立 |
| 工具 | 注入稳定 operation_id、调用范围、预算和取消信号 |
| 状态导出 | 在实际安全边界导出会话、记忆、计划和文件引用 |
| 恢复 | 从已提交检查点重建逻辑状态，验证版本兼容性 |
| 结束 | 收拢子任务、关闭本地 MCP/浏览器/工具，确认停止写入 |

原生 `TaskTracker` 的进程内状态可用于局部执行管理，但不能成为云端 Run 或事件的事实源。不要把它直接改造成整个分布式队列；使用新增的 EventSink 和任务适配。[S02]

### 11.3 先验证原生入口，再收敛无界面入口

PoC 可由私有 QwenPaw 进程暴露原生 `/api/console/chat`，内部适配请求和 SSE。该接口是原生能力，但对外仍只提供本项目 Run API。[S03]

PoC 也必须处于隔离边界内，且有 `external_run_id` 的去重/查询映射。网络超时后先确认执行是否存在，不盲目再次提交原生聊天请求。启用独立网络后要显式处理 localhost 路由。

生产建议使用新增的 headless Runner，准确初始化和关闭 Runtime；不是声称上游已有 `qwenpaw worker` 命令。原生 Console、桌面、自动安装/更新、Cron、Heartbeat 和其他渠道入口默认不启动；保留哪些服务由实际依赖验证决定。

初始化、恢复、执行、静止、导出、提交和关闭的输入/输出及失败处理，以 [Runner 生命周期契约](QwenPaw_Multiuser_Runner_Lifecycle_v1.md) 为实施要求。Standalone 的业务 Workspace 可空，但其 Attempt 仍建立私有本地 Runtime Workspace 以接入会话生命周期；不能将业务可空直接映射为跳过 Session Hook 的 `workspace=None`。

<a id="s12"></a>
## 12. 应用层任务领取、执行归属与状态机

### 12.1 PostgreSQL 持久队列

首期 Run 与调度事实可使用同一数据库事务，避免“任务入库成功、队列发送失败”的双写窗口。Worker 主动调用内部 claim 接口，不从 K8s 查询待运行任务。

多个 Worker 领取可以使用短事务行锁和 `SKIP LOCKED`；它适合队列式消费者访问，但不是全局一致快照查询方式。锁必须同时覆盖 execution_scope 占用和候选 Run；Workspace 按工作区、Standalone 按 Session，不能因 workspace_id 为空就跳过并发控制。[S20]

```text
检查并预留本地执行槽
  → 请求领取
  → 服务端检查用户额度、execution_scope 占用与版本能力
  → 分配 Attempt，递增执行代数
  → 持久化租约、更新 Run 状态
  → 提交短事务
  → Worker 开始物化与沙箱启动
```

并发事务采用一致的锁顺序并处理冲突，不保持一个数据库事务直到数小时任务结束。注册的 Worker ID 标识进程实例，不复用 Pod 名称当永久执行者身份；重启必须生成新的实例身份。

统一锁序为 `tenant quota → user scheduler/quota → scope → run → attempt → operation/prompt/commit → event counter`。新增统一 `execution_scopes` 调度行作为占用事实源；原 Workspace/Session 的 active_run_id 如保留则同事务维护。新 Run 默认首次领取时绑定最新 revision，显式 expected_revision 不匹配则失败；恢复固定到自身已提交 checkpoint。详见 [版本选择与竞争表](QwenPaw_Multiuser_State_Transactions_v1.md)。

### 12.2 Run 状态

以下是建议的逻辑状态集合；具体枚举可在协议冻结时调整，但语义不能省略。

```text
QUEUED → STARTING → RUNNING → SUCCEEDED
                       │
                       ├── WAITING_INPUT → QUEUED（同一 Run，新 Attempt）
                       ├── PAUSED → QUEUED（显式恢复，同一 Run，新 Attempt）
                       ├── RECOVERING → QUEUED（允许恢复时）
                       ├── WAITING_RECONCILIATION（外部结果不明）
                       ├── FAILED
                       ├── CANCELLED
                       └── TIMED_OUT
```

`cancel_requested_at`、`pause_requested`、`draining` 是命令/控制意图，不等于执行已经结束。终态只有在状态提交或明确的故障处理后产生。终态写入幂等，不允许旧 Attempt 或迟到结果把 CANCELLED 覆盖为 SUCCEEDED。

Attempt 可以记录 `ASSIGNED / STARTING / RUNNING / COMPLETED / LOST / STOPPED / FAILED` 等观察状态；Run 与 Attempt 不是同一状态机。Run 恢复不创建新的业务任务，但会创建新的 Attempt。

### 12.3 租约与代数

租约包含 `worker_id, attempt_id, lease_epoch, lease_until`，以服务端数据库时间计算有效性。所有权威事件、检查点、工作区 head、工具操作开始和终态提交校验当前 Attempt 与代数。

续租与 Token 输出解耦，Supervisor 持续心跳。失去控制通道时停止发起新工具调用；本地 watchdog 应在安全期限内停止本次执行。API 恢复扫描使用条件更新或行锁，避免多个副本重复接管。

租约过期不等于旧进程一定停止。任务私有副本与版本提交可阻止旧实例发布文件，但数据库 fencing 不能阻止已发出的外部请求。恢复前检查工具账本；结果不明的非幂等操作不直接重试。

### 12.4 幂等与去重范围

- Run 创建：`tenant_id + user_id + session_id + Idempotency-Key` 唯一（tenant 与 Session 必填，不依赖可空 workspace_id 的唯一性语义）；保存规范化请求摘要，同键不同请求返回冲突。
- 事件：`run_id + producer_event_id` 唯一；服务端分配单调 `event_seq`。
- 检查点：稳定 `checkpoint_id/commit_id`，重复提交返回同一已提交结果。
- 人工输入：按 `run_id + prompt_id + input_id` 去重，只消费一次匹配的待答问题。
- 工具操作：`operation_id` 跨 Attempt 稳定，不把 Attempt ID 当副作用幂等键。

Run/Attempt/Scope/prompt/input 的精确转换、终态不可逆规则及取消/成功、续租/扫描、输入/超时的胜出条件见 [状态机与事务表](QwenPaw_Multiuser_State_Transactions_v1.md)。

<a id="s13"></a>
## 13. 长程任务、检查点与副作用恢复

### 13.1 能力分级

| 级别 | 能力 | 交付要求 |
|---|---|---|
| L1 | 网页/BFF/订阅断线，任务继续；事件可补读 | 基础任务服务必须支持 |
| L2 | Worker 故障后从已提交回合/子任务边界重建并继续 | 长程任务生产版必须支持 |
| L3 | 单次 Agent 循环内的工具边界恢复 | 对选定工具和模式验证后开放 |

原生自动 Checkpoint 在会话保存成功后的响应阶段调度，不表示每个工具执行前后都已同步保存；外层 Runtime Hook 与内层 Agent loop middleware 不同层次，逐工具边界需在正确层接入。[S04][S05]

### 13.2 恢复的对象是逻辑状态

保存目标、完成条件、已完成步骤、下一步、上下文、记忆、文件版本、待决工具操作和累计预算。恢复时重新创建 Runtime、MCP 和浏览器，重建必要连接；有外部长任务句柄时查询或续接。

不序列化活跃 asyncio Task，不把进程 PID 当可移植句柄，不把重新发送同一聊天请求当恢复协议。恢复后的模型输出也不保证逐 Token 与故障前一致；必须以前一次已提交步骤和工具结果为依据推进。

### 13.3 有界执行与预算

一个长任务可以拆成“收集资料 → 提取信息 → 分析 → 生成文件 → 校验”的片段。每片段有输入、完成条件和可提交状态，但这不要求引入独立工作流引擎。

限制总 Token、步骤、工具次数、墙钟截止时间、占槽执行时间、恢复次数和重试预算。预算跨 Attempt 累计，不能通过重试重置。定义重复工具错误、重复参数或持续无有效产物等无进展检测，触发失败或补充输入，不无限续跑。

后台子 Agent 必须纳入生命周期：主 Run 要么等待其停止并收拢写入，要么将子任务显式持久化并跟踪；首期可禁用脱离主 Run 的后台执行。

### 13.4 工具操作账本

```text
持久记录 operation_id 与请求摘要
  → 执行工具
  → 持久记录结果、外部请求编号及文件影响
  → 提交包含对应逻辑进度的检查点
```

同一 `operation_id` 不允许在恢复时换成不同参数。记录 `INTENT / IN_PROGRESS / SUCCEEDED / FAILED / UNKNOWN` 或等价状态，区分“工具失败”和“结果尚不确定”。

| 工具类型 | 恢复策略 |
|---|---|
| 只读查询 | 可按预算和重试策略重新调用 |
| 本地工作副本文件操作 | 从检查点重放可重放步骤，或恢复已保存文件增量 |
| 支持幂等键的外部 API | 复用稳定 operation_id，查回已执行结果 |
| 不支持幂等，但能查询状态 | 使用外部请求号/业务键对账后决定 |
| 不幂等、也不可查询的副作用 | 按冻结 fallback 失败或限时对账；仅低风险精确预授权可接受重复风险，原 UNKNOWN 保留 |

关键故障窗口：外部操作已经成功、结果尚未落库，Worker 消失。不能把租约接管等价为安全重试。也不能因为工具账本显示本地文件写入成功，就在恢复旧快照时跳过该工具——文件影响必须一并恢复。

执行前先持久化模型已选定的步骤、调用顺序与参数，Core 分配 logical_step_id/operation_id；请求摘要用于拒绝改参，不用于合并相同参数的有意重复调用。恢复先接回这些待决步骤和工具结果，再允许模型产生新步骤。INTENT/dispatch 响应丢失、UNKNOWN、已知结果未进 checkpoint 的处理及 L2 限制见 [可执行恢复协议](QwenPaw_Multiuser_Runner_Lifecycle_v1.md#4-可执行的恢复协议)。未实现接回的外部副作用工具仅允许在独立可提交片段运行，或首期关闭。

回退统一为 FAIL_WITH_UNKNOWN_OUTCOME、WAIT_FOR_RECONCILIATION、RETRY_WITH_DUPLICATE_RISK、SKIP_WITH_WARNING，见 [能力矩阵](QwenPaw_Multiuser_Capability_Matrix_v1.md)。失败保留未知警告；对账必须有路径与期限；接受重复风险只限平台批准的低风险具体动作、事先授权与次数预算，并创建关联 retry_of 的新操作；幂等重试复用原 ID。跳过仅限可选步骤并标记结果不完整。不允许 ASSUME_ABORTED；付款/发送消息/部署不能靠通用重试开关执行。

### 13.5 等待输入、暂停和取消

等待输入时，先原子提交 checkpoint、待答 prompt 和 Run 状态，再停止沙箱、释放槽。首期工作区仍由该逻辑 Run 占用，但不保留活跃 Worker 租约。收到匹配的输入后，更新同一 Run 的待执行状态，由兼容 Worker 创建新 Attempt。

WAITING_INPUT 是独立 Runtime 适配任务，不恢复原生内存 Future。每 Run 至多一个 OPEN prompt；答复绑定 prompt generation，工具审批另绑定 operation_id/request_hash/policy_version。答复接收与重新排队原子完成，状态先为 ACCEPTED，仅在包含应用结果的 checkpoint 提交时变为 APPLIED。等待超时进入 TIMED_OUT 并释放占用；暂停用 PAUSED，需显式 resume。部署必须配置等待/暂停/Run 时限及用户等待额度。详细取消、拒绝审批、迟到答复和清理门槛见 [等待与暂停规则](QwenPaw_Multiuser_Runner_Lifecycle_v1.md#5-waiting_input审批与暂停规则)。

取消先保存意图；运行中的 Worker 停止新工具调用、尽量到安全边界退出并清理。已排队任务可按事务取消；远程工具无法取消的部分记录为已发起或结果不明。不得把“停止本地等待”描述为撤销外部操作。

<a id="s14"></a>
## 14. 外部 API、内部协议与事件

本节接口均为拟新增业务契约，不是上游原生路由。正式 OpenAPI 与状态码在实现阶段冻结。

### 14.1 面向 BFF 的业务接口

| 方法与路径 | 行为 |
|---|---|
| `POST /v1/workspaces` | 创建工作区与初始版本，不占 Slot |
| `GET /v1/workspaces`、`GET/PATCH /v1/workspaces/{workspace_id}` | 查询、修改业务元数据 |
| `POST /v1/workspaces/{workspace_id}/reset` | 维护 operation，提交选择性重置 Revision |
| `DELETE /v1/workspaces/{workspace_id}` | 逻辑删除、停止新写入、释放引用及异步回收 |
| `POST /v1/sessions` | 创建 standalone 或 workspace Session |
| `GET/DELETE /v1/sessions/{session_id}` | 查询或按生命周期删除对话 |
| `POST /v1/sessions/{session_id}/promote` | 可选：显式迁入工作区 |
| `POST /v1/workspaces/{workspace_id}/files` | 导入已 READY 的固定 FileRef，创建新 Revision；不是接收文件字节 |
| `GET /v1/workspaces/{workspace_id}/files` | 查询已提交文件关联 |
| `DELETE /v1/workspaces/{workspace_id}/files/{entry_id}` | 移除工作区路径条目；不是物理删除 blob |
| `POST /v1/runs` | 幂等创建；保留输入引用并持久化后返回 202 |
| `GET /v1/runs/{run_id}` | 查询状态和产物引用 |
| `GET /v1/runs/{run_id}/events?after={event_seq}` | 事件补读与 SSE |
| `POST /v1/runs/{run_id}/inputs` | 提交匹配 prompt 的输入 |
| `POST /v1/runs/{run_id}/cancel` | 请求取消 |
| `POST /v1/runs/{run_id}/resume` | 仅对允许状态恢复 |
| `GET /v1/runs/{run_id}/artifacts` | 查询已发布业务产物 |

上传会话、内容流、文件版本、reference-set 位于第 24 节的 File API，不混入 Artifact 或 Workspace CRUD。Standalone 的 session_scope/owner 由 Session 查询绑定；不要求客户端重复传正确的 workspace_id，若传入则必须一致。

创建示例：

```http
POST /v1/runs
Authorization: Bearer <BFF_SIGNED_CONTEXT_TOKEN>
Idempotency-Key: request_001
Content-Type: application/json
```

```json
{
  "workspace_id": "ws_01",
  "session_id": "session_01",
  "agent_profile_id": "research_agent",
  "input": [{"type": "text", "text": "分析附件并生成报告"}],
  "attachments": [{"file_id": "input_01", "version": "fv_01"}]
}
```

```http
HTTP/1.1 202 Accepted
Content-Type: application/json
```

```json
{"run_id":"run_01","state":"QUEUED"}
```

先通过 FileGateway 确认输入 READY 并持久保留，再在 Core 事务中接受 Run；在途提交用稳定 operation_id 对账，失败不能留下可领取但输入未被保留的 Run。只在事务成功后返回接受结果。生产请求按第 4.5 节验证 BFF 工作负载身份和短期签名上下文，tenant/user 来自已验证上下文；忽略未验证身份头，不以“内网”代替认证。

### 14.2 Worker 内部接口

```http
POST /internal/workers/register
POST /internal/workers/{worker_id}/claim
POST /internal/attempts/{attempt_id}/heartbeat
POST /internal/attempts/{attempt_id}/events:batch
POST /internal/attempts/{attempt_id}/operations/begin
POST /internal/attempts/{attempt_id}/operations/{operation_id}/result
POST /internal/attempts/{attempt_id}/checkpoints/commit
POST /internal/attempts/{attempt_id}/finish
```

内部写入绑定 Attempt、代数与幂等键。claim 只由 Supervisor 调用；Runtime 通过受限本任务通道间接报告。Heartbeat 返回取消/排空等命令。文件字节由 Supervisor 通过 File API 传输；Worker API 只接引用和状态。不能借内部提交路由发放底层存储凭据。

### 14.3 SSE 事件语义

```text
id: 87
event: tool.completed
data: {"run_id":"run_01","event_seq":87,"attempt_id":"attempt_02","producer_event_id":"evt_007","operation_id":"op_005"}
```

`event_seq` 属于逻辑 Run 的权威事件，跨 Attempt 单调；`producer_event_id` 用于上传重试去重。权威事件先持久化再发布；Token 增量用有界暂态通道与独立 stream_offset，定期保存消息快照/合并片段。权威通知可尽力而为，但已提交事实必须能补读；未持久 Token 尾部允许丢失并显式标记，不保证逐 Token 无损重连。

快照带 message_revision 和 covers_stream_offset；重连补读权威事实、加载快照、再接仍存在的尾部，尾部丢失用 stream_reset 提示。最终消息按版本替换。多 API 副本须共享暂态路由/通道或明确按快照降级，纯 Pub/Sub 不提供持久补读。调试日志独立输出；调度/心跳与事件写入使用独立连接预算。

DDL 在 P2 冻结保留和分区选择，不强制首日分区。run_id Hash 兼容现有唯一键但不便按时间整分区清理；时间 Range 需处理唯一键包含分区键及跨分区去重。批量写、消息快照、索引与归档结合压测决定；不能把追加事件量直接等同于更新/删除产生的死元组。详见 [事件存储契约](QwenPaw_Multiuser_State_Transactions_v1.md#51-权威事件暂态文本与分区)。

实时订阅要处理“补读结束与注册实时订阅之间”的竞态，使用游标反复补读或等价方式避免漏事件。每连接队列有界，慢客户端断开后补读；超出保留期的游标返回明确错误及重新加载状态的方法。

事件输出区分业务状态、进度、文本增量、工具执行、检查点、等待输入和产物。恢复发布显式事件；旧 Attempt 的未提交文本标记为被中断/非最终，不与新 Attempt 文本无条件拼接。必要时返回最终消息版本覆盖暂态视图。

BFF 关闭响应缓冲，允许心跳和合适空闲超时；订阅断开不触发 Run 取消。事件数据不能包含凭据或完整未脱敏环境变量。

### 14.4 错误码建议

| 错误语义 | 客户端/调度处理 |
|---|---|
| `IDEMPOTENCY_CONFLICT` | 同键不同请求，拒绝，不重建 Run |
| `WORKSPACE_BUSY` | 按产品规则排队或返回冲突；不双写 |
| `STALE_ATTEMPT` / `STALE_REVISION` | 拒绝提交，查询当前状态 |
| `SANDBOX_POLICY_UNSUPPORTED` | 停止执行，不降级裸跑 |
| `CHECKPOINT_INCOMPATIBLE` | 路由兼容版本或人工处理 |
| `TOOL_RESULT_UNKNOWN` | 按冻结 fallback 失败/限时对账/受限继续，保留未知事实，不盲目重复副作用 |
| `EVENT_CURSOR_EXPIRED` | 重载任务快照及可用事件范围 |
| `BUDGET_EXCEEDED` / `QUEUE_LIMIT_EXCEEDED` | 明确停止或拒绝准入 |

<a id="s15"></a>
## 15. 持久数据模型与事务所有权

### 15.1 Harness Core 独占的业务表

| 表/对象 | 核心字段 |
|---|---|
| workspaces | user_id、workspace_id、lifecycle、head_snapshot_ref、revision、active_run_id、deleted_at、purge_status |
| sessions | user_id、session_id、session_scope、workspace_id NULL、conversation_ref、revision、active_run_id（standalone） |
| runs | run_id、user_id、session_id、workspace_id NULL、execution_scope_type/id、state、current_attempt_id、checkpoint_id、budgets |
| attempts | attempt_id、run_id、worker_id、slot_id、lease_epoch、lease_until、state、profile、runtime_identity（生产 image digest / 本机 bundle 指纹） |
| workers | worker_id、capabilities、versions、slots_total/busy、draining、last_seen_at |
| workspace_revisions | workspace_id、revision、files/Conversation/Memory 的 FileRef 与逻辑版本、checkpoint_id、reference_set_id |
| session_revisions | standalone session_id、revision、Conversation/必要中间文件 FileRef、checkpoint_id、reference_set_id |
| run_events | run_id、event_seq、producer_event_id、attempt_id、type、payload、created_at |
| checkpoints | checkpoint_id、run_id、base/committed_scope_revision、manifest_file_ref、reference_set_id、schema_version、committed_at |
| tool_operations | operation_id、run_id、logical_step_id、request_hash、state、result_ref、external_request_id、file_effect_ref |
| run_inputs | input_id、run_id、prompt_id/generation、FileRef/payload、ACCEPTED/APPLIED、applied_checkpoint_id、idempotency_key |
| artifacts | artifact_id、scope、run_id、file_ref、state、display_name；业务发布对象，不是存储对象 |
| request_keys | user_id、session_id、idempotency_key、request_hash、run_id |
| maintenance_operations | operation_id、scope、reset/delete/promote、expected_revision、state、error |
| file_commit_operations / outbox | commit_id、checkpoint/run/revision 目标、reference_set_id、PREPARING/PINNED/COMMITTED/ABORTED；outbox 独立 PENDING/DELIVERED |
| execution_scopes | owner、scope_type/id、revision、active_run_id、maintenance_operation_id、lifecycle；统一占用与锁事实源 |
| run_prompts | prompt_id/generation、kind、schema、continuation_ref、operation_id/request_hash/policy_version、expires_at、status |
| tenant_quotas / user_scheduler / quota_reservations | 必填 tenant、user、weight、virtual_finish、执行/排队/等待额度、Token/存储预留与幂等结算 |

以上所有业务表/关联通过 tenant_id 固定隔离域；Workspace 固定 owner_user_id，事件与操作另记 actor_user_id。大消息快照引用及 stream 覆盖游标单独存储，暂态 Token 不占用 run_events 权威序号。

### 15.2 File Service 独占的文件表

| 表/对象 | 核心字段 |
|---|---|
| files | file_id、固定业务范围、用途、展示属性、逻辑状态 |
| file_versions | file_id、version、READY 等状态、size、digest、mime、provider_ref（私有）、created_at |
| uploads | upload_id、scope、expected_size/digest、idempotency_key、state、expires_at、私有在途上传信息 |
| reference_sets / reference_members | reference_set_id、owner_namespace/type/id、成员 FileRef、sealed_digest、state、release_generation |
| storage_locations / gc_jobs | provider 私有映射、回收状态、在途保护、失败与重试 |
| file_idempotency_records | scope、操作类型、请求键、摘要、响应引用 |

同一 DB 实例可建立 `harness`、`files` 两个 schema；Core 不直接 SELECT/UPDATE 文件表，也不建立跨模块外键、级联删除或联合事务。文件模块不直接 JOIN runs/workspaces 表，应消费可信范围与引用契约。可复制必要的展示信息，但不得形成第二个存储位置事实源。

### 15.3 必须满足的不变量

1. Workspace 至多一个写入 Run；Standalone Session 至多一个写入 Run；每 Run 至多一个有效 Attempt 提交者。
2. session_scope 与 workspace_id 一致：standalone 为空、workspace 非空，owner 与所有关联范围匹配。
3. 业务 Head 只引用内容 READY 且已持久保留的完整 FileRef 集合，不含存储位置。
4. 过期 Attempt、scope 已删除或版本不符，均不得提交权威状态。
5. 文件服务内容完成与 Core 业务发布是两步；“上传成功”不等于“Run 成功”。
6. 引用集合只有明确释放后才进入回收；回收与上传/保留相互排斥，禁止 GC 时创建悬空引用。
7. outbox、operation 与幂等机制必须能处理响应丢失和两端暂时不可用；不能靠首期同库事务掩盖故障窗口。

<a id="s16"></a>
## 16. 端到端执行时序

### 16.1 正常任务

```text
1. 网页经 BFF 上传至 Harness File API 获得 READY FileRef；提交消息、Session 与固定文件版本。
2. API 校验关联、幂等和预算，经文件契约保留输入，再事务持久化 QUEUED，返回 run_id + 202。
3. 有容量的 Supervisor claim；API 分配 Attempt、租约和 execution_scope 占用。
4. Supervisor 经 File API 下载已提交 Manifest 与内容，在 Attempt 私有目录恢复状态。
5. SandboxManager 检测并创建任务边界，配置受控网络和专用事件通道。
6. 新进程在边界内首次导入 QwenPaw，载入会话、记忆、配置与计划。
7. Runtime 执行有界片段；工具按最小权限运行，事件经 Supervisor 持久发布。
8. 工具调用记账，安全边界保存并提交状态；Supervisor 独立续租。
9. 需要输入则提交 checkpoint/prompt，停止沙箱释放槽，保留逻辑工作区占用。
10. 收拢子进程和写入，经 File API 上传产物/快照，并建立持久引用集合。
11. Core 校验归属、生命周期与版本，事务发布 Workspace/Session Head、产物与终态。
12. 销毁本次沙箱、临时能力和可写层；确认清理后执行槽才可用于下一任务。
```

### 16.2 故障恢复

```text
Worker/沙箱失联
  → 租约过期，由应用层恢复扫描处理
  → 原 Attempt 标记 LOST；旧代数不得提交
  → 检查最新已提交 checkpoint 和待决工具操作
  → 有不可确认副作用：按冻结 fallback 失败、限时对账或经验证的受限继续
  → 可恢复：同一 Run 重新 QUEUED
  → 另一兼容 Worker 创建新 Attempt 与新沙箱
  → 加载已提交状态，按重放/幂等/对账策略继续
```

未提交文件可能丢失；已发生的远程副作用可能仍存在。恢复处理的是这两类事实，不能仅通过重新启动 Pod 或再次发送相同输入来代替。

<a id="s17"></a>
## 17. 上游源码事实与改造落点

以下事实沿用上文对 **QwenPaw v2.2.0** 的源码核查；本文汇总设计，没有再次验证远端版本或执行这些代码。生产开发必须固定最终 commit、依赖与镜像后重新核对。不要把已发现的具体后端行为泛化成所有平台或所有调用路径的结论。

| 上游路径/能力 | 上文已确认的事实 | 改造策略 |
|---|---|---|
| `src/qwenpaw/constant.py` | 工作目录、私密目录与 `.env` 在模块初始化阶段确定。[S01] | 新 Runner 进程导入前设置路径和最小环境；避免跨用户复用已初始化解释器 |
| `src/qwenpaw/app/task_tracker.py` | 活动任务、Future、事件缓冲在进程内；订阅队列为无界设计。[S02] | 不作为云端任务事实源；新增持久 EventSink 和有界订阅 |
| `/api/console/chat` | 原生对话入口，使用 Agent/Session 标识和 SSE。[S03] | PoC 私有适配；正式对外是 Run API；Agent ID 不是租户边界 |
| `src/qwenpaw/checkpoints/hooks.py` | 自动快照在会话保存成功后的 POST_RESPONSE 阶段调度。[S04] | 增加显式安全边界和同步提交协议，不宣称任意工具中途可恢复 |
| `src/qwenpaw/runtime/phases.py` | 外层生命周期 Hook 与 Agent loop middleware 分层。[S05] | Run 管理接外层；逐工具恢复在内层工具/循环边界实现 |
| `src/qwenpaw/sandbox/config.py` | `allow_read_all` 默认 True；文档注明环境白名单尚未实际实现，进程数/内存字段未被后端强制执行。[S06] | 服务端最小视图、干净环境及实际资源控制；禁止只写配置即宣布隔离 |
| `src/qwenpaw/sandbox/bubblewrap_sandbox.py` | 未使用 `--unshare-net`，网络限制字段未实施；父环境 `/tmp`、`/dev/shm` 被绑定可写；复制父环境变量。[S07] | 独占临时层、最小环境、受控网络；不能沿用这些默认行为作为云端多租户保证 |
| 同上，限制读范围的路径构造 | 即便关闭全盘读取，仍暴露较宽系统目录。[S07] | 使用干净、经过筛选的运行视图，不把 Supervisor 系统目录原样挂入 |
| `src/qwenpaw/governance/tool_adapter.py` | 存在工具策略与执行模式差异，OFF 模式中的部分 Shell 路径按设计可不进入工具沙箱。[S08] | 不可关闭的外层任务边界；审批免交互不能移除平台隔离 |

### 17.1 不优先修改的上游部分

不先改 Hub RuntimeProvisioner、不迁移 Hub SQLite、不全面重写原生 Console。新增外部任务服务与少量 Runtime 适配补丁，保持清晰接口，便于上游升级。

### 17.2 必须完成的运行路径盘点

记录每项能力实际在哪个进程执行、是否派生子进程、读写哪些路径、是否联网、是否持有凭据、是否可取消、是否可恢复。至少覆盖内置文件工具、Shell、REPL、本地 MCP、远程 MCP、浏览器、Skills 加载、插件初始化、模型 Provider 和后台子 Agent。

如果某条高风险路径绕过统一沙箱/代理，首期禁用或完成迁移后开放。不能仅因工具名称带有“安全”或配置显示 sandbox_enabled，就认为执行已受限。

<a id="s18"></a>
## 18. 建议代码结构、模块契约与配置

### 18.1 拟新增代码结构

```text
qwenpaw_cloud/
  api/
    workspaces.py
    sessions.py
    runs.py
    events.py
    artifacts.py
    internal_workers.py
  domain/
    execution_context.py
    run.py
    attempt.py
    workspace.py
    budgets.py
    policies.py
  repositories/
    postgres.py
  worker_contracts/               # 独立发布；不依赖 Core ORM 或 QwenPaw
    openapi.yaml                 # 跨语言请求/响应、错误与版本契约
    models.py
    fixtures/                    # Java/Python 共同契约样本
  control_client/
    http_client.py               # Worker 访问 Core 的唯一业务入口
  worker/
    supervisor.py
    slot_manager.py
    runner_entry.py
    process_manager.py
    watchdog.py
  sandbox/
    manager.py
    capability_probe.py
    filesystem_policy.py
    network_policy.py
    resource_policy.py
    backends/
  adapter/
    qwenpaw_runner.py
    event_sink.py
    checkpoint_adapter.py
    tool_operation_adapter.py
  file_contracts/                 # 无存储供应商依赖
    models.py                     # FileRef / Scope / Metadata
    gateway.py                    # FileGateway Protocol
    errors.py
  file_client/
    http_client.py                # Worker；Core 拆分后也使用
    inprocess_gateway.py          # Core 首期可用，不穿透模块
  fileservice/                    # 可整体迁移到 BFF
    api.py
    application.py
    domain.py
    metadata_repository.py
    reference_sets.py
    garbage_collector.py
    adapters/
      blob_store.py               # BlobStorePort
      oss.py                      # 仅此层使用存储 SDK
      s3.py
      local_test.py               # 仅开发测试
    migrations/                   # files schema
  workspace_io/                   # 留在 Harness / Worker
    materializer.py
    snapshot_codec.py
    safe_paths.py
    artifact_collector.py
  recovery/
    scanner.py
    replay.py
    reconciliation.py
  observability/
  migrations/
  tests/
    unit/
    integration/
    isolation/
    recovery/
    load/

deploy/
  worker/
  harness_api/
  file_service/
```

上述是同仓库目录建议，不是共享运行时依赖的许可。构建 control-api 与 worker 独立安装/启动单元，Worker 单元不包含业务 Repository/迁移权限，协议包不能反向导入业务实现。Core 内 recovery/scanner 与 reconciliation 负责权威恢复决策，Worker adapter 仅执行已授权 recovery_plan；本机端到端测试也使用真实 HTTP 客户端，不直接调用 Core 服务对象绕过边界。

这是建议模块边界，不要求所有文件独立成包或微服务。最终使用语言、ORM、Web 框架和部署模板由工程团队决定，不影响任务协议。

### 18.2 跨模块契约

| 模块 | 输入 | 输出/责任 |
|---|---|---|
| `SlotManager` | Worker 容量、资源预算、任务 profile | 是否可领取；预留和释放容量 |
| `WorkspaceMaterializer` | 固定范围的已提交 manifest | 私有可写副本、校验结果 |
| `SandboxManager` | 只读强制 policy、挂载、资源、网络 profile | 已验证边界的句柄；失败不得裸跑 |
| `QwenPawRunner` | ExecutionContext、版本、输入、恢复状态 | 结构化事件、可导出状态、结束原因 |
| `ToolOperationAdapter` | operation_id、请求摘要、工具类别 | 结果、重放/幂等/对账信息 |
| `CheckpointAdapter` | 静止边界的会话/记忆/文件状态 | 不可变 manifest；不自行越权发布 head |
| `FileGateway` | 固定 Scope、FileRef、上传/引用请求 | 稳定契约，不含 provider locator |
| `FileService` | 契约请求和内容流 | 文件 READY、内容保留与回收；不推进业务 Head |
| `BlobStorePort` | 文件模块内部私有 locator 与内容 | 具体存储读写；不向 Core/Worker 暴露 |
| `FileCommitCoordinator` | 业务提交意图、FileRef 集合 | 先保留后发布，补偿和释放 outbox |
| `RecoveryScanner` | 过期租约、checkpoint、操作账本 | 安全重新排队、失败或待对账 |

### 18.3 配置结构示意

下列字段全部属于本项目拟定 schema，**不是可直接传给 QwenPaw 的原生配置，也不是已验证的可部署文件**。数值是演示，真实容量与后端在验证后填写。

```yaml
worker:
  max_concurrent_runs: 1                 # P0—P3 默认；P4 验证后才提高
  runtime_entry: qwenpaw_cloud.worker.runner_entry
  claim_only_when_capacity_available: true
  per_workspace_active_write_runs: 1

runtime:
  fresh_process_per_attempt: true
  agent_profile_version_required: true
  image_digest_required: true
  working_dir: /state/qwenpaw
  project_dir: /workspace
  shared_writable_home: false

sandbox:
  backend: REQUIRED_VALIDATED_BACKEND    # 占位符必须使生产启动校验失败
  required: true
  fail_closed: true
  isolated_filesystem_per_attempt: true
  isolated_tmp_home_and_shm: true
  tool_restriction_required: true
  policy_mutable_by_agent: false
  env_mode: explicit_allowlist           # 要在启动代码中真正实施
  limits_profile: REQUIRED_MEASURED_PROFILE

network:
  default_egress: deny
  runtime_profile: runtime_required_services
  offline_tool_profile: no_external_network
  browser_profile: controlled_web_egress
  supervisor_admin_api_visible_to_runtime: false

# Core/Worker 使用的配置；示例名称不是上游原生参数
files:
  worker_transport: http
  api_base_url: https://harness.internal/internal/file-api/v1
  allow_storage_redirects: false
  provider_fallback_enabled: false
  core_gateway: http_file_service          # 生产独立数据面；进程内仅开发/测试

workspace_io:
  mode: attempt_private_copy
  checkpoint_publish: upload_pin_then_core_commit

# 只有首期 Harness 内 fileservice 读取；Worker/Runtime 不接收
file_service:
  enabled: true
  metadata_schema: files
  blob_driver: REQUIRED_SELECTED_PROVIDER
  provider_config_ref: REQUIRED_FILE_SERVICE_ONLY_CONFIG
  versioned_file_content: true
  stream_content_through_api: true
  global_storage_credentials_in_sandbox: false

recovery:
  baseline_level: committed_boundary
  tool_boundary_recovery: validated_tools_only
  unknown_outcome_policy: per_tool_frozen  # 默认失败或限时对账，低风险精确预授权另验
  waiting_input_releases_slot: true
  waiting_input_keeps_workspace_reservation: true
```

需要有“期望策略 → 后端能力探测 → 实际约束验证”链路。以上任何 true 都不能代替实现与测试。心跳周期、租约时长、超时、每槽资源、允许域名/端口、保留期等没有在对话中确定，不提供伪精确默认值。

<a id="macos-dev"></a>
### 18.4 本机 macos-dev 配置契约

目的：直接在开发者 macOS 上开发和执行功能/协议测试，不要求虚拟机、Docker 或本地 K8s。此处仍为拟新增配置，不表示现有 CLI 已接受这些字段或沙箱已通过验证。第 18.3 节是 Linux 生产目标；profile 必须显式选择，不能探测失败后自动从生产切换到开发模式。

```text
本机测试客户端/开发 BFF
  → Core / Event API（独立进程）
  → File API（独立进程，local_test 驱动）
  → 本机或专用远程测试 PostgreSQL
Worker Supervisor（每进程单 Slot，可启动两个进程模拟不同 Worker）
  → 新 Attempt 私有目录 + 全新 Python 进程
  → native_seatbelt 任务启动适配器 → QwenPaw Runner
```

P0 先使用 fixture executor 验证协议，再接 native Runner；fixture 不执行 Agent/Shell/上传代码，不能声称已测试真实 Runner 或隔离。开发服务默认仅监听 loopback；连接远程测试 PG 时显式配置测试 DSN/TLS，禁止默认连接生产库。Core/文件调用仍走既定 HTTP 契约和独立事务。

拟定覆盖配置：

```yaml
profile: macos-dev
deployment_kind: native_processes
api_bind: loopback
worker:
  max_concurrent_runs: 1
  fresh_process_per_attempt: true
runner:
  executor: fixture                  # 协议桩；native 必须单独启用并通过探测
  model: deterministic_fixture      # 默认离线，无模型凭据
  identity: local_bundle_fingerprint # commit + dirty diff + 依赖锁摘要
sandbox:
  native_backend: native_seatbelt    # 拟新增“整个 Runner”启动适配器
  required_for_native: true
  fallback_to_unsandboxed: false
  capability_report_required: true
paths:
  root: REQUIRED_ABSOLUTE_DEV_ROOT
  per_attempt: true
database:
  engine: postgresql
  dsn_ref: REQUIRED_TEST_DSN          # 仅运行数据库集成用例时必需
files:
  worker_transport: http
  core_gateway: http_file_service
  driver: local_test
  direct_transfer_enabled: false
```

root 由开发配置传入且排除源码、真实用户工作目录与密钥目录；Supervisor 为每 Attempt 建立 home/tmp/state/workspace/secrets 子目录。首次导入 QwenPaw 前，用显式环境白名单设置 HOME/TMPDIR/QWENPAW_WORKING_DIR/QWENPAW_SECRET_DIR。不继承整份 os.environ，不加载真实用户 .env、系统钥匙串或开发仓库密钥；仅提供离线 fixture 或本任务测试能力。Python/site-packages 可按需只读授权，不能因此放开整个真实 HOME。

macOS 不使用 Linux 的 /state、/workspace 绝对路径假设，使用 PathMap 解析实际私有根；导出只含逻辑相对路径与 FileRef，不保存宿主绝对路径。真实模型、MCP、Shell、浏览器、桌面、Cron/Heartbeat、插件自动安装默认关闭，仅显式验证的测试用例开放最小子集。

现有 [MacOSSandbox](../../src/qwenpaw/sandbox/macos_sandbox.py) 是命令级 Seatbelt 实现；需新增适配器把整个 Runner 纳入边界。现有探测仅检查 sandbox-exec 是否存在，不能作为隔离通过证据；环境白名单、资源限额也不能仅靠原生字段宣称实现。探测必须包含实际允许/拒绝用例和进程清理。

默认离线模型桩不联网。接真实模型前必须提供批准的模型端点、测试凭据和小额预算，并单独验证网络路径；现有 Seatbelt 后端无法落实域名白名单时不得把它记作已受控出站。无法满足声明的 native 必需能力则拒绝该用例；仍可运行明确标记的 fixture 测试，但禁止将 native 失败静默降级为裸进程或全网络放通。

开发基线已确定 Python **3.12.11**，真实模型提供方为 **DeepSeek**；离线模型桩仍用于可复现协议测试。具体 model ID、思考模式、API 端点和调用预算在真实联调前固定，不以提供方名称替代完整模型配置。3.12.12 不作为 3.12.11 的隐式替代；P0-01 对齐仓库 `.python-version`、独立虚拟环境、依赖锁定和 runtime_identity。该版本位于项目声明的 Python 范围内，但完整依赖兼容性仍需实际安装与导入验证。

**当前 sandbox-exec 的适用结论：可作为 macos-dev 的任务沙箱候选，现有包装尚不满足本协议，不能用于生产多用户隔离验收。** 代码审查发现：MacOSSandbox 当前为命令级入口；默认允许广泛文件读取；环境从 os.environ 继承；非空 network_allow（包括仅一个域名）会生成允许全部网络的规则；资源配置不构成内存/进程数硬限制。P0 必须新增整个 Runner 的启动边界、关闭广泛读取并按路径授权、最小环境及限量输出收集，实测越界读写、其他 Attempt 访问、未授权联网、超时/取消后的后代进程清理。资源硬隔离缺失标 UNSUPPORTED，限制为可信开发负载；不能用超时或单 Slot 冒充资源隔离。

DeepSeek 联调优先设计为沙箱外的可信模型网关：上游凭据仅由网关持有，固定上游与允许模型、按 Attempt 鉴权并限制预算/请求大小；Runner 仅获得受限调用能力，Shell/Python 工具默认无网络。需要新增并验证 Seatbelt 端点限制或受控 IPC 适配，确保不能绕过网关访问公网及其他本机服务；仅设置代理环境变量不算落实。现有内置 DeepSeek provider 固定官方 base_url，网关接入需要明确模型适配入口，不能假设已经支持 URL 重定向。若该路径验证失败，真实模型用例保持阻塞，离线 P0 可继续。

| 项目 | macos-dev 规则 | Linux 生产规则 |
|---|---|---|
| 任务/文件/恢复契约 | 与生产相同，不跳过 tenant、租约、幂等、pin 或版本校验 | 相同 |
| Runtime 版本 | 固定 commit、依赖及工作树补丁的本地指纹；不跨版本静默恢复 | 固定镜像 digest/依赖/profile/schema |
| 持久数据库 | SQL/锁/并发集成测试用真实 PG；fixture repository 仅单元测试 | 生产 PG |
| 文件驱动 | local_test 只用于功能和故障注入，不证明对象存储耐久性 | 选定生产驱动与备份验证 |
| 文件/进程/网络限制 | Seatbelt 任务适配逐项实测；独立目录/进程不自动等于强隔离 | 实际 Linux 后端与集群配置验收 |
| 资源/宿主机制 | cgroup、Linux PID/net namespace、K8s NetworkPolicy、跨 Pod OOM 为 UNSUPPORTED | 必须有对应正/负向证据 |

测试报告分 PASS/FAIL/UNSUPPORTED，并记录 profile、真实/fixture executor、OS/架构、Python、commit/依赖指纹、沙箱策略与实测能力。跳过的 Linux 用例不得计入 PASS。macos-dev 不接受不互信用户工作负载，不作为生产对外运行配置；不通过开发 profile 关闭生产强制校验。

<a id="s19"></a>
## 19. 部署、发布、监控与运维

### 19.1 部署边界

```text
harness-api / event-api   普通共享后端，可使用既有应用平台
agent-worker             常驻 Worker Pod 池，P0—P3 每 Pod 单 Supervisor/单 Slot
file-api                 Harness 所属文件数据面，生产独立进程/实例组
PostgreSQL               已有/托管持久数据库
Object Storage           OSS/S3 兼容持久内容存储
Execution Sandbox        经验证的后端与宿主支持
```

API 不必与 Worker 处于同一 K8s 集群。Worker 不暴露给网页，不需要 Kubernetes SDK 或创建工作负载权限。对当前可信用户 P1，部署配置中的资源限制、只读根、非 root、禁止提权、seccomp、NetworkPolicy 与 Attempt 清理共同构成执行边界；它们不等同于面向不可信任意代码的任务级强沙箱。[S15]

如果后端需要宿主资源委派、额外运行时或沙箱管理服务，由基础设施层明确配置和评审，不将整个 Pod 设为 privileged，不向任务挂载 docker.sock/hostPath 等广泛宿主能力。

### 19.2 发布与排空

固定 QwenPaw commit、依赖锁、镜像 digest、Agent profile、Skills 和检查点 schema。恢复只能交给声明兼容的 Worker。新旧版本可并存；旧任务不能在一次恢复中静默加载不兼容版本。

Supervisor 接到停止/排空信号后，不再领取新任务；运行中的 Attempt 尝试提交安全边界并退出。超过宽限期走正常故障恢复。Pod 优雅退出机制只是最后的执行窗口，不能替代检查点和恢复协议。[S22]

探针区分“管理进程存活”“是否接新任务”“执行槽是否繁忙”。Agent 长时间调用工具不是 liveness 失败；满槽也不是进程故障。

### 19.3 容量扩展

先支持手工增加 Worker 副本，再按需要使用基础设施自动扩容。HPA 可使用自定义/外部指标，队列等待、槽位利用率可作为容量信号，但不是本项目必须实现的业务控制器。[S24]

多槽上线前验证每槽实际资源限制；用真实任务测 P95/P99 占槽时长、内存、CPU 限流与文件处理成本。不要只根据在线人数或 API QPS 估算执行池。

### 19.4 必备观测

建议监控：队列长度和等待时间、可用/繁忙/隔离槽数、物化和沙箱启动耗时、模型/MCP 延迟与限流、每类任务占槽时长、检查点耗时与失败率、事件积压、恢复次数、旧提交拒绝数、工具结果不明数、沙箱策略拒绝、资源越限、清理失败、对象存储增长与 GC。

日志/Trace 关联 `user_id/workspace_id/run_id/attempt_id/worker_id/slot_id`，敏感内容脱敏；指标标签避免无限用户/Run 高基数。记录版本和策略摘要，以便复现隔离或恢复问题。

### 19.5 备份与生命周期

数据库与对象引用共同构成恢复状态；备份演练必须验证引用对象存在、解密所需配置可用、版本兼容且能实际重新执行。删除工作区前处理其活动 Run、等待输入和恢复引用。对象回收遵循引用与保留期，不根据本地目录是否存在判断。

<a id="s20"></a>
## 20. 分阶段实施与工程任务

### 20.1 阶段与交付门槛

| 阶段 | 交付 | 阶段门槛 |
|---|---|---|
| P0：基线与关键 PoC | 本机 macos-dev；固定版本/依赖；fixture 协议测试与 native Runner 初始化/关闭/导出；Linux 候选能力清单 | 本机真实执行证据与 fixture 结果分开；列明 Linux 未验证项，不等于生产隔离通过 |
| P1：可信任务 Pod 执行边界 | Kylin V10（Tercel）/K8s v1.21.7；单 Pod/Worker/Slot；Pod 资源与安全上下文、私有 Attempt 环境、Pod 级受控网络和清理 | 资源超限有界失败、必要网络成功、禁止目标失败；清理失败停用 Worker；同槽顺序执行不串数据；不宣称不可信代码强隔离 |
| P2：多用户异步服务与持久提交 | 分域数据模型、幂等 Run、领取/续租、持久事件、BFF、文件上传/READY/pin/Core commit/outbox、基础产物 | 多 Worker 分域；同 Scope 不双写；断线不取消；输入接受和产物发布有持久引用；提交响应丢失可查回 |
| P3：长程可靠性 | 在 P2 提交链路上增加一致 Conversation/Memory 快照、工具账本接回、恢复、等待输入、暂停、取消和累计预算 | 杀 Worker 后跨 Worker 从已提交边界继续；未知副作用可对账；答复可恢复应用；Memory 导出失败不发布 |
| P4：容量与可选多槽 | 按资源 profile 验证多槽；成本调度、压测、排空升级、监控、备份/GC | 多槽准入不通过保持单槽扩副本；给出容量与支持范围 |

当前 P1 仅面向可信用户，不把任务级强沙箱作为门槛；若未来接入不可信用户或开放任意代码，必须先通过独立强沙箱阶段。P2 不等于已承诺故障续跑；至少通过 P3 才能作为具有恢复语义的长程 Harness。P4 的高密度配置不能放松 P1 已验证的 Pod 资源、网络和清理门槛。

P0—P3 默认单 Slot。两级身份、BFF 服务鉴权、原子额度和公平领取在 P2 落地；文件数据面生产独立部署。Park & Yield、受限直传、私有 CAS、Memory Delta 为后续按需演进，不隐含为 P2/P3 必做；完整 Workspace 并发合并单独立项。

### 20.2 可拆分工程任务

| 编号 | 任务 | 主要依赖 | 完成标准 |
|---|---|---|---|
| T01 | 固定源码、依赖与运行身份 | 无 | commit/dirty diff/依赖锁齐备；本机 bundle 指纹，Linux 生产镜像 digest 与回退版本 |
| T02 | macos-dev Runner 最小 PoC | T01 | fixture 与真实 native 结果分开；新进程可执行、保存、恢复会话并关闭 |
| T03 | 工具与运行路径盘点 | T02 | 文件/网络/进程/凭据/恢复能力矩阵完整 |
| T04 | Kylin/K8s 宿主能力验证 | T01 | 记录 SP/内核/架构、容器运行时、cgroup、kubelet PID、seccomp、CNI 和 K8s v1.21.7 实际能力 |
| T05 | ExecutionContext 与配置加载 | T01、T05a 最小协议 | 路径和范围在首次导入前固定，策略不可被工作区覆盖；P0 子集先于 T02 的真实 Runner 集成 |
| T05a（P0/P2） | 独立 Control/Worker 协议与启动入口 | T01；先于真实 Worker 集成 | P0 冻结最小 OpenAPI/错误/幂等样本并做分进程 HTTP 测试；P2 完成分机、服务身份、版本兼容与无业务 DB 权限验收；P3 扩展恢复协议 |
| T06 | ExecutionBoundary 生命周期 | T04、T05 | Runner 新进程、停止、清理和隔离停用幂等；Pod 必需前提不满足则拒绝领取 |
| T07 | Attempt 私有环境与进程清理 | T06 | 当前工作区/状态/临时目录独占；不复用 Runtime 与可写环境；顺序执行不串数据 |
| T08（P1/P4） | Pod/单槽资源约束 | T04、T06 | P1 固定并实测 CPU/内存/PID/临时盘/时限/输出限制；P4 再优化容量与多槽密度；P0 macOS 缺失硬限制明确标 UNSUPPORTED |
| T09 | Pod 网络与控制通道 | T06 | CNI 实际执行默认拒绝；必要 DNS/Core/File/测试网关可用，非允许目标不可达 |
| T10 | P1 工具范围冻结 | T03、T07、T09 | 不因取消强沙箱自动开放高风险工具；提前开放项逐一记录共享 Pod 权限和残余风险 |
| T11 | 工作区数据模型与约束 | T05 | 同名用户资源不串域，同工作区单写者 |
| T12 | Run/Attempt/租约模型 | T11 | 旧代数不能提交，逻辑占用与活跃租约分离 |
| T13 | 幂等 Run API | T12、T21a | 同请求同 Run，同键不同请求冲突；输入已持久保留才返回接受 |
| T14 | 领取、额度与能力匹配 | T08、T12、T28 | 不双占、不超领，短事务，无全局长锁 |
| T15 | 单槽 Supervisor 与 watchdog（P4 再开放多槽） | T06、T08、T14 | 心跳不受任务阻塞，容量可配置，失联停止新调用 |
| T16 | 持久 EventSink 与消息快照 | T12 | 权威序号/去重与提交一致；暂态 stream_offset 分离；快照补读及尾部缺失提示 |
| T17 | SSE/BFF 协议 | T13、T16 | 权威事件可补读；暂态尾部丢失显式提示；快照恢复；慢客户端有界 |
| T18 | File Service 契约与独立模块 | T11、FS01—FS04 | 只有文件模块使用 SDK；Core/Worker 使用契约，范围固定 |
| T19 | 安全导入/解压/导出 | T07、T18 | 链接/路径穿越/特殊文件/规模限制有对抗测试 |
| T20 | 工作区物化与状态导出 | T02、T18、T19 | 文件/会话/记忆跨 Worker 重建一致 |
| T21a（P2） | 核心文件条件提交 | T12、T18、T19、FS05、FS06 | Run 输入/基础 revision/产物统一 upload→READY→pin→Core commit；响应丢失可查回，补偿不误删 |
| T21（P3） | 一致快照接入条件提交 | T20、T21a | 复用 P2 链路提交 Conversation/Memory/文件；旧 Attempt/revision 被拒绝；重复提交幂等 |
| T22 | 工具操作账本 | T03、T12 | 分类、稳定 operation_id、结果/文件影响持久化 |
| T23 | L2 恢复与回退 | T21、T22 | 安全重放；UNKNOWN 保留事实，冻结 fallback 与精确授权，禁止盲目重试 |
| T24 | WaitingInputAdapter 与暂停续跑 | T21、T23 | continuation/prompt/checkpoint 原子提交；ACCEPTED→APPLIED 随 checkpoint；过期/拒绝/迟到/取消规则通过；不恢复 Future |
| T25 | 取消、超时与预算 | T15、T22 | 真实清理、不可取消远程操作如实记录、预算跨 Attempt |
| T26（P2） | 产物发布与下载 | T18、T21a | 对象校验、引用封存和业务提交成功后才可下载 |
| T27a（P0/P1） | 本地清理、槽隔离与安全复用 | T06/T07 的本阶段子集；不等待 T25 | P0 验证受控子进程、私有目录、凭据/FD 清理及失败停用；P1 在真实 Kylin Worker Pod 验证进程/目录清理与失败停用；仅发送停止信号不算成功 |
| T27（P3） | 清理接入业务取消与恢复 | T27a、T15、T25 | 业务状态与清理状态分别记录；接入远程未决副作用与恢复准入，失败槽不复用 |
| T28（P2） | 两级额度与加权公平领取 | T12 | tenant/user 原子预留、并发不超额；加权虚拟完成时间；恢复不饿死新任务；供 T14 使用 |
| T29 | 版本兼容与排空发布 | T21、T23、T27 | 新旧池共存、旧任务不误加载新格式 |
| T30 | 日志、指标与链路 | T15、T16、T23 | 可定位任务、资源、网络、恢复与清理故障 |
| T31 | 内容保留/GC 与备份恢复 | T18、T21、T26、FS05 | 由文件模块按引用 GC；Core 发出释放 outbox，不按前缀删除 |
| T32 | 多槽与混沌验收 | T10、T17、T23、T27—T31 | 完整测试报告、容量数据与开放能力清单 |

新增业务任务（承接 v4，使用独立编号，避免与 T01—T32 冲突）：

| 编号 | 任务 | 验收 |
|---|---|---|
| B01 | Standalone/Workspace 双 Scope | 普通对话无需 Workspace；各自单写者与幂等成立 |
| B02 | Workspace CRUD/Reset/Delete | 创建不占 Slot；维护期间禁止新写入；删除与 GC 分离 |
| B03 | Memory 版本及范围 | 同边界提交/恢复，禁止跨工作区检索，索引可重建 |
| B05 | MemorySnapshotAdapter 契约 | 六方法与后端/格式版本验证；停止后台写入及一致导出失败不发布；保留各 Session 会话映射 |
| B04 | 可选 Session Promote | 在明确范围迁移 Conversation/文件引用，不移动 Pod |
| B06（P2） | Tenant/owner 与 BFF 服务鉴权 | tenant 必填；owner 唯一归属；mTLS/等价身份和签名用户上下文；伪造头、错误 audience、跨域资源均拒绝 |
| B07（后续） | Park & Yield | 分支保留、清理确认后让渡；恢复 revision 检查；输入竞态不绕过检查 |
| B08（后续） | Memory Delta/私有 CAS | 未变引用复用先行；增量闭包与有界链；缓存不可写穿、不能凭摘要越权 |

文件服务专项任务 FS01—FS12 与迁移验收见第 24 节。外置到 BFF 属于后续阶段；文件契约、内容代理和双模块一致性属于首期交付。

建议关键路径：`固定版本/Runner PoC → 沙箱与网络验证 → P2 异步任务服务及文件核心提交链路 → P3 一致快照、等待与恢复 → 多槽容量与生产验收`。FS01—FS06 的核心协议、基础引用 GC 和故障窗口测试必须在 P2 完成；P3 扩展快照，P4 完成保留运营与容量验证。独立数据模型、存储和事件模块可并行推进，但上线门槛不能跳过。

<a id="p0-readiness"></a>
### 20.3 第一阶段开发判断与最小交付

**可以开始 P0（基线与 PoC）开发；尚不能认定已具备 P1 生产隔离验收条件。** 本次是文档与只读环境核查，没有创建运行配置、安装依赖、运行 QwenPaw 或完成沙箱实验。业务不变量和模块边界已足够支持适配层开发；生产后端、容量参数与真实服务接入可在对应阶段补齐。

P0 的唯一交付目标是：在 macos-dev 上，以独立 Core、Python Worker 和真实沙箱 Runner，证明一个固定离线任务能够经 HTTP 领取、执行、提交、关闭，并在新 Worker 上从已提交版本继续。它是协议与适配可行性验证，不是缩小用户数后的完整生产平台。下表细化原 P0-02/03/05，旧编号仍作为对应任务组；跨期 T 编号在 P0 仅实现这里列出的子集，不等待其 P2/P3 全量依赖。

| 步骤 | 依赖 | 最小范围 | 完成证据 |
|---|---|---|---|
| P0-01 环境基线 | 无 | Python 3.12.11、独立环境、依赖锁、源码/dirty 指纹、私有开发根、macos-dev 配置校验 | 重建与导入命令、角色配置样例；不读取真实用户配置；建立能力报告格式，实际能力尚未测试时不标 PASS |
| P0-02a 协议与进程骨架 | 01 | 最小 OpenAPI、独立 control-api/worker 入口、HTTP 客户端、测试服务身份与版本校验 | 两进程通信；固定 JSON 样本、错误码、重复请求与不兼容版本用例；Worker 无业务 DSN/ORM |
| P0-02b fixture 生命周期 | 02a | ExecutionContext、PathMap、Runner 接口、fixture executor、NullMemory | 两个合成用户同名资源不串目录；初始化/导出/关闭正常与失败路径；本项不加载真实 QwenPaw |
| P0-03a native 启动边界 | 02b | Seatbelt task launcher、最小环境、关闭无关 FD、私有 HOME/tmp/state/workspace | 从启动即受限的受控 Python 探针成功；合法依赖可读、真实 HOME/其他 Attempt 不可读写 |
| P0-03b 隔离与退出 | 03a | 默认拒绝网络；允许本任务必要 IPC；超时/取消、本地 watchdog、有界 stdout/stderr、清理失败停用槽 | 越界/符号链接、继承凭据/FD、未授权网络均拒绝；受控后代退出；洪量输出不无界占内存；未确认清理不复用 |
| P0-03c 真实 Runtime 闭环 | 03b | Workspace.stream_query、确定性离线模型、Memory=none、固定受控文件用例 | 真正执行→停止写入→导出→关闭→新进程恢复；文件摘要、会话工具关联与下一步一致；保存/导出失败显式返回 |
| P0-04 Memory 旁路调查 | 02b；真实后端测试需 03b | ReMe Light 六方法与依赖可行性、小样本一致导出 | 输出可行/不支持/阻塞及证据；成功集成不作为 P0 出口条件，不阻塞 05；不得通过静默改用 none 声称 ReMe 通过 |
| P0-05a PG 最小持久链路 | 02a、02b，真实测试 PG | 最小迁移、HTTP 幂等创建/claim/续租、File API local_test、封存和条件提交/查询 | 独立 Core/File API；两个 Worker 竞争只得一个有效分配；输入与提交集合持久保留；先可用 fixture 调试 |
| P0-05b 故障与跨 Worker 重建 | 03c、05a | 接入真实 native Runner，执行下述四组故障测试 | 可重复故障注入与数据库断言；Worker B 从已提交版本继续，旧提交拒绝；不复制 Worker A 活动目录 |
| P0-06 验收报告 | 必需主线全部通过；04 提供调查结论 | 汇总五道门槛、未支持项、Linux 环境需求及 P1 后端候选 | 命令、固定输入、版本、结果与证据可复现；阻塞项明确，不把生产待验项计作本机通过 |

主线为 `01 → 02a → 02b → 03a → 03b → 03c → 05b → 06`；05a 在 02b 后且 PG 可用时推进，05b 等待 03c 与 05a；04 为旁路，不插入主线。默认新增 qwenpaw_cloud 适配层，不重写 Hub/Console；实现者直接确定工程路径与测试参数并记录，无需等待真实模型或生产环境就能开始 01/02。

#### 20.3.1 最小协议、数据与测试任务

P0-02a 固定版本化 JSON schema、字段类型、UTC 时间/代数编码、错误/重试与幂等规则。最小 API 覆盖：测试 Run 创建/查询、Worker 注册/能力、claim 及请求结果查询、heartbeat/control、ready、checkpoint submit/query、boundary 与 cleanup。测试身份仍需验证服务身份和分配关系；仅绑定 loopback 不能替代鉴权。02 阶段可用 fixture repository 验证协议，05 必须替换为真实 PG；未来增加 operation 与 input API 使用显式版本扩展，不提供假成功占位实现。

05 的最小业务数据包含可信 tenant/user/scope/session、Run、Attempt/epoch/租约、请求幂等记录、Scope revision/active_run、commit/引用集合关联、检查点与清理状态，以及支持这些操作的最小权威事件。可以使用固定测试租户和静态准入额度，不建设完整用户管理、加权公平调度和计费；必须验证 Scope 单写、有效 Attempt 唯一与并发条件更新。表/字段语义沿用状态机文档，不能另建一个与未来模型相反的临时状态机。

File API 子集包含上传/complete→READY、下载固定版本、pin/query 与引用保留；Core 使用同一提交协议，不能把本地文件存在当成 COMMITTED。测试期间禁用自动 GC，持久保留已用与未决集合；P0 清理采用停服后的显式测试环境重置，不借此声称已完成 P2 补偿/GC。File Service 元数据及 Core 事实持久化，Core 重启后不能丢失封存与 commit 查询结果。

固定 native 场景：离线模型通过批准的确定性文件工具生成一个小文件并形成完整回合，保存 Conversation、文件、NullMemory 标记、游标/下一片段描述与预算；提交后关闭，在另一个 Worker 上校验摘要与消息/工具关联，并执行固定的第二片段。仅在已经提交的回合边界恢复，未提交的纯本地工作允许丢弃；禁用真实外部副作用，不在 P0 承诺任意工具中途接回。跨 Worker 只经 File API 传递；固定版本与反序列化校验仍必须执行。

#### 20.3.2 四组必需故障实验

| 编号 | 故障注入点 | 必须断言 |
|---|---|---|
| P0-F01 | 两 Worker 并发 claim；数据库分配提交后丢弃响应 | 单一有效分配；相同 request_id 查回原 Attempt，不重复占用；过期原分配不被悄悄换成新分配 |
| P0-F02 | Core 重启、控制链路中断和续租响应延迟 | PG 事实不丢；Worker 在固定本地期限停止；迟到响应不复活 Attempt；清理失败槽停用 |
| P0-F03 | pin 完成但发布前故障；Core 发布提交后丢弃响应 | 前者不发布新 Head；后者按 commit_id 查回同一结果，不重复推进 revision；未决引用仍保留 |
| P0-F04 | 已提交边界后终止 Worker A；以新 Attempt 在 B 重建；再发送 A 的旧代数请求 | B 恢复固定文件/会话版本并继续；A 的 ready/续租/提交无法获得新执行权；终态不可被迟到结果改写 |

故障必须在明确屏障处注入，记录事务已提交与否，避免仅靠随机 sleep 猜测窗口。预置有界测试时限：续租间隔、租约有效期、安全余量、控制请求超时、停止宽限、输出上限、文件上限、重试上限及用例总超时；02a 冻结可覆盖的测试默认值，启动时验证其关系和范围。验收据固定期限判断，不使用无限轮询或随意扩大超时掩盖失败；测试值不代表生产容量结论。

<a id="p0-session-tests"></a>
#### 20.3.3 多会话测试夹具与必需断言

在既有 P0 测试结构中增加 P0-S01/P0-S02，不新增会话管理产品接口或调度框架。固定测试身份 tenant=t1、user=u1，两个会话使用相同 Agent 配置和显示名称、不同稳定 session_id=s1/s2；各自写入不同的确定性消息及工具结果标记。Memory 显式为 none，避免混入长期记忆召回。02b 先验证 fixture，03c/05b 补真实 Runner、PG 与 File API 证据。

| 编号与建议用例名 | 设置与执行步骤 | 必须断言与证据 |
|---|---|---|
| P0-S01 / test_same_user_sessions_restore_independently | 同用户创建两个 Standalone Session，分别执行并提交不同标记的会话/小文件；关闭 Runner，在另一 Worker 的新私有目录分别恢复，执行固定第二片段 | 恢复的 Conversation 包含自身已提交消息、tool_call_id 与结果关联，不包含另一 Session 标记或历史；文件摘要、Scope、session_id 正确；一方推进不改变另一方引用；保存导出、恢复状态及第二片段输入的结构化断言，不凭模型回答判定 |
| P0-S02 / test_workspace_sessions_single_writer_preserves_refs | 同用户、同 Workspace 的 s1/s2 预置已提交 Conversation 引用；提交两个 latest_at_claim 写 Run，以屏障让两个 Worker 竞争；胜者活动期间重复领取另一 Run，再提交、结束并确认清理，随后执行另一 Run | 不预设谁获胜；竞争期间仅一个有效写 Attempt 和对应 active_run_id，另一 Run 保持 QUEUED；提交仅更新当前 session→conversation 引用，另一会话引用/摘要可解析且内容不变；结束和清理后另一 Run 可领取，base_revision 为前次提交版本；第二次提交也保留第一会话引用；新 Worker 恢复各会话时加载正确历史 |

P0-S02 使用受控并发屏障与 PG 状态快照证明没有重叠写入，不能以串行请求代替竞争测试，也不能直接改表使第二 Run 可领取。引用校验同时覆盖 Manifest 映射和 FileRef 内容，只使用合成测试数据。同 Workspace 共享项目文件是预期行为；这里保证 Conversation 选择和引用保留正确，不声称同 Workspace 的文件彼此保密，Memory 共享语义另验。

建议落在 tests/integration/test_session_isolation.py（fixture/映射与单写事务）和 tests/recovery/test_session_restore.py（真实 Runner 跨 Worker 恢复），文件名实施时可等价调整。P0-06 报告按两个编号分别记录模式、版本、输入、结果与证据，fixture PASS 不替代 native PASS。P0-S01/P0-S02 为必需项，失败阻止对应 G3/G5 通过。

#### 20.3.4 出口门槛与范围排除

| 门槛 | 必需证据 |
|---|---|
| G1 环境可复现 | Python 3.12.11、锁定依赖、源码指纹、独立角色启动与环境重建说明 |
| G2 控制/执行解耦 | 实际 HTTP、身份/版本校验、协议样本、无 Worker 业务 DB 权限/ORM 依赖或跨角色共享路径 |
| G3 真实 Runtime | 03c 和 05b 的执行—导出—关闭—新 Worker 恢复成功；P0-S01/S02 各会话恢复正确，不能用 fixture 结果代替 |
| G4 本机边界 | 03a/03b 越界拒绝、环境/FD、未授权网络、输出和清理测试；必须能力失败则阻塞 native 路径 |
| G5 持久协议 | 真实 PG、持久 File API 元数据、单写与条件提交；P0-F01—F04 及 P0-S01/S02 的隔离、竞争与引用保留断言 |

G1—G5 全部通过且 04/06 报告齐备，才可宣布 P0 完成；单次 happy path 演示不够。报告每项 PASS/FAIL/BLOCKED/UNSUPPORTED，BLOCKED 为缺前置，UNSUPPORTED 为后端不具备能力，均不算通过。macOS 资源硬隔离等已明确不属于 P0 门槛的 Linux 能力可以 UNSUPPORTED；整个 Runner 的必需 Seatbelt 边界或真实 PG 缺失则 P0 不得完成，只继续独立工作。未启用工具不执行其生产验收，不以禁用记录冒充 PASS。

本期不建设：Java Core、第三方调度框架、完整公平调度/计费、产品级 SSE/BFF、Workspace 全量 CRUD、WAITING_INPUT 与暂停产品链路、通用工具账本/外部副作用恢复、生产 ReMe 集成、生产对象存储及运营 GC、多槽、Linux 生产强隔离验收。DeepSeek 真实联调和模型网关为主线完成后的可选实验，不影响离线 P0 出口；真实端点、模型 ID、凭据和预算未落实时不执行该实验。不实现这些产品功能不允许省略 P0 用例已有的 tenant、授权、租约、revision 或文件封存约束。

最终交付包含源码/依赖指纹、OpenAPI 与契约样本、独立启动配置、最小 PG 迁移、确定性模型与输入、故障注入脚本、G1—G5 报告和 P1 差距清单。P0-01 先复核环境记录；历史只读核查不等于环境已安装。PG 版本/DSN/建测试库权限在 05a 前落实；Linux 访问在 P1 验收前落实，均不阻塞不依赖它们的步骤。

<a id="s21"></a>
## 21. 验收矩阵

每个用例应记录版本、沙箱后端、宿主配置、输入、预期/实际结果和证据。负向隔离测试与正向可用性测试同等重要。表中门槛为设计要求，当前没有声称已经通过。

| 编号 | 场景 | 必须满足的结果 |
|---|---|---|
| A01 | 两用户使用相同 Agent/Session ID、文件名 | 所有数据、检索、事件和产物分域 |
| A02 | 同用户两个工作区分别存放秘密文本 | 另一工作区的记忆和检索不能召回 |
| A03 | 同工作区两个 Session 同时提交写任务 | 一个执行，另一个排队，不双写 |
| A04 | 网页上传/删除发生在 Run 执行中 | 遵循版本和写入占用规则，不悄悄修改活动目录 |
| A05 | 同 Pod 两个槽并行访问 `/workspace` | 只看到各自映射，不看到父目录和其他槽 |
| A06 | Python 绕过内置文件工具访问外部路径 | 仍被系统边界限制 |
| A07 | 文件工具在进程内执行 | 其能力明确，不能冒充更窄子沙箱；需要时迁移 |
| A08 | Agent 访问 `/state`、Supervisor 配置和凭据 | 工具不能访问未允许状态，任务不能访问平台资源 |
| A09 | 链接、路径穿越、魔术链接与打包竞态 | 导入、导出和产物收集不越界 |
| A10 | 恶意归档、过多文件、过大解压内容 | 在限额内拒绝，不污染可信环境 |
| A11 | 读取父进程环境、FD、管理 socket | 无法取得未授权能力 |
| A12 | 修改审批模式、sandbox_enabled 或 policy 文件 | 不能关闭外层强制隔离 |
| A13 | 沙箱后端/资源限制不可用 | 明确失败，不降级普通进程 |
| A14 | 单槽触发内存/进程/磁盘限额 | 按定义限制，其他槽尽可能不受影响；记录实际故障边界 |
| A15 | Runtime 调用模型流、Embedding、批准 MCP | 功能正常，错误码与取消可传递 |
| A16 | 断网工具尝试直连 IP、取消代理、换客户端 | 不能绕过出站策略 |
| A17 | 访问云元数据、DB、其他 Worker、内部管理面 | 被拒绝；指定内网模型例外仍正常 |
| A18 | 浏览器重定向、解析变化、IPv6 等 | 不绕过批准的出口范围 |
| A19 | 任务独立网络后的 localhost、DNS、控制通道 | 行为明确；事件/取消/续租链路仍正常 |
| A20 | 同 Pod 两槽尝试访问彼此服务 | 执行级隔离生效，不依赖单一 Pod NetworkPolicy |
| A21 | Run 已入库但创建响应丢失 | 相同幂等键返回原 Run |
| A22 | 相同幂等键提交不同请求 | 冲突，不复用错误输入 |
| A23 | 网页/BFF/API 断线或重启 | 已接受任务不丢；网页按游标补读 |
| A24 | 事件上传重试、补读/实时切换竞态 | 去重且不漏事件，Run 序号单调 |
| A25 | SSE 客户端持续慢速消费 | 内存有界；权威事件与消息快照可补读，暂态缺失明确提示 |
| A26 | Worker/Sandbox 被 SIGKILL 或 OOM | 从已提交边界由兼容 Worker 恢复 |
| A27 | 旧 Worker 失联后恢复并提交 | 旧代数不能更新 head、权威事件或终态 |
| A28 | 对象上传成功、数据库提交失败 | head 不动，孤儿对象可安全回收 |
| A29 | 数据库 commit 成功、响应丢失 | 幂等查询返回既有提交，无矛盾状态 |
| A30 | 活跃本地数据库进入快照 | 使用一致性导出，不恢复损坏状态 |
| A31 | 外部工具已成功、结果未落库 | 查回/对账/等待确认，不盲目重复 |
| A32 | 文件工具结果已记录、文件未进检查点 | 恢复文件增量或重放，不能仅跳过工具 |
| A33 | 等待输入很久后由另一 Worker 继续 | 不占计算槽，工作区逻辑占用和版本保持明确 |
| A34 | 取消时仍有浏览器/Shell/MCP 后台进程 | 沙箱级清理；远程不可撤销操作明确记录 |
| A35 | 用户 A 修改 HOME/安装依赖后执行用户 B | B 无 A 的可写环境、profile、凭据或进程残留 |
| A36 | 清理失败 | 槽被隔离，不能继续接任务 |
| A37 | 恢复前后 Token/时间/步骤预算 | 累计一致，不能靠重试清零 |
| A38 | 模型 429、重复错误或长期无进展 | 有背压、退避、预算和退出条件 |
| A39 | 新旧 Runner/Skills/checkpoint 版本并存 | 只允许兼容恢复，不静默换执行语义 |
| A40 | 产物上传失败或引用校验失败 | 不出现虚假的“成功且可下载” |
| A41 | 备份恢复与对象 GC | 引用完整，活动/待恢复状态不被误删 |
| A42 | 多槽持续压测、重复取消与恢复 | 无持续资源泄漏，输出实测吞吐与延迟而非估算承诺 |
| A43（P0/P2） | Core 与 Worker 独立启动；P2 分机且无共享目录 | Worker 无业务 DB 权限和 Core ORM 依赖；输入/状态通过 HTTP 与 FileRef 传递 |
| A44（P0/P2） | claim/续租响应丢失、Core 重启和网络隔离 | 重复 claim 不重复分配；watchdog 按保守期限停止；迟到续租不复活 Attempt；旧提交拒绝 |
| A45（P2） | 伪造 Worker、跨分配调用和协议不兼容 | 验证服务身份与分配关系；拒绝越权、未知必需能力和不兼容版本 |
| A46（Java 切换前） | 新 Core 运行相同契约与并发故障用例 | 状态/幂等/租约/提交语义一致；旧写入者已撤销，无双调度权威 |

### 21.1 双 Scope、生命周期与 Memory 补充验收

| 编号 | 场景 | 结果 |
|---|---|---|
| B-A01 | 无 Workspace 多轮对话 | 不隐式创建 Workspace；Conversation 与附件连续 |
| B-A02 | 同一 Standalone Session 并发写 Run | 串行；不因 workspace_id=NULL 而双写或失去幂等 |
| B-A03 | Standalone 长任务中途故障 | 必要中间文件随检查点恢复，临时未提交内容不虚假续用 |
| B-A04 | Workspace 创建/Reset/Delete | 不绑定执行资源；维护 operation 阻止新领取，引用正确释放 |
| B-A05 | Reset 当前 Memory 后读取旧快照 | 产品解释历史保留；不声称立即物理擦除 |
| B-A06 | Memory 新增后跨 Worker 继续 | 使用相同已提交 Files/Conversation/Memory 版本组合 |
| B-A07 | Session 删除 | 按约定只删对话，不误删已提交 Workspace Memory |
| B-A08 | 可选 Promote | 固定转换前后 Scope，选定文件保留，无活动写入竞态 |

文件服务接口、引用与迁移专项验收见第 24 节。

### 21.2 最低可用性测试样本

至少覆盖：纯模型对话、带历史继续对话、文件读写、Python 分析、Shell 子进程、本地 stdio MCP、远程 HTTP MCP、浏览器访问与下载、长时工具调用、人工输入、多轮恢复、最终产物生成。禁用的能力需在对外接口明确，而不是请求后进入无限等待。

<a id="s22"></a>
## 22. 待验证项、风险与上线前决策

### 22.1 尚未确认的工程参数

| 待确认项 | 必须回答的问题 | 完成节点 |
|---|---|---|
| 最终上游版本 | 哪个 commit、依赖组合和镜像作为基线？ | P0 |
| Runner 接入 | 初始化、状态导出、恢复、关闭的真实入口是什么？ | P0 |
| P1 目标平台 | Kylin V10（Tercel）的 SP、内核、架构、容器运行时和 cgroup 模式是什么？ | P1 |
| Pod 安全基线 | 非 root、禁止提权、丢弃 capabilities、显式 seccomp、只读根和禁用 host 能力能否落实？ | P1 |
| 网络方案 | CNI 是否实际执行 NetworkPolicy；允许服务、端口、DNS 与受控出口如何落地？ | P1 |
| 工具支持矩阵 | 哪些工具可取消、可重放、需对账或必须关闭？ | P1/P3 |
| 持久状态后端 | QwenPaw 会话、记忆和本地数据库如何一致导出？ | P0/P3 |
| 单槽/单 Pod 资源 | 各任务 profile 的内存、CPU、进程数、临时盘限制与容量？ | P1 固定并验收单槽限额；P4 验证容量/多槽 |
| 业务时限 | 租约、续租、Run deadline、等待输入和恢复期限？ | P2/P3 |
| 文件与事件保留 | 附件、快照、产物、日志、事件、孤儿对象保留多久？ | P4 |
| 发布与恢复目标 | 可接受的等待时间、恢复时间与未提交工作损失范围？ | P3/P4 |
| L3 范围 | 哪些 Agent/工具模式确实支持逐工具边界恢复？ | 单项验证后开放 |

### 22.2 重点风险及缓解

| 风险 | 缓解 |
|---|---|
| 把 Pod 资源限制误认为不可信代码沙箱 | 对外结论限定为可信任务；未来不可信用户必须新增强沙箱阶段 |
| 默认拒绝网络后 Runtime 断网 | 显式提供模型/控制/DNS 通道，并做正向测试 |
| Runtime 网络权限被工具共享 | P1 明确接受可信用户下的残余风险；不自动开放高风险工具，Pod 出口按最小允许列表收敛 |
| 单 Pod 资源影响节点其他负载 | 单 Slot、CPU/内存/临时盘 limits、kubelet PID 限制和真实超限测试 |
| 快照文件与会话/记忆不一致 | 安全边界、一致性导出、原子 head 提交 |
| 恢复重复外部操作 | 稳定 operation_id、账本、幂等、对账和 UNKNOWN 状态 |
| 同槽后续用户读到残留 | 整体销毁可写环境、资源撤销、失败槽隔离 |
| 高权限导入/导出器被链接利用 | 受限暂存、安全路径处理、静止导出、规模与类型校验 |
| 上游升级改变恢复语义 | commit/digest/profile/schema 固定，兼容池和回退演练 |

安全保证针对可信用户威胁模型与经过验证的策略，不覆盖恶意租户、容器逃逸、未知内核漏洞、平台管理员访问或外部服务失陷。不能承诺“绝对不会越界”；应提供实际配置、测试证据、补丁与运行监测。

<a id="development-decisions"></a>
### 22.3 当前环境与待落实决定

2026-09-11 只读核查：源码 HEAD 为 `2b09de79c37735b75c65c839705869d9ed040f45`；本机 macOS 26.6.2 / arm64；uv 与 sandbox-exec 可找到。默认 /usr/bin/python3 是 3.9.6，不满足项目要求；uv 已安装 3.12.12/3.13.12，符合 pyproject.toml 的 >=3.11,<3.14 范围，但 .python-version 指定 3.11，本次未发现已安装 3.11 或可执行的仓库 .venv。未运行依赖导入/Runner/沙箱执行测试。

psql/pg_ctl 未出现在当前 PATH；这不等于本机或远程一定没有 PostgreSQL，本次未探测服务与连接。模型凭据、数据库 DSN、真实用户文件均未读取。下表区分工程实现者可以直接采用的默认方案与需要用户/平台提供的接入信息，避免把所有问题都当作开工确认。

| 决定 | 当前默认/需要的信息 | 最迟完成时间 | 是否阻塞现在开工 |
|---|---|---|---|
| 源码/工程布局 | P0 固定上述本地 commit，加记录的工作树差异；新增适配层，少量上游补丁；不等待新版本 | P0-01 | 否，已有默认 |
| Python 与依赖 | 已确定 3.12.11；P0-01 将当前 .python-version 的 3.11 对齐，建立独立环境与可复现依赖锁定；不使用系统 3.9 或隐式替换为已安装的 3.12.12 | 真实导入前 | 不阻塞写代码；阻塞未配置环境的真实执行 |
| 本地运行根与测试身份 | 使用项目外专用私有开发根与合成 tenant/user；生产目录禁止；测试 BFF 使用独立测试签名密钥/证书，仍验证签名/范围 | P0-01/P0-03 | 否，实施者可按默认选择并记录 |
| PostgreSQL 接入 | 需要本机原生测试实例或专用远程测试 DSN，以及版本/建测试库权限；版本以目标生产兼容范围固定，不以 SQLite 替代 | P0-05a | 不阻塞 01—03 与 Memory 旁路；阻塞 05a/05b 和 G5 |
| 真实模型 | 已确定 DeepSeek；具体 model ID、思考模式、官方/其他部署端点、测试凭据、小额预算仍需落实，并验证受控模型网关路径；默认先用离线模型桩 | 真实模型联调前 | 否 |
| Memory | none 先行；remelight 为首个持久候选，需验证依赖、导出和关闭；不一次开放全部插件 | P0-04/P3 | 否，候选已有默认 |
| P1 Linux 环境 | 已确定 Kylin Linux Advanced Server V10（Tercel）与 Kubernetes v1.21.7；仍需 SP/内核/架构、容器运行时、CNI、kubelet PID 配置、资源数值、允许网络端点和测试访问 | P1 验收前 | 不阻塞代码/清单骨架；阻塞真实环境验收 |
| BFF 生产身份与 Tenant 映射 | 签发者、audience、服务身份/证书、tenant/user 来源和测试用例；本机先用测试签发者 | P2 正式集成前 | 否 |
| 生产存储/容量与时限 | OSS/S3 驱动、配额/保留期、等待/租约预算、目标并发和 RPO/RTO；local_test 仅功能验证 | P2/P3，对应验收前 | 否 |

待提供信息中的凭据通过受控配置注入，不写进设计文档。开发者应先完成已有默认的步骤；只有某一步实际依赖未提供的信息时才暂停该步骤。本次文档更新不包含安装依赖、连接数据库或开放真实外部工具。

<a id="s23"></a>
## 23. 给后续实施者的约束摘要

> 以下内容可直接作为后续实施任务的架构约束。具体代码仍需依据固定版本验证。

1. 建设的是普通多用户 Agent 任务服务，K8s 只是 Worker 的部署载体；不按 Run/用户创建 K8s 工作负载，不建设 Operator/CRD。
2. 不实现登录认证授权系统；所有数据和能力绑定可信用户与 Session/可选 Workspace 范围，不能由模型扩权。
3. 共享 Harness API 与 PostgreSQL 任务事实；Worker 主动领取；BFF 不知道 Pod，不保存唯一执行状态。
4. P0—P3 一个常驻 Worker Pod 一个 Slot，P4 多槽另验；一个 Slot 同时执行一个 Attempt；P1 每次新建 QwenPaw 进程与私有可写环境，不要求任务级强沙箱。
5. P1 面向可信用户，以 Pod/container 资源、安全上下文、Pod 网络策略和 Attempt 清理为边界；不得把结果表述为不可信任意代码强隔离。
6. Runtime 保留模型与本任务控制通道；P1 同 Pod 工具会共享网络权限，故不自动开放高风险工具；网络或资源必需限制不可用时拒绝领取。
7. 工作区持久内容通过 File Service 保存，本地只是 Attempt 副本。经 File API 上传、校验、保留引用后，以有效 Attempt/租约/版本条件发布 Head。
8. Workspace 写入按工作区串行，Standalone 按 Session 串行；默认无跨工作区记忆、缓存和文件共享。
9. Run 独立于 HTTP/SSE。异步提交、事件持久化、重连补读、显式取消和人工输入必须有明确契约。
10. 长程恢复以已提交逻辑边界为基线，不恢复原进程；工具副作用有稳定操作标识和对账，未知结果不得盲目重试。
11. 不把平台 DB/Bucket/Worker 管理凭据放进 Runner；Supervisor、Storage Broker、导入导出和内部协议本身也需最小能力和安全校验。
12. 优先采用新增适配层和少量上游补丁；不先全面云化 Hub，不强制引入工作流引擎。P1 同时交付单槽资源、清理、网络和回归证据；多槽留到 P4。
13. 文件模块仍归 Harness，生产数据面独立部署。首期经 File API 中继；后续可显式启用受限直传授权，Core/业务清单不保存定位信息，Worker 不持有全局凭据。
14. 文件服务即使首期共库部署也独占文件元数据和事务；后期迁到 BFF 时保持 FileRef、版本、幂等和引用集合语义不变。工作区与 Memory 业务语义不迁出 Harness。
15. 不通过无限反向回调实现文件服务。以持久引用、提交意图和 outbox 对账协调；后端不可用时不绕过 File API。

**当前 P1 可交付基线不是“QwenPaw 成功运行在 Pod 中”，而是：在明确的可信用户前提下，多用户顺序执行不串域、Pod 资源和网络限制真实生效、Attempt 清理可确认、失败 Worker 不复用、单槽容量经过实测。任务级强沙箱与不可信任意代码支持不在本阶段。**

---


<a id="s24"></a>
## 24. 文件服务边界、契约与迁往 BFF 的路线

### 24.1 当前决定与不变项

当前选择“模块化单体”：File Service 作为 Harness 的独立模块部署，不立即建设外部文件微服务或迁到 BFF。文件系统解耦包括两层：**调用方与文件服务解耦；文件服务与具体存储系统解耦**。

```text
Harness Core / Worker
        ↓ FileGateway / FileApiClient
File API + File Application Service
        ↓ BlobStorePort
OSSAdapter / S3Adapter / 其他经验证的持久内容驱动
```

上层契约面向文件上传、版本、内容流和引用，不模拟完整远程 POSIX 文件系统。工具仍在本地磁盘操作；文件系统操作由 Sandbox/WorkspaceMaterializer 管理。

只迁移用户附件而保留 checkpoint、Memory 导出和产物直连存储会留下耦合；这些内容必须全部经过同一文件契约。模型调用、MCP 出口和 Harness 任务数据库不包含在本次文件服务迁移中。

### 24.2 契约依赖规则

Core 依赖 `FileGateway`，Worker 依赖 `FileApiClient`。可用 `InProcessFileGateway` 调用同应用内文件应用服务，也可用 `HttpFileGateway`，但两者通过同一套契约测试；调用者不能携带 Core ORM 会话参与文件事务。Worker 从首期用 HTTP，这样它和当前/未来文件服务的边界一致。

存储驱动仅在 File Service 初始化入口配置。Core 不能通过依赖注入取得 BlobStorePort；Supervisor 不能为了性能直接下载某个对象 Key。配置不能在失败时回退到直连 provider。

File Service 独立拥有 `files` schema/migration/Repository；可暂时与 Core 共数据库实例，但禁止跨模块联表、共享实体、外键级联和联合写事务。未来迁移的是文件服务的代码、路由、文件元数据/上传记录/保留引用和驱动配置，不只是改一条 URL。

### 24.3 拟新增 HTTP File API

固定版本前缀示例：`/internal/file-api/v1`。首期由 Harness 挂载，后期由 BFF 文件模块挂载，调用者只改变服务地址/适配器。以下全部是新增契约，不是 QwenPaw 原生 API。

| 接口 | 语义 |
|---|---|
| `POST /uploads` | 按固定 Scope、用途、大小预算与幂等键创建上传会话 |
| `GET /uploads/{upload_id}` | 查询在途上传状态和已完成结果，供超时重试使用 |
| `PUT /uploads/{upload_id}/content` | 经文件服务流式上传；V1 可整文件重传，大文件分片另定可选扩展 |
| `POST /uploads/{upload_id}/complete` | 校验持久内容，幂等生成不可变 READY FileRef |
| `POST /uploads/{upload_id}/protection:renew` | 可信协调器续期尚未 pin 的上传内容保护；complete 后仍保留有限保护期，不作为永久引用 |
| `DELETE /uploads/{upload_id}` | 幂等中止未完成上传，不删除已被发布的 FileRef |
| `GET /files/{file_id}/versions/{version}` | 查询确定版本的元数据，不暴露 provider locator |
| `GET /files/{file_id}/versions/{version}/content` | 直接返回文件流，禁止重定向到存储系统 |
| `PUT /reference-sets/{reference_set_id}` | 幂等建立并封存一组完整 FileRef，成员未 READY 则拒绝封存 |
| `GET /reference-sets/{reference_set_id}` | 查询保留状态与成员摘要，用于提交恢复和对账 |
| `DELETE /reference-sets/{reference_set_id}` | 幂等释放引用；仅协调器具备能力，不提供给普通 Agent/Worker |

大清单可使用“追加批次→封存”扩展；封存后成员不可修改，同 ID 不同摘要必须冲突。文件服务只认可封存集合为提交保护依据。release 后相同 ID 不重新复活，需新 generation/新 ID，避免迟到请求重新删除新的保留关系。

为处理补偿先到而 pin 后到，DELETE 对尚不存在的 reference_set_id 也写入释放墓碑；迟到 PUT 必须拒绝。该能力与 pin/GC 互斥在 P2 验收。

文件上传状态建议为 `CREATED → UPLOADING → VERIFYING → READY`，失败/中止/过期明确记录；幂等请求同键不同内容拒绝。文件 READY 后不能原地覆盖 bytes。

元数据示例：

```json
{
  "file_id": "file_001",
  "version": "fv_01",
  "state": "READY",
  "name": "研究报告.pdf",
  "kind": "user_upload",
  "media_type": "application/pdf",
  "size": 2468123,
  "digest": {"algorithm": "sha256", "value": "REQUIRED_REAL_DIGEST"}
}
```

Scope 包含必填 tenant_id、user_id、scope_type/id、用途及调用方被授予的本次操作范围；由可信平台注入而非模型填入。内部文件读取不得依赖浏览器在线会话。未通过完整性/归属检查的文件不能作为 Run 的 READY 输入。

### 24.4 首期两条数据路径

```text
用户上传：
网页 → BFF（转发）→ Harness File API → FileService → 存储驱动
完成后返回 FileRef；任务创建/Workspace 导入只传引用，不再次上传字节。

任务读写：
Supervisor → FileApiClient → Harness File API → 存储驱动
输入/快照下载 → 本地校验和物化 → 沙箱使用。
安全导出 → File API 上传 → READY FileRef → Core 保留与业务发布。
```

不把文档解析放在高权限文件模块内执行；文件服务处理字节和元数据，正文提取、OCR、切块/向量化由受限任务按需处理。输入原件与派生内容各自有不可变引用。

### 24.5 不能偷用同数据库事务

文件服务当前虽然在 Harness 项目内，仍应按可远程失败的服务设计。Core 的 `file_commit_operations` 持久记录 PREPARING/PINNED/COMMITTED/ABORTED；文件服务独立维护引用集合，两者按第 10.5 节协议推进。

| 故障窗口 | 应有行为 |
|---|---|
| 上传成功但 complete 响应丢失 | 相同 upload_id 查询/重试，返回同一 FileRef |
| 文件 READY 但 Run 未接受 | 在途 operation 保护；未引用内容之后按策略回收 |
| reference-set 封存但 Core 尚未提交 | 暂时保留，允许查询/重试；不能超时即删 |
| Core 提交成功但客户端没收到响应 | 由 commit_id 查回已提交结果，不重复建立业务版本 |
| Core 已释放业务引用但文件服务暂时不可用 | outbox 重试，内容多留而不是悬空 |
| 文件服务不可用 | 暂停物化/检查点发布，受限重试；不得变为直连 SDK |

对账清理必须先将未决 Core operation 以条件更新推进 ABORTED，确认无已发布引用、无可再提交的执行者，再释放保留集合。不能只按“当前数据库没有这条 Head”决定删除，也不要求文件服务每次读取都反查业务库。

### 24.6 Streaming、容量与错误契约

文件内容采用二进制流和有界缓冲，不能 Base64 进 JSON 或整体读入内存。大文件并发、存储请求、网页请求和 Run API 使用分别受控的连接/资源预算；指标区分传输耗时与 Agent 执行耗时。

开发/PoC 可与 Core 同进程；首期生产 File API 采用独立进程/实例组及资源、连接预算，避免字节中继影响续租、Claim 和事件。仍归 Harness 文件模块维护，不要求先迁 BFF；Core 生产通过 HttpFileGateway 调用，InProcess 仅开发与契约测试。

超时、重试、取消、摘要校验和错误码必须统一：`FILE_NOT_READY`、`FILE_VERSION_NOT_FOUND`、`SCOPE_MISMATCH`、`UPLOAD_INCOMPLETE`、`CONTENT_DIGEST_MISMATCH`、`REFERENCE_SET_CONFLICT`、`FILE_SERVICE_UNAVAILABLE`。具体状态码和 Range 支持在 OpenAPI 冻结时确认；不假定所有存储都实现相同分片/锁语义。

文件版本与完整性信息由服务端确认。客户端声明的 mime、size、digest 只是待校验输入。Artifact 下载、内部 Memory/Conversation 导出和运行配置必须按用途区分，不能给网页一个列举所有 FileRef 的通用接口。

**后续可选：受限直传模式。** 此模式需要显式启用与独立验收，不改变 FileRef、pin、Head 提交和 GC 事实源。新增版本化 transfer-grant 契约，由 File Service 按已验证 tenant/scope/Attempt、确定文件版本或 upload_id、单对象、GET/PUT、短 TTL、必需校验头及大小预算签发。Core 和 Runtime 不接收 grant；只有统一 FileApiClient 在传输期间使用，并校验批准的存储端点，不任意跟随重定向。不向 Worker 发放可列举 Bucket 的广泛 STS 凭据或允许其自己签名。

预签名 URL 是可转用且可能在有效期内重复使用的临时能力，不能当作一次性上传、立即可撤销权限或不可变版本保证。[S27] PUT 仅指向独立暂存对象；complete 服务端核验大小/摘要后，发布到客户端无法覆盖的最终对象，或绑定经验证的不可变存储 version。迟到/重复 PUT 不得改变任何已 READY FileRef 内容。摘要不得以多段上传 ETag 直接代替；校验与存储版本实现按 provider 验收。

grant 绑定 Attempt 不表示对象存储会实时检查租约；失租后拒绝新授权/complete/业务发布，已授权在途请求可能仍完成并成为待清理暂存内容。不能承诺 TTL 到期立即中断已有传输；若业务要求即时撤销/更严格隔离则维持中继模式。续期需重新鉴权与检查预算，日志、事件和快照不得保存 URL/签名。默认期限由配置和传输预算决定，不把固定十分钟当通用安全值。

Core/Worker 始终依赖 FileGateway/FileApiClient，不导入 provider SDK、不自行拼对象 Key。后续客户端可解释不透明 grant 并执行标准 HTTP 传输，但不把 locator 写进 Run/Manifest/业务数据库。文件服务故障不自动切换直传或使用旧授权绕过未通过的 complete；传输完成仍须 READY→pin→Core commit 后才可见。浏览器直传涉及 CORS/暴露面，另行设计，不能直接复用内部 Worker 能力。

开启前演练：授权过期与续期、失租后在途上传、重复 PUT 覆盖尝试、服务端校验失败、越权 FileRef、错误端点、签名泄露日志检查、complete 响应丢失。压测对比中继与直传的带宽、TLS CPU、内存、P95/P99 心跳/claim 延迟；不能仅凭 I/O 理论推断必须引入直传。

### 24.7 迁往 BFF 的步骤

1. **首期就冻结 File API/DTO/Error/幂等语义。** Core 与 Worker 不导入 provider SDK、不读 files schema。建立同一套进程内与 HTTP 契约测试。
2. **提供 BFF 的等价 File API。** 迁移整个文件模块，保持 file_id/version、upload_id、reference_set_id、已完成幂等记录不变；底层对象未必需要搬迁。
3. **迁移或重新归属文件元数据。** 可以先保持同一独立 files 数据库/schema，由新的服务单独拥有写入权；若换库，需快照/增量同步、校验和写入切换方案。不能忽略上传会话和引用集合。
4. **确定单一写入权。** 按分区/迁移批次设定一个 writer；首期建议明确维护窗口或受控转发。禁止 BFF 与 Harness 文件模块无协调地各自写一套状态。长上传要排空或转交会话，不能中途丢失。
5. **切换调用。** Worker 改 File API base_url，Core 从 InProcessFileGateway 切为 HttpFileGateway；业务协议、FileRef 和 checkpoint 清单保持稳定。
6. **演练旧任务恢复。** v5 内置文件模块产生的输入、Memory 和 checkpoint，在 BFF 文件模块下完整读取、续跑、释放与 GC。
7. **再停旧模块。** 新地址验证通过并完成日志、引用、未完成上传对账后关闭旧文件路由；保留受控回滚，回滚同样保持单 writer 和完整引用状态。

因此，“代码层面可更换适配器”不等于“生产只改 URL”。迁移必须覆盖状态、在途请求、写入归属、容量和回滚；否则会出现文件存在但引用丢失的恢复故障。

BFF 中网页业务接口可以调用 Harness；BFF 内部文件接口只能进入文件模块/存储，不能反向等待当前 Agent Run，否则会形成业务调用环。

### 24.8 首期工程任务（独立编号）

| 编号 | 内容 | 完成条件 |
|---|---|---|
| FS01 | file_contracts 与 OpenAPI | FileRef/Scope/Metadata、错误、幂等、版本冻结，无 provider 字段 |
| FS02 | 内置 File Service | 独立 application/domain、files schema/migration/Repository |
| FS03 | BlobStorePort 与驱动 | SDK 仅在驱动；测试驱动不作为生产持久保证 |
| FS04 | FileGateway/FileApiClient | Core 可进程内，Worker 必 HTTP；同语义契约测试 |
| FS05 | Upload/complete/引用集合 | READY 不变；封存成员完整；重试/释放幂等 |
| FS06 | FileCommitCoordinator/outbox | 先 pin 后业务提交，未决对账不误删 |
| FS07 | 全部文件路径替换 | 输入、产物、工作区、Memory、Conversation、Manifest 均走 File API |
| FS08 | 安全流传输与用途限制 | 有界缓冲、Scope、重定向禁止、路径安全、内部内容不公开 |
| FS09 | 引用 GC | 不直接按 Workspace prefix 删除；在途/保留集合不误清理 |
| FS10 | 双部署拓扑测试 | File Service 在同应用与独立进程下运行相同测试 |
| FS11 | 模块依赖与配置审计 | 无存储 SDK/全局凭据/files 表访问；业务记录无 locator；可选 grant 仅客户端短暂使用 |
| FS12 | BFF 迁移演练 | 稳定旧引用/版本，单 writer，在途上传、恢复、回滚有记录 |
| FS13（后续可选） | 受限直传授权 | 单对象/动作/TTL/完整性约束；暂存与不可变发布；重复 PUT 不改变 READY 版本；Core/Runtime 无 locator/grant |

FS01—FS11 属于首期模块解耦验收；其中核心提交和生产数据面隔离在 P2 完成。FS12 的测试计划首期具备，真正迁入 BFF 属于后续；FS13 为按需演进，不作为启动前置。

### 24.9 专项验收

| 编号 | 用例 | 必须满足 |
|---|---|---|
| F-A01 | 扫描 Core/Worker 的依赖及文件调用 | 无 provider SDK/全局凭据/files 联表；业务记录无 locator；后续 grant 仅统一客户端暂存且不入日志 |
| F-A02 | 网络仅允许 Worker 访问 File API | 输入、产物、Memory、快照完整跑通，无存储直连 |
| F-A03 | 存储驱动替换 | File API/FileRef/业务清单不变 |
| F-A04 | 文件模块同进程改为独立 HTTP 服务 | 相同语义与错误，无同库事务依赖 |
| F-A05 | 上传/complete/pin 返回丢失 | 幂等查回，不生成不同 FileRef/集合 |
| F-A06 | 用户覆盖同名文件 | 旧 Run 读取固定版本而非新内容 |
| F-A07 | pin 成功、Core commit 失败 | 活动/未决内容不被回收，确认 ABORTED 后补偿 |
| F-A08 | Core commit 成功后网络中断 | 返回已提交状态，继续保留完整引用闭包 |
| F-A09 | Manifest 引用大量嵌套文件 | 不只保留 Manifest；GC 不删子文件 |
| F-A10 | 删除 Workspace/File/Session | Core 决定语义，File Service 按有效引用 GC，不误清理 Memory 历史 |
| F-A11 | 文件服务不可用/返回跨主机重定向 | 可重试或拒绝，不绕过接口直连底层 |
| F-A12 | 大文件/慢上传与 SSE 同时存在 | 内存和并发有界，不阻塞租约/控制通道 |
| F-A13 | Standalone 长任务恢复 | Session、输入、中间文件都有有效保留引用 |
| F-A14 | 切换 BFF 后恢复旧检查点 | FileRef、版本、reference-set 和 Memory 连续 |
| F-A15 | 双端迁移与回滚 | 无双 writer，未完成上传/释放消息不丢失 |
| F-A16（可选直传） | 重复 PUT、失租、过期与校验失败 | READY 内容不可覆盖；临时授权不扩大作用域；无租约业务发布仍拒绝 |
| F-A17（可选缓存） | 命中他域摘要、修改工作副本、缓存损坏 | 仍校验授权；不能写穿共享 inode；损坏重取，不改变持久事实 |

### 24.10 本次最终边界

> **当前：文件服务归 Harness 维护，生产采用独立数据面进程/实例组。**
>
> **依赖：Harness Core/Worker → 文件契约 → File Service → 存储驱动。**
>
> **未来：将 File Service 与其文件元数据、引用生命周期整体迁到 BFF；Harness 的任务、Workspace/Session 和 Memory 语义留在原处。**
>
> **始终：Agent 使用当前沙箱本地文件，不取得存储传输授权；Worker 首期经 File API 中继，后续仅可经统一客户端使用文件服务签发的受限传输授权。**


<a id="sources"></a>
## 来源、核查范围与参考资料

本文在既有 v4 文件与本次对话基础上整合，并新增文件模块先内置 Harness、后迁 BFF 的边界决定；来源文件为 QwenPaw_Multiuser_Harness_Implementation_Baseline_v4.md。本次是方案整理，不宣称实现已存在或迁移已完成。上游事实沿用对话中读取的固定标签源码；未在本次文档整理中重新运行 QwenPaw、复查远端最新版或进行部署压测。以下链接供实施时核对；官方通用文档可能随版本变化，应以最终选定平台版本再次验证。

文中 `[Sxx]` 指向公开的一手资料；业务架构、数据表、API、模块与配置是本项目的设计建议，不代表 QwenPaw 原生实现。

| 引用 | 一手资料与用途 |
|---|---|
| [S01] | QwenPaw v2.2.0 `constant.py`：模块级工作目录、私密目录和环境初始化 |
| [S02] | QwenPaw v2.2.0 `app/task_tracker.py`：进程内运行状态与事件缓冲 |
| [S03] | QwenPaw v2.2.0 REST API 教程：原生聊天接口与 Agent/Session 标识 |
| [S04] | QwenPaw v2.2.0 `checkpoints/hooks.py`：自动快照触发边界 |
| [S05] | QwenPaw v2.2.0 `runtime/phases.py`：外层生命周期与 Agent loop 分层 |
| [S06] | QwenPaw v2.2.0 `sandbox/config.py`：配置默认值与尚未强制执行的字段 |
| [S07] | QwenPaw v2.2.0 `sandbox/bubblewrap_sandbox.py`：挂载、环境与网络实现 |
| [S08] | QwenPaw v2.2.0 `governance/tool_adapter.py`：工具策略与执行模式 |
| [S09] | Kubernetes Pods：Pod/容器概念与共享网络命名空间 |
| [S10] | Kubernetes Network Policies：Pod 粒度、出站策略与 DNS |
| [S11] | Kubernetes Resource Management：容器资源请求、限制与内存行为 |
| [S12] | Bubblewrap 官方仓库：沙箱边界和命名空间配置 |
| [S13] | Linux Landlock：文件能力限制与已打开 FD 的注意事项 |
| [S14] | Linux cgroup v2：资源控制、层次与委派 |
| [S15] | Kubernetes Linux Kernel Security Constraints：内核级安全限制 |
| [S16] | gVisor 架构指南：沙箱边界、威胁模型与多客户隔离 |
| [S17] | Linux `openat2(2)`：受限路径解析 |
| [S18] | Python `tarfile`：归档提取风险与过滤限制 |
| [S19] | PostgreSQL Row Security：RLS 与绕过行为 |
| [S20] | PostgreSQL SELECT：锁与 `SKIP LOCKED` |
| [S21] | MCP 2025-06-18 Transport 规范：stdio 与 Streamable HTTP |
| [S22] | Kubernetes Pod Lifecycle：退出与终止流程 |
| [S23] | Kubernetes Volumes：临时卷及生命周期 |
| [S24] | Kubernetes HPA：基础设施容量扩展 |
| [S25] | Amazon S3 Mountpoint：对象挂载与 POSIX 功能限制 |
| [S26] | Amazon S3 Prefixes：前缀不是真实目录 |
| [S27] | Amazon S3 Presigned URLs：对象级临时访问能力 |

[S01]: https://github.com/agentscope-ai/QwenPaw/blob/v2.2.0/src/qwenpaw/constant.py
[S02]: https://github.com/agentscope-ai/QwenPaw/blob/v2.2.0/src/qwenpaw/app/task_tracker.py
[S03]: https://github.com/agentscope-ai/QwenPaw/blob/v2.2.0/website/public/docs/api-tutorial.zh.md
[S04]: https://github.com/agentscope-ai/QwenPaw/blob/v2.2.0/src/qwenpaw/checkpoints/hooks.py
[S05]: https://github.com/agentscope-ai/QwenPaw/blob/v2.2.0/src/qwenpaw/runtime/phases.py
[S06]: https://github.com/agentscope-ai/QwenPaw/blob/v2.2.0/src/qwenpaw/sandbox/config.py
[S07]: https://github.com/agentscope-ai/QwenPaw/blob/v2.2.0/src/qwenpaw/sandbox/bubblewrap_sandbox.py
[S08]: https://github.com/agentscope-ai/QwenPaw/blob/v2.2.0/src/qwenpaw/governance/tool_adapter.py
[S09]: https://kubernetes.io/docs/concepts/workloads/pods/
[S10]: https://kubernetes.io/docs/concepts/services-networking/network-policies/
[S11]: https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/
[S12]: https://github.com/containers/bubblewrap
[S13]: https://www.kernel.org/doc/html/latest/userspace-api/landlock.html
[S14]: https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html
[S15]: https://kubernetes.io/docs/concepts/security/linux-kernel-security-constraints/
[S16]: https://gvisor.dev/docs/architecture_guide/intro/
[S17]: https://man7.org/linux/man-pages/man2/openat2.2.html
[S18]: https://docs.python.org/3/library/tarfile.html
[S19]: https://www.postgresql.org/docs/current/ddl-rowsecurity.html
[S20]: https://www.postgresql.org/docs/current/sql-select.html
[S21]: https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
[S22]: https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/
[S23]: https://kubernetes.io/docs/concepts/storage/volumes/
[S24]: https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/
[S25]: https://docs.aws.amazon.com/AmazonS3/latest/userguide/mountpoint.html
[S26]: https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-prefixes.html
[S27]: https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html

---
