# QwenPaw 多用户能力矩阵 v1

本文是拟实施门槛（2026-09-11），区分仓库事实与尚未实现的云端保证。盘点内置描述器、治理工具、部分插件与 Memory 注册表；这是初始静态清单，不宣称覆盖全部动态注册。LSP、外部插件目录和运行时注入必须由启动 inventory 补齐，未列工具/插件默认拒绝。依据基线 [第 13 节](QwenPaw_Multiuser_Harness_Implementation_Baseline_v5.md#s13)、[第 17 节](QwenPaw_Multiuser_Harness_Implementation_Baseline_v5.md#s17)、[第 20 节](QwenPaw_Multiuser_Harness_Implementation_Baseline_v5.md#s20)。

## 级别与默认门槛

| 级别 | 本矩阵含义 | 默认 |
|---|---|---|
| L1 | 断线后任务继续、事件可补读 | 需由 Runner/Lifecycle 实现 |
| L2 | 仅从已提交回合/子任务边界恢复 | **P3 验证后的目标边界；当前未启用** |
| L3 | 单 Agent 循环内逐工具恢复 | 每个适配器单独验证后开启 |

L2 不推导出 Shell、MCP 或 Browser 的重放安全；离线本地写入仅在已验证可重放时从 checkpoint 重放，或恢复完整文件影响。脱离主 Run 的 Agent、cron、桌面控制首期关闭。下面隔离与取消要求均为拟实现目标；P2 可运行安全子集，但故障时明确失败，不自动续跑。

## Worker、Slot 与 Scope 约束

首期控制面与 Worker 独立进程，通过版本化 HTTP/OpenAPI 契约交互；未来 Java Core 复用相同协议。Worker 不直连业务数据库或导入 Core ORM，分机运行不依赖共享目录。能力登记不等于执行授权；Core 校验服务身份、分配关系与版本兼容后才允许领取。具体边界与 A43—A46 验收见 [控制面与执行面解耦](QwenPaw_Multiuser_Harness_Implementation_Baseline_v5.md#control-worker-boundary)。

Linux 生产 P0–P3 每个静态 Worker Pod 只使用一个 Slot；macos-dev 则每个本机 Worker 进程一个 Slot。P4 才可启用多 Slot，且必须分别验证资源、网络与进程边界。多 Slot 或 Pod 图示不构成物理隔离保证。每个请求、事件、产物、Memory 与检索都必须带可信注入的 `tenant_id`；私有 Workspace 只能在其 tenant 与授权用户 Scope 内访问。

## macOS 开发配置矩阵

P0 必需范围为 G1—G5：环境、跨进程协议、真实 Runner、macOS 执行边界与真实 PG 故障实验，具体见 [P0 开发清单](QwenPaw_Multiuser_Harness_Implementation_Baseline_v5.md#p0-readiness)。使用离线模型、NullMemory 与批准的确定性本地文件用例。ReMe 只要求调查报告，DeepSeek 联调可选；WAITING_INPUT、通用外部工具恢复和 Linux 隔离验收属于后续阶段。下表及后续工具表是累计目标，未进入 P0 的项目应标后续/未启用，不能记 PASS；P0 必需项 FAIL/BLOCKED/UNSUPPORTED 均阻止 P0 完成。

macOS 开发配置只用于本地验证，不要求 VM 或 Docker；业务契约保持不变。基线见 [macOS 开发配置](QwenPaw_Multiuser_Harness_Implementation_Baseline_v5.md#macos-dev) 与 [P0 readiness](QwenPaw_Multiuser_Harness_Implementation_Baseline_v5.md#p0-readiness)。

| 项目 | macOS 开发配置 | 判定与边界 |
|---|---|---|
| Worker/Slot | 一个进程 Worker、一个 Slot | 每个 Attempt 创建新进程及私有路径；不复用上一次 Attempt 的进程或临时目录 |
| 原生隔离 | 真实 native Seatbelt task wrapper 仍待实现 | 未实现前不得宣称原生 Runner 或隔离已通过；禁止 raw native command fallback |
| 覆盖测试 | native/fixture 测试覆盖 tenant、auth、run、idempotency、PG 并发、File API、checkpoint、memory、cancel、`WAITING_INPUT` | 默认 fixture/offline 测试，不使用真实 secrets；Mock-only 测试不能认证 native Runner 或隔离 |
| Linux 专属边界 | cgroup、PID/net namespace、K8s NetworkPolicy、OOM isolation | macOS 不能通过这些项目，统一报告 `UNSUPPORTED`，不得报告 `PASS` |
| PostgreSQL | 本机真实 PG engine，或 SQL 测试使用远程测试 PG | 不得用 SQLite 替代实际 PG 引擎 |
| 生产门槛 | P0–P3 Pod 规则仅适用于 `linux-production` | 不把 macOS 开发结果升级为生产隔离保证 |

## UNKNOWN 与恢复兜底矩阵

`UNKNOWN` 是事实状态，表示原操作结果无法确认；不得写成成功，也不得假定已中止。它不必然永久等待：按下表选择动作，并保留原 `operation_id` 与 UNKNOWN 记录。

| 情形 | 处置 | 约束 |
|---|---|---|
| 结果未知且无法安全补偿 | `FAIL_WITH_UNKNOWN_OUTCOME`，终态 `FAILED` 并带 warning | 失败不抹除 UNKNOWN 事实 |
| 需要外部对账 | `WAIT_FOR_RECONCILIATION` | 必须有 deadline；到期按失败处理并保留 UNKNOWN |
| 已证明幂等且操作相同 | 正常重试 | 使用同一 `operation_id`；按既定重试预算执行 |
| 可能重复但属于低风险精确动作 | `RETRY_WITH_DUPLICATE_RISK` | 新建 ID，以 `retry_of` 关联原 ID；需平台批准、用户事前对具体工具/动作范围的授权及有界重试次数 |
| 可选步骤结果未知但允许跳过 | `SKIP_WITH_WARNING` | 标记结果不完整，保留未知事实；不得把低风险作为通用重试依据；发送、支付、部署等高风险操作不得通用授权重试 |

幂等重试继续使用同一 `operation_id`；可能重复的重试必须生成新 ID，并以 `retry_of` 关联原 ID，原 UNKNOWN 历史按审计保留策略保存，不因重试改写为未执行。不得使用 `ASSUME_ABORTED`。只有选择 `WAIT_FOR_RECONCILIATION` 且设置 deadline 才等待；否则按表中终态、重试或跳过规则处理。

## 工具注册清单（仓库事实）

注册入口是 [`runtime/tool_registry.py`](../../src/qwenpaw/runtime/tool_registry.py) 的 `@tool_descriptor` 与 [`agents/tools/__init__.py`](../../src/qwenpaw/agents/tools/__init__.py) 的导入；治理身份来自 [`governance/tool_registry.py`](../../src/qwenpaw/governance/tool_registry.py)。同类别不代表共享授权，每个工具身份分别登记策略与验收结果。

| 类别 | 工具身份（Python 名 → policy 名） | 隔离边界 | 取消/恢复 | 启用门槛 |
|---|---|---|---|---|
| 文件 | `read_file→Read`、`write_file→Write`、`edit_file→Edit`、`append_file→Append`、`grep_search→Grep`、`glob_search→Glob`、`ast_search→AstSearch`、`send_file_to_user→SendFileToUser` | 独占 workspace 临时层 | 本地停止；从 checkpoint 重放文件影响 | P3 L2；L3 文件适配器验证 |
| 网络 | `web_search→WebSearch`、`web_fetch→WebFetch`、`view_image→ViewImage`、`view_video→ViewVideo` | 受控出口与凭据范围 | 只读可重试；远程 UNKNOWN 按兜底矩阵处置 | P3 L2；逐适配器 L3 |
| 浏览器 | `browser→Browser`（统一/deprecated 二选一别名） | 独立 browser runtime、workspace 绑定 | 本地停止；外部效果 UNKNOWN | P3 L2；无 blanket replay safety |
| Shell | `execute_shell_command→Bash` | OFF 模式直接 ALLOW-all 且可不附加 `sandbox_config`；云端拒绝裸跑 | 停止进程树；外部效果 UNKNOWN | P3 前禁用；L3 单独验证 |
| 内部 | `get_current_time→GetCurrentTime`、`set_user_timezone→SetUserTimezone`、`get_token_usage→GetTokenUsage`、`list_agents→ListAgents`、`chat_with_agent→ChatWithAgent`、`submit_to_agent→SubmitToAgent`、`check_agent_task→CheckAgentTask`、`spawn_subagent→SpawnSubagent`、`delegate_external_agent→DelegateExternalAgent`、`run_tool_batch→RunToolBatch`、`activate_f1_exploration_mode→ActivateF1ExplorationMode` | Run/Session scope | 主 Run 取消并收拢；脱离主 Run 子 Agent 不恢复 | P3 L2；后台 Agent 首期关闭 |
| 动态/非描述器 | `recall_history→RecallHistory`、`recall_history_python→RecallHistoryPython`、`memory_search→MemorySearch`、`memory_remember→MemoryRemember`、`recover_visual_context→RecoverVisualContext` | 各 backend/feature 适配器声明 | 未声明则 UNKNOWN/阻断 | `RecallHistoryPython` 需 sandbox；其余逐项探测 |

上述分类的具体开放规则以下表为准，不能根据同一行其他工具通过验收就批量开放：

| 能力 | 拟开放范围 | 取消、隔离与恢复限制 |
|---|---|---|
| 文件 Read/Write/Edit/Append/Grep/Glob/AstSearch | P2 私有文件根的安全读写；P3 才开放 L2 | 排空本地写入；只重放验证过的步骤，或恢复 file_effect；链接/路径/规模需对抗测试 |
| SendFileToUser | P2 改接 Artifact 提交链路后开放 | 不直接复用原生渠道发送；稳定 commit_id 查回，停止传输不撤销已发布结果 |
| WebSearch/WebFetch/ViewImage/ViewVideo | P2 只读子集，P3 恢复 | 本地文件限私有根，网络限受控出口，媒体解析在沙箱内；关闭连接不代表远程请求撤回 |
| Browser | P3 只读 profile 验证后；写入/提交默认关闭 | 独占 profile、进程、出口；不恢复浏览器句柄，提交表单等未知效果转对账 |
| Bash | P3 离线白名单场景；通用联网 Shell 默认关闭 | 强制子沙箱、清理进程树；不继承模型凭据/控制 FD；任意脚本不默认可重放 |
| GetCurrentTime/GetTokenUsage | P2 范围绑定后开放 | 只读可重试；时间结果可以改变，Token 用量以 Core 为准 |
| SetUserTimezone | 完成 Scope 配置适配前关闭 | 配置改写随 revision 提交，禁止修改跨用户全局配置 |
| ListAgents | 完成当前 Scope 可见性过滤后开放 | 只列批准的配置与对象，不列其他用户 Agent |
| Agent 调用/后台委派/批处理/F1 模式 | 首期默认关闭，受管子任务另行验收 | 子调用逐项受权、取消须收拢；首期受管工具串行，不用批处理绕过账本 |
| 动态 Memory/History/Visual 工具 | 逐工具映射与六方法协议通过后开放 | 固定 Session/Workspace 版本；Python history REPL 另受子沙箱约束 |
| MCP（动态 server/tool/version） | 默认关闭；P3 已验证只读工具逐项开放 | stdio 子进程受任务边界；HTTP 经批准网关；断开不等于取消；写操作需幂等/查询/UNKNOWN 策略 |
| LSP/其他插件 | 未盘点默认关闭 | 私有根和进程；后台写入、取消、状态导出逐项验证 |
| computer_use、共享桌面、Cron、Heartbeat | 首期关闭 | 不暴露宿主桌面，不创建脱离 Run 的后台执行 |

Shell 源码依据：[tool_adapter.py](../../src/qwenpaw/governance/tool_adapter.py#L215) 的 OFF 路径可不附加 sandbox_config，审批后也存在移除沙箱参数重试的路径。平台强制边界须独立于原生审批开关。Skills 只提供当前 Scope 的批准内容，无权改变工具权限。

插件工具由 [`PawApp.register`](../../src/qwenpaw/pawapp/app.py#L748) 动态加入并按 owner 可撤销。仓库 bundle manifest 当前为 `chrome`、`cloudpaw`、`computer-use`、`omp-workflows`、`qwenpaw-pet`；其中明确注册 Agent tool 的是 `computer-use→computer_use`（见 [`plugin.py`](../../plugins/bundle/computer-use/plugin.py#L137)），其余能力按插件实际注册结果逐项盘点，未验证即拒绝。插件声明或名称不能替代隔离、取消、恢复证据。

## 内存后端清单与协议门槛

注册表见 [`base_memory_manager.py`](../../src/qwenpaw/agents/memory/base_memory_manager.py#L843)，当前核心注册项是 `none`（禁用，见 [`dummy.py`](../../src/qwenpaw/agents/memory/dummy.py#L11)）与 `remelight`（ReMe Light，见 [`reme_light_memory_manager.py`](../../src/qwenpaw/agents/memory/reme_light_memory_manager.py#L212)）。仓库还提供插件 backend：`adbpg`（[`plugin.py`](../../plugins/memory/adbpg/plugin.py#L8)）与 `powercontext`（[`plugin.py`](../../plugins/memory/powercontext/plugin.py#L8)）；二者均网络依赖，默认关闭，需安装、版本/协议探测、scope 隔离和六步协议验证后启用。插件可通过 [`plugins/api.py`](../../src/qwenpaw/plugins/api.py#L347) 注册其他 backend；未知 ID、缺依赖、版本不符或正在卸载均拒绝，不自动降级。

多用户 Memory protocol 由主设计统一定义为：`probe → quiesce → export_snapshot → validate_snapshot → restore_snapshot → close`。实现前只记录能力，不宣称已有云端快照/跨 Worker 保证。取消须先提交意图并停止新调用；正在运行的后端先 quiesce，无法取消的远程作业标记已发起或 UNKNOWN；恢复只使用通过校验的 snapshot，索引可重建但不得跨 scope 检索。复用已有 Memory 时先复用 source，再复用 index；source 与 index 必须分离。可选 `base + delta` 仅在支持删除、顺序、链长上限并经校验后启用；GC 仅可在闭包校验后清理，且必须保留仍有任何 live reference 的每个 base/delta。禁止把原始活动 WAL 当快照。

| backend | 隔离与取消 | 恢复要求 | 首期选择 |
|---|---|---|---|
| `none` | 无后台 Memory 写入；取消幂等 | 显式 memory=null，Conversation 仍持久化 | Standalone 默认；Workspace 可显式关闭 Memory |
| `remelight` | Attempt 私有存储；停止提炼/索引；quiesce/close 失败传播 | 固定 ReMe/schema/embedding 版本，源数据一致导出，索引校验或重建 | P0 验证候选；P3 六方法与故障测试通过后作为首个持久后端 |
| `adbpg` | 远程数据库固定 Scope 的受限能力；本地取消不能撤销已执行写入 | 固定 revision 的一致导出/保留，未知写入可对账 | 默认关闭；不得注入共享数据库管理凭据 |
| `powercontext` | 远程服务固定 Scope/namespace；后台作业停止或对账 | 完整导出或持久版本引用、索引兼容；只返回“最新记忆”不合格 | 默认关闭，协议验证后另开放 |

这些远程后端要求尚待验证，不表示插件已经具备。六方法为必需基线；同 Attempt 多片段继续还须支持生命周期契约中的 `release_barrier`，否则在已提交边界关闭并创建新 Attempt。

事件日志的 L1 保证是 durable facts 与 message snapshot 可补读；transient token tail 允许丢失，不能冒充事实。事件、产物和恢复记录均继承 tenant 与私有 Workspace Scope。

## 验收

P0 多会话必需断言见 [P0-S01/P0-S02](QwenPaw_Multiuser_Harness_Implementation_Baseline_v5.md#p0-session-tests)：同一用户多个 Standalone Session 跨 Worker 恢复后历史独立；同 Workspace 多 Session 单写且提交不覆盖其他会话引用。均使用 Memory=none、合成标记、真实 Runner 与持久提交证据，分别纳入 G3/G5。共享 Workspace 文件不属于本用例的会话隐私隔离承诺。

1. 启动时输出完整 registry inventory（内置、动态、插件、Memory），每项带 source、version、scope、tool type、sandbox、cancel、L2/L3 状态及 `fallback_action`、`auth_ref`、`retry_budget`；未列项调用被拒绝。
2. 两用户使用同名工具/会话/文件/记忆时，路径、事件、产物和检索均分域；同一 workspace 单写者。
3. Worker 杀停后只能从已提交边界继续；本地离线写从 checkpoint 重放，外部 UNKNOWN 按上述兜底矩阵处置。
4. Memory 后端逐项通过上述六步协议和版本探测；插件卸载前无活动实例/选择租约。

关联生命周期与事务设计：[Runner Lifecycle](QwenPaw_Multiuser_Runner_Lifecycle_v1.md)、[State Transactions](QwenPaw_Multiuser_State_Transactions_v1.md)。
