# P1 可信任务执行边界：Kylin V10（Tercel）

> 本文自 2026-09-14 起作为 P1 执行边界与验收范围的权威定义；如与《QwenPaw 多用户云端长程任务 Harness：实施基线 v5》中“每 Attempt 强沙箱”的通用设计冲突，P1 以本文为准。强沙箱设计保留为未来接入不可信用户或开放任意代码前的独立安全阶段，不计入当前 P1。

## 1. 已冻结前提

| 项目 | P1 决定 |
|---|---|
| 用户信任 | 当前接入用户均为可信用户；P1 防止资源争抢、误操作、状态残留和意外串域，不承诺抵御恶意租户或容器逃逸 |
| 目标操作系统 | Kylin Linux Advanced Server V10（Tercel）；具体 SP、内核和 CPU 架构在部署验收时记录 |
| Kubernetes | v1.21.7；按现有集群 API 兼容实施。该 minor 已于 2022-06-28 结束上游维护，最终补丁为 v1.21.14；验收报告必须保留版本风险 |
| Worker 拓扑 | 常驻 Worker Pod 池；一个 Pod 一个 Worker、一个 Slot；一个 Slot 同时只运行一个 Attempt |
| Pod 生命周期 | 不按用户或 Run 动态创建 Pod/Job；同一 Worker Pod 可顺序服务不同可信用户 |
| 执行方式 | 每 Attempt 启动全新 Runner 进程并使用独立可写目录；P1 不要求 Bubblewrap、Landlock、gVisor、Kata、MicroVM 或其他任务级强沙箱 |
| 主要边界 | Pod/container cgroup 资源限制、Pod SecurityContext、Pod 级 NetworkPolicy、应用层时限与输出限制、Attempt 环境清理 |
| 对外声明 | 只能声明“可信任务的 Pod 资源隔离与环境清理通过”，不得声明“不可信任意代码强隔离通过” |

“可信用户”不等于输入天然安全。模型输出、上传内容、路径、归档和工具返回仍按不可信数据处理，继续执行大小、类型、范围、链接和路径穿越校验；本次只收窄执行者威胁模型，不撤销数据校验。

## 2. P1 范围

### 2.1 Linux 运行配置与身份

- 新增明确的 `kylin-v10-trusted` 运行 profile；不得复用 `macos-dev` 名称冒充 Linux 验收。
- Core、File API、Worker 继续使用独立入口和版本化 HTTP/FileRef 契约。
- Worker 保持单 Slot，并登记镜像 digest、QwenPaw commit、依赖锁、Python、OS、内核、架构、容器运行时和 Kubernetes 版本。
- P1 使用固定 Worker 池，不赋予 Worker 创建 Pod、Job、PVC、Service 或修改集群策略的权限。

### 2.2 Pod 资源边界

- 为 Worker 容器固定并实测 CPU、内存和 `ephemeral-storage` 的 requests/limits。
- 进程数使用 kubelet 的 per-Pod PID 上限；所有承载 Worker 的节点必须采用一致配置。
- 执行时长、停止宽限、单流输出、单文件大小、文件数量和工作目录总量继续由应用层做有界控制。
- CPU 超限应被节流；内存、PID、临时盘超限必须产生可观测失败，Worker 不得把失败结果发布为成功。
- P1 只验收单 Slot；多 Slot 的容量、每槽 cgroup 和同 Pod 故障域仍属于 P4。

资源数值在联调前通过一个命名 profile 冻结。未提供正式数值时可以实现配置、校验和测试夹具，但不能签发 P1 资源验收结论。

### 2.3 Pod 安全基线

Worker 部署至少满足：

- `runAsNonRoot: true`；
- `allowPrivilegeEscalation: false`；
- `capabilities.drop: ["ALL"]`；
- `seccompProfile.type: RuntimeDefault`；
- 根文件系统只读，可写目录仅通过专用 volume/`emptyDir` 挂载；
- 禁止 privileged、`hostNetwork`、`hostPID`、`hostIPC`、`hostPath` 和容器运行时 socket；
- Worker 不需要访问 Kubernetes API 时设置 `automountServiceAccountToken: false`；确需工作负载身份时使用最小 ServiceAccount，不能取得集群管理能力。

这些设置是当前可信任务 P1 的 Pod 安全基线，不升级为任意代码沙箱承诺。

### 2.4 Attempt 私有环境与串域防护

- 每个 Attempt 创建新的 home、tmp、state、workspace、secrets 目录，目录不得复用。
- 在首次导入 QwenPaw 前设置最小环境；不加载真实用户 `.env`、宿主 HOME、系统密钥或无关代理配置。
- Runner 使用新进程启动；不跨 Attempt 复用已经初始化的 QwenPaw Runtime、浏览器 profile、可写依赖目录或后台 Agent。
- 工作区内容只从已授权 FileRef 物化；恢复不得读取前一 Attempt 的本地目录。
- Scope、tenant、session、revision、Attempt 和租约校验继续沿用 P0 契约；可信用户前提不放宽业务数据分域。

### 2.5 进程终止、清理与槽复用

- 停止时先停止新工具调用，再终止 Runner 及其已知后代，并确认主进程和管道全部关闭。
- 删除本 Attempt 的可写目录、临时文件和短期能力；持久业务引用按 File API/Core 协议保留，不用本地清理代替业务 GC。
- 清理结果必须独立上报。无法确认清理成功时将 Worker 标记为 `QUARANTINED`，停止领取新任务，等待 Pod 重启或平台回收。
- 顺序执行不同 tenant 的测试必须证明 HOME、tmp、state、workspace、环境变量、文件和进程不残留。

P1 在可信用户前提下接受“进程树清理 + 私有目录销毁 + 失败停用 Worker”的边界；它不保证恶意进程无法逃逸或隐藏。

### 2.6 Pod 级受控网络

- NetworkPolicy 默认拒绝 Worker Pod 的非必要 ingress/egress；仅允许 DNS、Core、File API、批准的模型/Embedding/工具网关及明确的观测端点。
- NetworkPolicy 必须由实际 CNI 正向和负向验证；仅创建 API 对象不算通过。
- 不使用 `hostNetwork`；不向 Runner 暴露 PostgreSQL、对象存储管理端、其他 Worker 或集群管理面。
- P1 不建设 Runtime 与 Shell/Python 之间的独立网络命名空间。因此如果开放 Shell/Python，它们会共享 Worker Pod 网络权限，这属于可信用户范围内接受的残余风险，必须写入能力清单。
- 域名级出口控制如有要求，应通过受控网关或现有网络设施完成；Kubernetes NetworkPolicy 只承担 Pod 级地址/端口边界。

### 2.7 工具范围

- P1 不因取消强沙箱而自动扩大工具开放范围。
- 默认继续使用 P0 已验证的离线模型和固定本地工具完成执行边界验收。
- Shell、Browser、远程 MCP、插件安装、后台 Agent、Cron/Heartbeat 的产品开放仍按能力矩阵和 P2/P3 阶段执行。
- 若 P1 联调提前开放某项工具，必须单独记录它获得的文件、网络、凭据、取消和清理能力；该单项结果不能推广到其他工具。

## 3. 明确不属于 P1

- 面向恶意租户的任务级系统沙箱、内核攻击面隔离或容器逃逸防护；
- Bubblewrap/Landlock/gVisor/Kata/MicroVM 的选型、安装和验收；
- 每 Attempt 独立 PID/net namespace、工具子沙箱或 Runtime/工具分权网络；
- 每 Run 动态创建和销毁 Pod/Job；
- P4 多 Slot、高密度资源切分、容量压测和自动扩容；
- P2 的生产 BFF 身份、完整配额/公平调度、生产对象存储与事件链路；
- P3 的 ReMe 一致快照、WAITING_INPUT、暂停、通用副作用账本和故障续跑。

如果未来出现不可信用户、允许任意脚本或监管要求强执行隔离，必须先新增并通过独立的强沙箱阶段，再变更本节对外声明，不能把当前 P1 结果直接沿用。

## 4. 工程任务

| 编号 | 任务 | 完成标准 |
|---|---|---|
| P1-01 | Kylin Linux profile | `kylin-v10-trusted` 配置、角色入口和运行身份通过校验；macOS 与 Linux 证据分开 |
| P1-02 | Worker 镜像与清单 | 独立 Worker 镜像；单 Pod/Worker/Slot；安全上下文、只读根、专用可写卷和最小 ServiceAccount 明确 |
| P1-03 | 资源限制 | CPU、内存、临时盘、PID、时限、文件和输出限制配置化并有超限证据 |
| P1-04 | Attempt 环境 | 私有目录、最小环境、新 Runner 进程、FileRef 物化和跨 Attempt 不复用通过测试 |
| P1-05 | 清理与隔离 | 正常/超时/取消/异常退出均清理；失败进入 `QUARANTINED`，不继续领取 |
| P1-06 | 受控网络 | CNI 实际执行默认拒绝；必要 DNS/Core/File/测试网关成功，禁止目标失败 |
| P1-07 | 验收与报告 | 固定镜像与平台指纹；正负向证据、残余风险、未启用工具和 Kubernetes 版本风险完整 |

## 5. P1 验收门槛

| 门槛 | 必须满足 |
|---|---|
| G1 平台可复现 | 记录 Kylin V10（Tercel）、SP/内核/架构、Kubernetes v1.21.7、容器运行时、CNI、节点 cgroup/PID 配置和镜像 digest |
| G2 单槽部署边界 | 一个 Worker Pod 只登记一个 Slot；无 privileged/host namespace/hostPath/runtime socket；显式 seccomp 和最小权限 |
| G3 资源约束 | CPU、内存、PID、临时盘、执行时间、输出和文件限额逐项实测；超限不发布虚假成功 |
| G4 顺序隔离与清理 | 两个 tenant 顺序运行不串文件、HOME、状态、环境或进程；清理失败停止领取并触发 Pod 回收 |
| G5 受控网络 | 默认拒绝实际生效；必要 DNS/Core/File/测试网关成功；数据库、管理面和未批准目标不可达 |
| G6 P0 回归 | 租约、幂等、Scope、FileRef、checkpoint、恢复和旧 Attempt fencing 不因 Linux profile 回归 |

最终报告必须使用以下结论之一：

- `PASS — trusted workload pod boundary`：六道门槛全部通过；
- `BLOCKED`：缺少真实 Kylin/Kubernetes/CNI/kubelet 环境或资源参数；
- `FAIL`：已具备条件但正向或负向用例失败。

不得使用 `PASS — sandboxed untrusted workload` 或任何等价表述。

## 6. 验收前仍需冻结的信息

| 信息 | 当前状态 |
|---|---|
| Kylin SP、内核和 CPU 架构 | 未提供；实现可继续，G1 前必须记录 |
| 容器运行时及版本 | 未提供；影响 seccomp、cgroup 与镜像运行证据 |
| CNI 及 NetworkPolicy 能力 | 未提供；阻塞 G5 |
| CPU/内存/临时盘/PID/时限正式数值 | 未提供；阻塞 G3 |
| Core、File API、DNS、模型/测试网关允许地址与端口 | 未提供；阻塞 G5 |
| Kubernetes v1.21.7 生命周期风险接受或升级计划 | 未确认；必须进入验收报告和上线审批 |

这些信息不阻塞 P1-01 至 P1-05 的代码与清单骨架，但阻塞真实环境验收和 `PASS` 结论。

## 7. 平台依据

- Kylin 官方升级资料将 V10 SP1 标识为 `Kylin Linux Advanced Server V10 (Tercel)`，并展示 4.19 系列内核；P1 仍以目标节点实测结果为准：[麒麟 V10 升级资料](https://product.kylinos.cn/static/img/2024/12/e6ba94c6864e40d71ba86fa62eec80cc.pdf)。
- Kubernetes 官方版本历史记录 v1.21 的最终补丁为 v1.21.14、EOL 日期为 2022-06-28：[Kubernetes Patch Releases](https://kubernetes.io/releases/patch-releases/)。
- Kubernetes 的 seccomp profile 字段自 v1.19 稳定；P1 清单显式指定 `RuntimeDefault`，不依赖集群默认行为：[Seccomp and Kubernetes](https://kubernetes.io/docs/reference/node/seccomp/)。
- per-Pod PID 限制通过 kubelet `PodPidsLimit`/`--pod-max-pids` 配置，不是单 Pod Spec 资源字段：[Process ID Limits and Reservations](https://kubernetes.io/docs/concepts/policy/pid-limiting/)。
- NetworkPolicy 只有在 CNI 实现策略时才产生实际限制，因此 G5 必须包含网络正负向探测：[Network Policies](https://kubernetes.io/docs/concepts/services-networking/network-policies/)。
