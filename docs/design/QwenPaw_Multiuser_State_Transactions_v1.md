# 多用户状态机与事务表 v1

状态：拟实施协议，2026-09-11；本文件是编码、DDL 和并发测试的输入，不是已有实现。配套：[实施基线 v5](QwenPaw_Multiuser_Harness_Implementation_Baseline_v5.md)、[Runner 生命周期](QwenPaw_Multiuser_Runner_Lifecycle_v1.md)、[首期能力矩阵](QwenPaw_Multiuser_Capability_Matrix_v1.md)。细化并统一 v5 第 12、15、20、24 节中的状态名称。

开发支持 [macos-dev](QwenPaw_Multiuser_Harness_Implementation_Baseline_v5.md#macos-dev)：Core/File API/Worker 用本机独立进程，同一套状态机与事务校验不作平台分叉。锁、唯一约束、配额和并发测试连接真实 PostgreSQL；内存 repository 只验证控制流程，不证明 SQL 正确性。local_test 文件驱动不证明生产存储耐久性；报告记录 profile 与真实/fixture executor。Attempt 记录带类型 runtime_identity，本机指纹与生产镜像 digest 的兼容规则见 Runner 契约。

## 1. 数据约束与版本选择

P0 仅实现 [最小持久链路与故障实验](QwenPaw_Multiuser_Harness_Implementation_Baseline_v5.md#p0-readiness) 所需的模型子集：固定测试身份与静态准入额度，真实 Run/Attempt/Scope 条件更新、请求幂等、持久文件封存与 commit 查询；不提前实现完整公平调度、计费和 WAITING_INPUT 产品链路。未实现状态不得返回虚构成功。本文件中 P2/P3 规则仍为后续契约，不能把 P0 子集测试解释为全表验收。P0 禁用自动 GC、保留全部测试引用，只证明提交一致性；P2 仍须完成补偿与 GC 窗口测试。

本文件中的业务数据库事务、调度 SQL 与故障扫描只在 Core 执行。Python Worker 通过版本化 HTTP API 请求 claim/续租/提交，不持有业务 DSN 或 ORM；Java 替换 Core 时必须保持相同条件更新、锁序和幂等语义，见 [控制面与执行面边界](QwenPaw_Multiuser_Harness_Implementation_Baseline_v5.md#control-worker-boundary)。claim 的 request_id 与分配结果在同一事务中持久化；重试查回原分配，过期或已结束返回原分配的当前状态，不隐式建立新 Attempt。新领取使用新请求 ID；租约响应丢失不取消数据库中已发生的续租，也不能让旧执行者跳过本地停止期限。

- `execution_scopes(owner, type, id)` 为 Workspace/Standalone Session 提供统一调度行：`revision, active_run_id, maintenance_operation_id, lifecycle`。Workspace/Session 的原有 active_run_id 若保留，仅作为同事务维护的展示字段，不是第二个锁来源。
- tenant_id 必填；首期 Organization 由上游映射为 tenant，不另建组织体系。Workspace 必须有唯一非空 owner_user_id，同 tenant 内一个用户可拥有多个 Workspace；不对 owner_user_id 单独加 UNIQUE。actor_user_id 单独记录实际操作者，首期仅允许 owner 操作，不开放共享成员；未来协作通过成员权限扩展。
- 身份、关联、幂等键和 FileRef 授权均包含 tenant。Workspace ID 可全局唯一，owner 与 tenant 仍须通过复合关联/校验固定，不能靠 ID 难猜保证隔离。quota 同时约束 tenant/user 的并发、排队、等待、Token 与存储。
- 同 Scope 至多一个逻辑 Run；同 Run 至多一个有效 Attempt。Scope 使用非空类型和 ID，唯一约束不依赖可空 workspace_id。
- Run 与 Session/Workspace 的 owner 必须一致；Core 内使用复合关联约束/检查，File Service 在契约入口独立验证可信 Scope。跨 files schema 不建外键。
- `runs.current_attempt_id` 指向当前提交者；每次创建 Attempt 递增 Run 的 lease_epoch。终态、等待和暂停撤销有效提交者；旧 Attempt 记录保留供审计。
- 所有租约判断使用数据库时间；worker_id 是进程实例 ID。幂等键与请求摘要必须持久化，保留期不得短于支持的重试/恢复期限。

版本策略：附件始终固定 FileRef；新 Run 默认 `base_policy=latest_at_claim`，首次领取时绑定当前 Scope revision。调用者可显式 `expected_revision`，不匹配则 FAILED/STALE_REVISION，不静默换版本。已开始的 Run 恢复必须使用自身最新已提交 checkpoint，不能重新选择 latest；等待期间 Scope 占用保证基线不被其他写 Run 推进。

内部事务锁序统一：`tenant quota → user scheduler/quota → scope → run → attempt → operation/prompt/commit → event counter`，同层多行按 ID 排序。仅锁本操作所需行，但不得在已锁后序行时反向获取配额行；完成/取消/恢复释放额度同样遵循此顺序。队列先无锁扫描用户候选，再按顺序 `SKIP LOCKED` 锁调度/Scope 行并重检。远程文件调用和工具执行均不在数据库事务内。

### 1.1 公平调度与配额原子预留（P2）

维护 `tenant_quotas`、`user_scheduler(tenant_id,user_id,weight,virtual_finish,active_count,queued_count,waiting_count)` 与幂等 `quota_reservations`。首期使用加权虚拟完成时间轮转：在可运行且未超限的用户中按 virtual_finish 选择，再取该用户最早满足 Scope/版本/profile 的 Run；成功领取后令 `virtual_finish = max(virtual_finish, pool_virtual_time) + estimated_cost / weight`。首期 estimated_cost=1，只承诺调度启动机会公平；profile 成本与实际占槽时间结算后续验证，不声称已实现资源公平。

pool_virtual_time 是每个执行池的持久调度水位；调度 tick 以当前可运行用户 virtual_finish 的最小值为候选，在独立短事务取 max(旧水位,候选)，无可运行用户则不推进。claim 读取水位快照，不反向锁水位行。新/重新活跃用户在领取时使用上述 max 规则，避免空闲期间积累无限优先权。并行 SKIP LOCKED 提供近似加权公平，不承诺严格全局次序；验收采用持续有可运行任务时的份额和最大等待时间。

claim 事务先锁 tenant quota/user scheduler，检查两级额度并预留 active reservation，再锁 Scope/Run、创建 Attempt、推进调度位置；后续检查失败整笔回滚。仅用窗口函数或查询 active 数量后再写入会竞态超领，不符合要求。claim_request_id 响应丢失返回原 reservation/Attempt，不重复扣额。

等待/暂停释放执行额度但保留 waiting reservation；接受答复进入队列时原子转换为 queued reservation，队列已满则拒绝接受并保持问题开放，不能丢失已接受输入。恢复参与同一公平队列，无无限优先插队。退出、失租、取消释放额度均以 reservation_id 幂等结算；逻辑额度释放不把未清理物理槽变为空闲。

Token 在模型调用前由可信网关预留，按实际用量结算，未知消费保守保留；存储在上传前按两级预算预留并在校验/GC 后结算。禁止通过新 Attempt 重置计费或预算。长任务另受有界片段、占槽时限和执行池分类限制。P2 验收包含两个 Worker 同时领取最后一个额度，以及一个用户连续提交 100 个任务时其他用户仍能获得执行机会。

## 2. Run 与 Attempt 状态

Run 终态：SUCCEEDED / FAILED / CANCELLED / TIMED_OUT。WAITING_RECONCILIATION 用于结果未知；RECOVERING 同时承担待兼容 Worker/格式适配的阻塞状态，通过 `blocked_reason` 区分，不反复热重试。

| 动作 | Run 转换 | 事务条件及结果 |
|---|---|---|
| 接受新任务 | 无→QUEUED | 输入 READY 且已 pin；请求幂等；绑定 Session；写事件后返回 202 |
| claim | QUEUED→STARTING | Scope ACTIVE、无维护、无其他 active Run、无取消意图且未超时；版本/额度/能力/前次清理或隔离门槛匹配；建立 Attempt 与租约 |
| ready | STARTING→RUNNING | 当前 Attempt/代数/租约有效，Runtime 初始化/恢复成功 |
| 提交中间边界 | RUNNING→RUNNING | 文件集合已 pin；CAS base_revision；原子更新 Head/checkpoint/游标/事件 |
| 分段交接 | RUNNING→RECOVERING | 已提交边界且后端不支持释放静止令牌；旧 Attempt STOPPED，撤销提交权并关闭；保留 Scope，经恢复决策创建新 Attempt，不记故障重试 |
| 等待输入提交 | RUNNING→WAITING_INPUT | 同上，附 OPEN prompt；结束 Attempt 提交权，保留 Scope 占用 |
| 接受答复 | WAITING_INPUT→QUEUED | prompt 开放、未过期且 generation 匹配；原子记录 ACCEPTED 输入与事件 |
| 暂停提交 | RUNNING→PAUSED | pause_requested 且到达一致边界；结束 Attempt 提交权，保留 Scope 占用 |
| resume | PAUSED→QUEUED | 未过期，checkpoint 可恢复；清除 pause_requested；无伪造输入 |
| 正常结束 | RUNNING→SUCCEEDED | 一致状态/产物已 pin；未取消、未超时；原子发布结果、撤销 Attempt、释放 Scope |
| 故障扫描 | STARTING/RUNNING→RECOVERING | 当前租约过期或执行失败；旧 Attempt LOST/FAILED，撤销提交权；保留 Scope |
| 恢复决策 | RECOVERING→QUEUED | 可兼容恢复、预算足够、待决操作安全、旧执行者满足隔离门槛 |
| 结果不明 | RUNNING/RECOVERING→WAITING_RECONCILIATION 或 FAILED | 原操作记 UNKNOWN；按冻结 fallback 等待或失败并附 outcome_warning；不强制所有未知结果无限等待 |
| 对账完成 | WAITING_RECONCILIATION→RECOVERING | 可信协调器持久化证据、结论与结果；不由模型修改账本 |
| 取消/超时/不可恢复 | 非终态→相应终态 | 按第 4 节结束，关闭 prompt、撤销 Attempt、释放 Scope；保存未决副作用清单 |

低风险操作只有在 [能力矩阵](QwenPaw_Multiuser_Capability_Matrix_v1.md) 规定的精确预授权和预算通过后，才能按 RETRY_WITH_DUPLICATE_RISK 建立新 operation（retry_of 指向旧操作）；原 UNKNOWN 不改成未执行。SKIP_WITH_WARNING 仅适用于可选步骤，记录跳过结果及产物不完整标记。高风险动作使用失败或有期限对账；到期 FAILED 并保留未知审计。RUNNING 中可按已授权策略继续；已经暂停的 Run 通过 RECOVERING 接回，不能绕过恢复校验。

任何终态不可被迟到结果改写。重试终态任务需新 Run、新幂等键；`resume` 不复活终态。

Attempt：`ASSIGNED → STARTING → RUNNING → COMPLETED | STOPPED | LOST | FAILED`。claim 创建 ASSIGNED；初始化确认 STARTING；ready 后 RUNNING；成功边界结束为 COMPLETED，等待/暂停/取消结束为 STOPPED。LOST/FAILED/STOPPED/COMPLETED 不重新获得租约。观察到的清理状态独立为 `PENDING | CONFIRMED | QUARANTINED`；业务状态结束不表示槽已可复用。

STARTING/ASSIGNED 也可因初始化失败、取消或失租进入 FAILED/STOPPED/LOST。所有普通 ready、工具 dispatch、继续执行及成功发布均检查取消意图与 Run deadline；续租不能延长业务 deadline。只有取消收尾的专用提交允许在取消意图存在时发布 CANCELLED。

## 3. 文件提交统一协议（P2 必须实现）

`file_commit_operations` 统一枚举：`PREPARING → PINNED → COMMITTED`，或 `PREPARING/PINNED → ABORTED`。COMMITTED/ABORTED 终态不可逆。outbox 自有 PENDING/DELIVERED，不与 commit 状态混用。

每次提交记录 `commit_id, kind, owner/scope, run/attempt/epoch(适用时), expected_revision, reference_set_id, closure_digest, target_state, request_hash`。kind 区分 RUN_ACCEPT、CHECKPOINT、ARTIFACT_FINISH、WAIT、PAUSE、CANCEL_FINALIZE、MAINTENANCE。创建任务/维护由其可信 operation 授权，不伪造 Attempt。

| 步骤 | 本地事务/外部动作 | 重试与失败处理 |
|---|---|---|
| 1 prepare | Core 事务建立稳定 commit_id/PREPARING，校验执行或维护权限 | 相同 ID 同请求返回原结果，不同摘要冲突 |
| 2 upload | 事务外调用 File API，取得 READY FileRef；确定完整引用闭包和 digest | upload_id 查询/重试；在途内容保护需续期；保护失效则重新校验内容，禁止继续引用已回收文件 |
| 3 seal/pin | 持久记录闭包与 reference_set_id 后，事务外封存文件集合 | 同 ID 同 digest 幂等；响应丢失查询集合；封存后不可改成员 |
| 4 pinned | Core 条件更新 PREPARING→PINNED，保存封存确认 | 已 ABORTED 则不能复活；迟到 pin 进入补偿对账 |
| 5 publish | Core 短事务锁 Scope/Run/Attempt/commit，检查有效租约、状态、生命周期、expected_revision、无取消等；推进 Head/会话映射/操作游标/输入 APPLIED/Run 目标状态/事件，commit→COMMITTED | 响应丢失按 commit_id 返回持久结果；不能重建另一 revision |
| 6 release | 业务解除引用时，同一 Core 事务写 release outbox；事务外幂等释放集合 | 失败多保留；不先删文件再解除业务引用 |

`PINNED` 是持久封存集合的确认，不能只是“上传完成”。reference-set 只由协调器释放，Worker 不具备释放权限。封存集合必须覆盖 Manifest、嵌套文件、Conversation、Memory、工具结果与 continuation 的完整闭包。

complete 生成 READY 后，未 pin 内容仍保留 upload 保护期；上传完成不立即解除保护。协调器须在该期限内 pin 或续期（拟增内部上传保护续期接口，仅可信协调器可用）。过期则重新查询/校验 FileRef，缺失内容重新上传；不能让文件在 complete 与 pin 之间无保护。pin 与到期 GC 的最终互斥由 File Service 事务保证。

补偿器先按相同锁序 CAS 未决 commit→ABORTED，确认无已提交目标及有效提交者；再释放集合。发布和 ABORTED 竞争只能有一方成功。DELETE reference-set 必须允许为尚未创建的稳定 ID 写入释放墓碑；否则“补偿先到、pin 后到”会留下不可控引用。迟到 PUT 不得复活墓碑，需新 generation/ID。File Service 内 pin/GC 对版本采用互斥状态检查，已进入不可逆删除的版本不得 pin。

取消或失租后仍可按 commit_id 查回已 COMMITTED 结果，但不能因此获得新写入权限。已 ABORTED 的运行如需重新提交，应由有效执行者建立新 commit_id。

## 4. 命令、租约与竞争结果

| 竞争 | 线性化规则与结果 |
|---|---|
| 两 Worker 领取同 Scope | Scope 行锁胜者获取 active_run_id；另一方重检后跳过；不持锁物化文件 |
| claim 响应丢失 | `worker_instance + claim_request_id` 幂等查回原 Attempt；本地槽预留在确认前不重复使用 |
| 取消 vs 成功提交 | 同锁序串行。成功先提交则取消返回已有终态；取消意图先提交则普通成功 CAS 拒绝，仅允许取消收尾 |
| 续租 vs 过期扫描 | 续租要求旧 lease_until 仍大于 DB 当前时间；过期不能复活。扫描 CAS 当前 Attempt 后标 LOST；只允许新代数恢复 |
| 输入 vs 等待超时 | 同锁锁 prompt/Run；以 DB 时间校验 expires_at。过期答复拒绝；先成功接受的答复不再被该 prompt 超时器撤销，Run deadline 仍有效 |
| Reset/Delete vs claim | scope.maintenance_operation_id 先登记再请求停止；存在维护占用时所有 claim 拒绝；停止与维护提交之间不清空维护占用 |
| 文件提交 vs 取消/删除 | 发布事务复核状态；失败 CAS 不发布产物，pin 走补偿；不能因文件存在就标任务成功 |
| 排队任务 vs revision 更新 | latest_at_claim 在实际领取时绑定最新；expected_revision 不符明确失败，不自动重写输入 |
| 等待答复已接受 vs 旧沙箱未关闭 | 输入保持 ACCEPTED/QUEUED；领取前检查关闭确认或故障隔离门槛，不能并行应用两次答复 |

取消/超时：QUEUED、WAITING_INPUT、PAUSED 且无执行者时直接事务结束；正在运行时先记控制意图、阻止新分发，等待限时收尾。限时结束后撤销租约/外部能力并强制停止；满足隔离门槛后结束并释放 Scope。远程无法撤销的请求记录 UNKNOWN 及 outcome_warning；终态只表示不再执行本 Run，不表示副作用已撤销。后续对账更新操作审计，不改终态。

故障隔离门槛：旧 Attempt 已确认停止，或者提交权已撤销、旧私有文件环境无法接触新副本、所有可产生远程副作用的通道已被强制撤销/fence，且未决操作已分类。存在不可撤销的未受管外部执行时进入对账；不能仅凭租约到期宣布可安全重新执行。销毁失败的旧槽始终 QUARANTINED，与新 Worker 能否恢复分别判断。

## 5. Scope、工具和输入的必要表约束

| 对象 | 状态/约束 |
|---|---|
| Scope | CREATING/ACTIVE/DELETING/DELETED；BUSY 派生；Reset 为独立 operation，不新造 Workspace 状态 |
| tool_operations | INTENT/IN_PROGRESS/SUCCEEDED/FAILED/UNKNOWN；operation_id 唯一、请求摘要不变；增加 dispatch_epoch、retry_class、effect_ref、resolution_evidence |
| 工具重试 | IN_PROGRESS/UNKNOWN 仅经分类/对账回到可分发状态，新增 dispatch 记录并保留历史；SUCCEEDED 不重新执行外部副作用 |
| operation_dispositions | UNKNOWN 的处理决定单独持久化：operation_id、fallback_action、policy_version、auth_ref、retry_budget、retry_of/child_operation_id、warning、适用 checkpoint 游标；不把处理决定冒充外部成功/失败证据 |
| run_prompts | OPEN/ANSWERED/EXPIRED/CANCELLED/SUPERSEDED；每 Run 至多一个 OPEN；审批绑定操作/参数/策略 |
| run_inputs | ACCEPTED/APPLIED；每 prompt 至多一个接受答复；应用随 checkpoint 原子提交，恢复读取不等于消费 |
| run_events | UNIQUE(run_id, producer_event_id)；每 Run 的 event_seq 由同事务计数行分配；状态/事件同时提交 |
| workspace_revisions | 保留所有 Session 的 Conversation 映射，仅替换当前项；Memory 和文件在相同 revision 发布 |
| file_commit_operations | UNIQUE(commit_id)，稳定 request_hash；唯一业务目标阻止同一 checkpoint 重复发布 |

权威事件续读以 DB 为事实源。通知仅用于唤醒；客户端注册后再次按游标补读，慢客户端断开补读。关键事件不接受旧 Attempt，新到的迟到执行日志只能作为非权威审计记录。

### 5.1 权威事件、暂态文本与分区

Run 状态、工具意图/结果、审批、checkpoint、最终消息版本写 PG，事务提交后发布。Token 增量在有界通道暂存，以独立 `attempt_id/message_id/stream_generation/stream_offset` 推送，不占用权威 event_seq；调试日志走独立有界日志通路。

定期持久化消息快照/合并片段（大内容可为 FileRef），记录 `message_revision + covers_stream_offset`；快照间隔、最大缓冲字节、保留期必须配置。重连先补读权威事件，再加载最近消息快照和仍可用的暂态尾部；尾部丢失返回 stream_reset 标记，最终消息按版本替换。L1 保证任务继续和已提交事实可补读，不保证未持久 Token 尾部无损。多 API 副本须共享路由/暂态通道或按快照降级；纯进程内 Channel/无持久 Pub/Sub 不能冒充跨副本补读存储。

事件写入批处理并与调度/心跳使用独立连接预算。P2 冻结容量、保留和分区决策，非强制首日启用分区：按 run_id Hash 可保持现有唯一键，但不能高效按时间整分区删除；按时间 Range 则必须把分区键纳入唯一键，并通过独立去重记录等方式保留逻辑去重保证。分区/归档前检查引用及重放期限。WAL、索引和租约更新压力分别监测；不将追加写入直接等同于大量死元组。

### 5.2 Park & Yield（后续独立能力，首期关闭）

新增 PARKED 状态及 `branch_checkpoint_id/base_revision/park_expires_at`，与保留占用的 PAUSED 分开。只允许 WAITING_INPUT/PAUSED 且沙箱停止、无待决副作用时显式 park；先保留分支完整引用，Core 同事务记录 PARKED、移除 Scope active_run_id、保留有界 parked/waiting 配额。后续 claim 不能把 PARKED 当作普通 QUEUED。

park 与答复接受在同一锁序下竞争：答复先接受进入 QUEUED 则 park 冲突；park 先成功则原问题挂起，inputs 拒绝并要求先 unpark，防止绕过版本检查。unpark 重新获得 Scope/配额后比较当前 revision：相同则恢复先前 WAITING_INPUT/PAUSED 状态；不同则保持 PARKED，要求重新规划、显式合并或转独立分支。审批参数/策略变更必须作废旧批准。禁止直接恢复旧目录覆盖新 Head。

分支仅文件路径不冲突不代表可合并：需校验读集/写集、Memory、配置与删除语义；通用 Shell 无可验证读集时视为依赖整个 Workspace。未实现分支合并时仅提供冲突提示/导出，不静默合并。取消/park 超时可结束 Run 并按保留规则释放分支引用，不影响当前 Head。未来开放此能力须另加 park/unpark API 与并发测试；首期仍采用单写者与有限等待。

park_expires_at 不得超过 Run deadline，park 不延长审批授权期限；unpark 发现问题/授权过期必须重新提问或超时结束。Scope 删除或 unpark revision 冲突时不保留临时获取的占用/执行额度。恢复扫描先查 operation_disposition，再决定是否需要重新对账，防止已授权跳过/重试在每个 Attempt 重复触发；处置应用与下一 checkpoint 游标绑定。

## 6. 交付顺序调整

| 阶段 | 文件与可靠性要求 |
|---|---|
| P0/P1 | macos-dev 单进程 Worker/单 Slot 验证 P0；Linux 生产单 Pod 单 Slot 验收 P1；Runtime/Supervisor 与跨 Attempt 仍隔离；本地驱动仅测试 |
| P2 | 单槽 Worker 池、两级身份/配额与公平领取；File API 独立数据面；FS01—FS06 与基础 GC；权威事件/消息快照；幂等及故障窗口测试 |
| P3 | 在 P2 同一链路增加一致 Memory/Conversation checkpoint、工具账本接回、WAITING_INPUT、PAUSED 和 L2 恢复；不再另造上传提交实现 |
| P4 | 多槽仅验证通过才启用；profile 成本调度、吞吐/恢复压测、完整 GC/保留运营和容量结论 |

P2 不承诺 Agent 故障续跑；它必须保证被接受的输入和成功发布的产物有持久引用。BFF 文件模块迁移仍在后续阶段。

受限直传、私有 CAS、Park & Yield、Memory Delta 均为后续按需演进，不是 P2/P3 的隐含必做项。全面 Workspace 并发合并单独立项。

## 7. 事务验收清单

P0 新增必需多会话用例 [P0-S01/P0-S02](QwenPaw_Multiuser_Harness_Implementation_Baseline_v5.md#p0-session-tests)：同用户不同 Standalone Session 的恢复状态不串用；同 Workspace 不同 Session 的写 Run 真实并发竞争时仅一个有效写入者，未获准者保持排队。依次提交时只替换当前 Session 的 Conversation 条目，另一 Session 的 FileRef/摘要保持不变且可读取；后继 Run 按 latest_at_claim 绑定前次 revision。用真实 PG 屏障与条件更新验收，不以测试脚本改状态模拟调度。此测试不依赖 WAITING_INPUT 或持久 Memory 产品实现。

1. 两 Worker 同时 claim、claim 响应丢失重试：一个 Run/Scope 仅一个有效执行者。
2. 在 prepare/upload/pin/publish 每个响应窗口注入断线：结果可查回，无重复业务 revision，无已提交内容被 GC。
3. pin 与 GC、publish 与 abort、release 先到与迟到 pin：无悬空引用，墓碑不复活。
4. 成功与取消、续租与扫描、输入与超时、Reset 与 claim 同时发生：结果严格符合竞争表。
5. WAITING_INPUT 无租约仍占 Scope；答复 ACCEPTED 后 Worker 死亡可重新应用，APPLIED 只在 checkpoint 提交时出现。
6. 旧 Attempt 在新代数建立后发送结果、事件和 Head 更新：权威写入拒绝；取消后的未知副作用仍可审计对账。
7. 同 Workspace 连续排队两个任务：latest_at_claim 按顺序推进；显式旧版本条件产生可解释冲突。
8. tenant/user 最后额度并发竞争、claim 重试、结束结算重试均不超扣/重复释放；恢复流量不饿死新用户。
9. Token 暂态通道丢失/跨副本重连：权威事件不丢，恢复消息快照并显式标记暂态尾部缺失。
