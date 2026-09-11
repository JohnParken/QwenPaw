# 多用户 Runner 生命周期、恢复与 Memory 契约 v1

状态：拟实施协议，2026-09-11；不代表现有代码已实现。配套：[实施基线 v5](QwenPaw_Multiuser_Harness_Implementation_Baseline_v5.md)、[状态机与事务表](QwenPaw_Multiuser_State_Transactions_v1.md)、[首期能力矩阵](QwenPaw_Multiuser_Capability_Matrix_v1.md)。本文件细化 v5 第 5、11、13 节；枚举与事务条件以状态机文档为准。

## 1. 首期承诺与适配入口

首期支持 L1（连接断开后继续、已提交事实与消息快照可补读）和经验证的 L2（从已提交回合/子任务边界重建）。未持久 Token 尾部可丢失并显式标记。L3 仅对完成工具适配和故障测试的模式开放。不恢复 Python 栈、Future、PID、浏览器句柄；不通过重新发送原始用户请求冒充恢复。

可复用入口是 [Workspace.stream_query](../../src/qwenpaw/app/workspace/workspace.py)，其调用 Runtime 生命周期；[CLI task](../../src/qwenpaw/cli/task_cmd.py) 的直接 Agent 执行只能作为 PoC 参考。[Session Hook](../../src/qwenpaw/hooks/session/session_hook.py) 的异常处理与 [自动 Checkpoint Hook](../../src/qwenpaw/checkpoints/hooks.py) 的异步调度不能直接提供云端提交成功保证。适配器必须把加载、保存、导出失败显式上报，禁止失败后按空会话继续。

产品 standalone Session 仍须建立 Attempt 私有的本地 Runtime Workspace 对象；它不创建持久业务 Workspace。否则 `workspace=None` 路径会跳过现有会话 Hook。内部会话/用户标识使用平台生成的规范 ID，不以可碰撞的文件名净化结果承担身份映射。

## 2. 角色与调用契约

首期控制面与 Python Worker 独立进程，使用版本化 HTTP 协议，未来允许 Java 替换 Core，详见 [控制面与执行面边界](QwenPaw_Multiuser_Harness_Implementation_Baseline_v5.md#control-worker-boundary)。以下 Runner 接口是 Supervisor 与沙箱内 Runtime 的本地生命周期契约，不等同于 Worker→Core 网络 API；不把 Runner 对象直接暴露给远程调度器。Worker 通过客户端领取、续租、提交和报告清理，不访问业务数据库；两层协议分别版本化，平台凭据不传入 Runner。

| 组件 | 权限与输出 |
|---|---|
| Core | 验证范围、租约、版本；分配稳定步骤/操作 ID；发布权威状态 |
| Supervisor | 创建沙箱、续租、物化、收集本地导出、调用 File API、销毁与清理确认 |
| Runner | 沙箱内运行 Runtime；仅报告本 Attempt 的事件、执行意图和本地导出描述，不直接提交 Head |
| Memory adapter | 当前 Scope 内的停止写入、一致导出、校验、恢复；不访问平台文件服务 |

Runner 最小接口（拟新增；所有调用带协议版本、request_id、deadline）：

```text
initialize(context, pinned_profile, local_inputs) -> Ready(capabilities)
restore(committed_manifest, local_state, recovery_plan) -> Restored(cursor)
execute(segment, control_channel) -> Boundary(reason, cursor)
quiesce(reason, deadline) -> Quiesced(barrier_id) | QuiesceFailed
export_state(barrier_id, checkpoint_id) -> LocalExport(manifest, entries, digests)
close(deadline) -> Closed | CloseFailed
```

同一 Attempt 在中间 checkpoint 后继续时，另使用 `release_barrier(barrier_id, committed_checkpoint_id)`：Core 提交已确认、租约仍有效且无停止意图才可释放；Runner 同步释放各写入者的静止令牌。重复释放幂等，未知/过期令牌拒绝。后端不能安全恢复写入时关闭本 Attempt，以已提交边界创建新 Attempt，不能重新启动旧实例。

`context` 固定 tenant_id、owner_user_id、actor_user_id、scope、session、run、attempt、lease_epoch、base_revision、策略与镜像版本。tenant 必填；首期 Workspace 仅 owner 可用，actor 单独审计。续租只能由 Supervisor 执行；Runner 不自报合法 owner 或增加预算。控制通道使用结构化协议，与工具 stdout/stderr 分离；结果大小、路径与消息类型均有上限。

Linux 生产 P0—P3 使用常驻单 Pod 单 Slot Worker 池，P4 多槽须另验。macos-dev 使用本机 Worker 进程/单 Slot，无虚拟机或 Pod 前置。单槽不取消 Runtime 与 Supervisor 的边界，也不保证物理隔离；任务前后仍销毁私有状态和进程。资源、网络和凭据约束不依赖原生工具审批开关。

### 2.1 macos-dev 适配

P0 执行子集以 [收敛后的开发清单](QwenPaw_Multiuser_Harness_Implementation_Baseline_v5.md#p0-readiness) 为准：02a/02b 分离网络协议和 fixture；03a/03b/03c 分离启动、隔离清理和真实 Runtime。必需 native 用例仅使用离线确定性模型、批准的本地文件工具、NullMemory 和完整回合边界；05b 验证在新 Worker 重建，不承诺本文件第 4/5 节的完整工具中途恢复或等待输入链路已实现。ReMe 六方法作为旁路调查，真实 DeepSeek 为可选联调；未验证能力保持关闭。本地取消/失联清理在 P0 必须验证，不能等到 P3 业务取消接入。

配置与第一阶段范围见 [macos-dev](QwenPaw_Multiuser_Harness_Implementation_Baseline_v5.md#macos-dev) 和 [P0 开工条件](QwenPaw_Multiuser_Harness_Implementation_Baseline_v5.md#p0-readiness)。profile 只替换启动/路径/存储适配，不改变 tenant、租约、版本、WAITING_INPUT 或提交契约。

fixture executor 不执行 QwenPaw，测试协议即可；native executor 以全新、环境白名单进程启动真实 QwenPaw，并由新增 Seatbelt task launcher 包住整个 Runner。默认模型为离线确定性适配器，可产生固定回复/工具调用以验证真实 Runtime；它与不运行 Runtime 的 fixture executor 分开标记。命令级 MacOSSandbox 及“命令存在”探测不能直接替代任务级探测，失败不得裸跑。

PathMap 将 home/tmp/state/workspace/secrets 映射到 Attempt 私有绝对根，跨 Attempt 恢复只使用逻辑路径；不导入真实用户 .env/钥匙串。控制与导出使用受限本任务 IPC，Loopback Core/File API 不因此对所有任务授予管理权。Linux 专属网络/资源能力标 UNSUPPORTED，不按测试跳过计 PASS。

运行身份使用带类型的 runtime_identity：macos-dev 为 local_bundle(commit,dirty_diff_digest,dependency_lock_digest,python,arch)，Linux 生产为 container_image(digest)。恢复匹配该类型与实际指纹，不伪造镜像 digest；生产拒绝 local_bundle 身份，跨环境恢复必须有单独兼容性证据。macOS 文件系统大小写等差异也纳入路径/快照测试。

开发 Python 固定为 3.12.11，真实模型提供方固定为 DeepSeek；具体模型 ID、思考模式和适配器版本纳入 pinned_profile。DeepSeek 的带工具思考模式要求后续请求完整回传 reasoning_content，详见 [官方契约](https://api-docs.deepseek.com/guides/thinking_mode/)。因此恢复测试必须覆盖 assistant content/reasoning_content/tool_calls、tool_call_id 与工具结果的完整保存和对应关系，不能只恢复最终文本；持久化字段须继承会话权限与保留策略，不进入公开调试日志。现有 model_factory 的 DeepSeek reasoning replay 适配是复用起点，仍需验证其经过 Session 保存、导出及重建后不丢失字段；普通消息恢复通过不能代替该测试。

## 3. 生命周期与失败处理

| 阶段 | 必须完成的动作 | 失败处理 |
|---|---|---|
| INITIALIZING | Sandbox 能力验证；最小环境；首次导入前固定 HOME/WORKING_DIR/SECRET_DIR；仅批准的服务启动 | 不执行用户代码；记录失败并销毁；暂时失败可受限重试 |
| RESTORING | 校验 Scope、摘要、schema、镜像/配置/Memory 兼容性；只恢复权威已提交清单 | 不兼容进入阻塞恢复状态；损坏内容报错，不回退空会话 |
| READY | 会话、工具、Memory 就绪；核对累计预算与控制通道 | 失租不得进入执行 |
| EXECUTING | 执行一个有界片段；每个受管工具先记录意图再调用；持续响应停止信号 | 工具未知结果记账；不得仅捕获异常后宣布成功 |
| QUIESCING | 禁止新模型/工具/子任务；等待或取消所有写入者；Memory quiesce；确认文件写入停止 | 超时不发布新 checkpoint；终止沙箱，保留旧 checkpoint |
| EXPORTING | 在同一 barrier 下导出文件、当前会话、Memory、执行游标和预算 | 任一部分失败则整次导出失败；不可混入各自“最新”版本 |
| COMMITTING | Supervisor 上传并校验；Core pin 完整引用闭包后条件提交 | 查询稳定 commit_id；未知响应不等于失败或成功；失租不得新提交 |
| CLOSING | 关闭 Memory/MCP/浏览器/Runtime；Supervisor 撤销临时能力并清理进程/可写层 | 业务提交保持不变；槽标为隔离待清理，不复用 |

正常执行可在成功提交中间边界后恢复接收写入，继续同一 Attempt；WAITING_INPUT、暂停、终态必须关闭沙箱。关闭失败不撤销已提交的业务结果。`close`、控制请求和提交查询幂等；已关闭实例不能重新 initialize，需新 Attempt。

取消期间只允许专用“取消收尾提交”：保存一致且可取得的审计/状态和 CANCELLED，不允许发布 SUCCEEDED。无法取得一致快照时保留旧 Head 并记录未保存工作；取消不是撤销远程副作用。

## 4. 可执行的恢复协议

### 4.1 必须持久化的逻辑标识

| 字段 | 规则 |
|---|---|
| logical_step_id | Core 为已接受的下一步分配，不能由恢复后的模型重新猜测 |
| operation_id | 某个已持久化步骤中的具体工具调用 ID，跨 Attempt 不变；相同参数的两次有意调用也必须有不同 ID |
| request_hash | 规范化工具名、参数、工具版本与权限上下文摘要；用于拒绝同 ID 改参，不作为操作 ID |
| continuation_ref | 可恢复的下一步描述：已选定调用、等待答复或下个有界片段；不包含可执行反序列化代码 |
| transcript_cursor | 已提交消息版本/工具调用位置；未提交 Token 仅是临时展示 |
| checkpoint operation map | 步骤、操作结果、文件影响与会话游标的对应关系 |

模型选定调用后，先把调用描述及顺序持久化，再允许调用工具。首期一个 Run 内受管工具串行；并行调用须另定义调用图与统一提交屏障。远程操作经过强制适配器/网关验证有效 Attempt；通用 Shell/脚本能发起的任意远程请求不能被误认为已纳入账本。

### 4.2 正常执行顺序

```text
生成下一步 → 持久化 step/operation(INTENT)
→ 领取本操作 dispatch 权，持久化 IN_PROGRESS，收到确认
→ 执行工具 → 持久化结果/外部请求号/文件影响
→ 更新本地会话与逻辑游标 → quiesce/export
→ upload/READY/pin/Core commit → 获得 checkpoint 提交确认
```

`begin` 响应丢失时查询操作状态；不能将已 IN_PROGRESS 的操作再次分发。无法确认旧分发是否发生时按 UNKNOWN 处理。已知失败也必须区分“保证未产生副作用”与“可能部分成功”。前者可按普通重试策略，后者只能走下述已冻结的未知结果策略。

### 4.3 故障后执行顺序

1. Core 条件标记旧 Attempt LOST、撤销提交权与临时外部能力；同 Run 保留逻辑 Scope 占用。
2. 查询稳定 commit_id，确认最后一次提交是否实际成功，不能只看 Worker 最后收到的响应。
3. 加载已提交 checkpoint，并读取其游标之后所有持久步骤/操作；校验兼容性和预算。
4. 对未决操作按下表生成持久 recovery_plan。若旧执行者仍可能访问无 fencing 的外部服务，先确认停止或进入 WAITING_RECONCILIATION，禁止盲目重放。
5. 对可恢复 Run 创建新 Attempt、新私有副本；恢复固定版本，不复制旧活动目录。
6. 先完成/接回既有待决步骤，将已知结果注入对应工具调用位置；完成后才允许模型生成新步骤。
7. 提交新的完整边界并发布恢复事件。未提交文本标记中断，最终消息按版本替换。

| 故障事实 | 恢复决定 |
|---|---|
| INTENT 且无有效 dispatch | 经新 Attempt 授权后执行同一操作 |
| IN_PROGRESS/UNKNOWN，只读 | 确认旧分发权失效后按预算重试；结果允许变化并注明重试 |
| IN_PROGRESS/UNKNOWN，外部幂等 API | 使用同一幂等键查询/重试；超过服务端幂等保留期则转对账 |
| IN_PROGRESS/UNKNOWN，外部可查询 | 先查询；“暂时查不到”只有符合服务契约才可判定未执行 |
| 外部结果不可确认 | 按冻结 fallback 失败、限时对账或受限继续；禁止模型自行把 UNKNOWN 改为未执行 |
| 结果 SUCCEEDED、checkpoint 尚未包含 | 恢复结果及对应 transcript；本地修改同时恢复完整 file_effect；缺失时仅按已验证重放规则执行 |
| 故障前未提交的多步本地修改 | 回滚到完整边界，按原顺序重放可重放片段；不得恢复部分文件后重复整个片段 |
| checkpoint 已提交、响应丢失 | 查回同一提交结果，不重复推进 revision |

L2 不必保存每个模型 Token 或任意循环栈；但只要允许片段内外部副作用，就必须能保存和接回该调用。未实现接回能力的工具只能在独立可提交片段运行，或首期关闭。预算由 Core 累计，未知消费采用保守预留，不因恢复清零。

### 4.4 未知副作用的回退

每个工具版本在 [能力矩阵](QwenPaw_Multiuser_Capability_Matrix_v1.md) 冻结 fallback_action、风险级别、重试上限、对账时限、可跳过标记。FAIL_WITH_UNKNOWN_OUTCOME 终止 Run 为 FAILED 并附 outcome_warning；WAIT_FOR_RECONCILIATION 仅在存在查询或人工确认路径时等待，到期失败；SKIP_WITH_WARNING 仅对可选步骤返回明确跳过/不完整结果。

RETRY_WITH_DUPLICATE_RISK 仅适用于平台批准的低风险动作，用户在执行前对具体工具/动作范围及次数预授权，Core 验证授权仍有效且旧执行者已停止或被隔离。建立新 operation_id，记录 retry_of 与授权证据；原操作保持 UNKNOWN，不改成 FAILED/未执行。幂等重试仍复用原 operation_id，二者不可混同。禁止 ASSUME_ABORTED_AND_LOG；付款、发送消息、部署等高风险动作不能由通用“允许重复网络操作”开关授权。任何回退不跳过租约、预算、范围和 checkpoint 条件。

处置决定由 Core 持久化为 operation_disposition，绑定策略/授权/预算及后继操作；checkpoint 记录其应用游标。恢复时先接回已存在决定，不因原操作仍为 UNKNOWN 再次创建重试或重复提问。SKIP 的事实是“明确跳过且结果未知”，不能注入虚构成功结果。

## 5. WAITING_INPUT、审批与暂停规则

原生 [ApprovalService](../../src/qwenpaw/app/approvals/service.py) 的 Future 不能跨进程恢复。拟新增 WaitingInputAdapter 将等待转换成可持久化 continuation；无法导出 continuation 的交互式工具首期关闭。

每个 Run 首期至多一个 OPEN prompt；表单可包含多个字段。持久字段：`prompt_id/generation/kind/schema/question_ref/continuation_ref/operation_id/request_hash/policy_version/expires_at/status`，kind 为 `clarification | tool_approval`。所有字段绑定 owner、scope、run；问题内容按预算限制。

1. 到达等待点，停止其他写入，导出包含 continuation 的一致状态；工具审批必须发生在 dispatch 前。
2. 通过文件提交协议，在同一 Core 事务中发布 checkpoint、OPEN prompt、WAITING_INPUT、事件，并撤销当前 Attempt 提交权。提交失败不得向前端发布“可以答复”的权威问题。
3. Supervisor 关闭沙箱、释放计算槽；Scope 的 active_run_id 保留。输入可以先被持久接受，但新 Attempt 执行前必须满足前次关闭或故障隔离条件。
4. `POST inputs` 校验 Run 正处等待、prompt_id/generation、未过期、schema、owner 和幂等键；一个 prompt 只接受一个答复。同键同内容返回原结果，同键不同内容冲突；不同键重复答复冲突。
5. 接受输入和 WAITING_INPUT→QUEUED 在同一事务内完成。输入先为 ACCEPTED，恢复时可重复读取；仅包含其应用结果的 checkpoint 提交后变为 APPLIED，不在领取时不可逆消费。
6. 新 Attempt 从 continuation 接回，先应用已有答复。工具批准仅适用于原 operation/参数/策略版本；策略已撤销则拒绝执行，参数变化需生成新操作及新 prompt。

拒绝审批产生该工具的明确 DENIED 结果，Agent 可选择其他方案；不自动取消整个 Run。迟到、取消后或旧 generation 的输入拒绝，不隐式创建新任务。更换问题只能在无已接受答复时原子作废旧 prompt 并创建新 generation。

每个部署 profile 必须配置 `input_wait_timeout`、`pause_timeout`、`run_deadline` 和 `max_waiting_runs_per_user`，缺失时配置校验失败；不提供无限等待默认值。实际等待截止时间取等待期限与 Run deadline 的较早者。等待不计占槽执行时间，但计墙钟时限并占用户等待额度。过期进入 TIMED_OUT，关闭问题并释放逻辑占用。

同 Workspace 其他写 Run 排队；用户看到占用 Run、等待原因与截止时间，可取消该 Run 后继续。Reset 默认冲突；“停止并重置”先建立维护占用。读取已提交历史与下载现有产物可继续。WAITING_RECONCILIATION 不按普通等待输入自动释放占用，需明确对账或终止并登记未决副作用。

后续提供显式 Park & Yield，首期关闭：只在沙箱停止且无待决副作用时保留分支 checkpoint、进入 PARKED 并让渡 Scope。恢复时重新获得占用并比较 revision，变化则重新规划/显式合并或独立分支；不得覆盖新 Head。原问题在 park 后暂停接收输入，unpark 完成版本检查后才重新开放。与仅释放槽的 PAUSED 不同，细节见 [Park 事务规则](QwenPaw_Multiuser_State_Transactions_v1.md#52-park--yield后续独立能力首期关闭)。仅路径不冲突不足以自动合并，Memory/配置及读依赖也须验证。

暂停使用独立 PAUSED 状态：只有提交安全边界后才释放槽；resume 复用同一 Run、新 Attempt，不要求伪造用户输入。暂停保留 Scope 占用并受 pause_timeout/Run deadline 限制。仅有 pause_requested 而未到边界不表示已暂停。

## 6. Memory 后端快照契约

每个后端/版本实现以下接口，缺少一致导出或恢复能力时不得启用持久 Workspace Memory。Standalone 默认使用显式 NullMemory 适配器，导出 `memory=null`，不偷偷创建跨 Session 记忆。

| 方法 | 输入/返回及要求 |
|---|---|
| probe | 返回 backend/version/schema、支持的 scope、后台写入、索引模式、可恢复格式；仅能力声明，仍需验收 |
| quiesce | 输入 barrier_id/deadline；关闭写入入口，排空或停止自动提炼/索引/后台 job，返回持有的静止令牌；失败明确报错 |
| export_snapshot | 输入静止令牌、checkpoint_id；输出当前私有目录中的确定文件清单、摘要、memory_revision 和 backend 元数据；令牌有效期间禁止写入 |
| validate_snapshot | 校验完整性、范围、版本兼容与依赖闭包；不得将反序列化载荷作为代码执行 |
| restore_snapshot | 在全新私有根恢复源数据，校验后再开放检索/写入；重复调用同恢复请求返回同结果或清理未完成临时根后重建 |
| close | 幂等关闭连接和全部后台 job，明确返回成功/失败；不能用“已发取消信号”代替已停止 |

六个方法是必需基线；支持同 Attempt 多片段的后端还须支持上述 `release_barrier`。静止令牌在释放或 close 前保持有效；导出期间不能因租约超时自行恢复后台写入。未提交导出不允许通过释放令牌宣称已持久化。

Memory manifest 至少含 `backend_id/backend_version/snapshot_schema/scope/memory_revision/source_session_provenance/source_entries/index_entries/index_status/embedding_profile/source_revision_digest`。凭据不入快照，通过本 Attempt 能力重新注入；恢复配置只能选择批准的后端和目的服务。

权威记忆源与索引分开：源必须完整保存；索引可省略后重建。重建就绪前回退到同版本源数据检索（后端支持时）或明确返回 MEMORY_INDEX_NOT_READY；不得静默返回空结果，也不得读取旧/未来/其他 Scope 的索引。ReMe 版本、索引格式或 embedding 配置改变需显式兼容器/迁移，不能自动改读最新格式。

SQLite 等本地数据库使用后端一致性 backup/export，禁止复制活动数据库文件及 WAL 拼装快照。对远程 Memory，必须能导出固定 revision 或固定版本引用并持续保留；仅返回“远程表名”不满足协议。现有 [ReMe close](../../src/qwenpaw/agents/memory/reme_light_memory_manager.py) 可能超时失败，必须传播；不能假定调用 close 就已静止。

一个 Workspace revision 包含文件根、Memory revision 及 `session_id → conversation_revision/FileRef` 映射。只更新当前 Session 条目，保留其他会话；单一 Core 提交发布这些引用。Standalone revision 只含自身 Conversation/中间文件。会话删除不自动擦除已提炼 Memory，来源追踪用于后续显式删除策略。

### 6.1 快照体积与增量演进

不要求每个工具边界全量上传 Memory。首期按有界回合/子任务提交：Memory 未变时复用旧 FileRef，保留其引用；源数据与可重建索引分开，只上传改变的不可变内容。即使 Memory 复用，仍需证明它对应本次一致边界；不能用复用掩盖未排空的后台写入。

Delta Export 是后端可选扩展，需包含 base_memory_revision、顺序、添加/修改/删除、格式版本、源摘要和完整依赖闭包；恢复严格按基线和增量顺序应用。配置最大链长、累计体积及恢复时延阈值，触发合并为完整基线，旧链按引用 GC；任何基线缺失/格式不兼容拒绝恢复。不能直接复制活动 SQLite WAL 充当可移植增量。

验收记录全量/增量大小、export/upload/restore P95、峰值内存和索引重建耗时。只有本地导出经优化仍超过预算时才评估独立 Memory Service，同时保持固定 Scope/revision 与恢复契约，不把远程“最新状态”作为捷径。

### 6.2 本地内容缓存

首期仅共享批准的公共只读镜像/依赖；私有 CAS 为后续优化。私有缓存键绑定 tenant/scope/digest/format，每次命中先验证 FileRef 授权与摘要；缓存不是事实源。可信组件写缓存，任务只取得私有副本；使用 reflink/COW 或复制，禁止将同一缓存 inode Hardlink 到可写工作区。配额、淘汰、损坏重取与并发物化须验收，不跨用户私有内容去重。

## 7. 最小故障验收

- 相同参数的两次合法工具调用产生不同 operation_id；跨 Attempt 接回同一调用 ID 不变。
- 外部成功、结果落库前杀 Worker：幂等适配器查回；不可确认操作进入对账，不重复执行。
- 本地文件写入已记录、checkpoint 未提交：恢复后内容和 transcript 同步，不跳过丢失文件影响。
- 等待提交失败不出现 OPEN prompt；答复接受后杀 Worker，重建仍得到同一答复且业务只应用一次。
- Memory 后台 job 未停止、export 失败、索引不兼容：禁止发布不一致 checkpoint，并保留上一版本。
- 同 Workspace 两 Session 轮流提交，分别恢复自己的对话，共享同一已提交 Memory revision。
- 提交成功但 close 失败：结果仍可查，槽不得复用；旧 Attempt 无法再提交或分发外部操作。
