# P0 工程实施与验收报告

**验收结论：P0 通过。G1—G5 全部 PASS；35/35 项 P0 测试通过（0 失败、0 跳过），另有 34 项上游回归通过。**

实施依据：多用户 Harness 实施基线 v5 第 20.3 节，以及 Runner、状态事务、能力矩阵 v1。仅验收 **macos-dev P0**，不宣称生产多用户隔离已经完成。

最终机器判定、源码指纹和测试数量以 [acceptance.json](../../cloud/reports/acceptance.json) 为准；环境信息见 [environment.json](../../cloud/reports/environment.json)。环境重建、角色启动、PG 测试库准备及复跑命令见 [cloud/README.md](../../cloud/README.md)。

## 实施交付

| 阶段 | 实施内容 | 证据 |
|---|---|---|
| P0-01 | uv 管理 Python 3.12.11 与独立 `.venv`；固定 255 个依赖；实际安装版本和源码/锁指纹校验 | `.python-version`、`cloud/requirements.lock`、环境报告、依赖漂移拒绝测试 |
| P0-02a | Core、File API、Worker 独立入口；HTTP 客户端；服务签名身份、Scope/分配和协议版本校验 | `src/qwenpaw_cloud/`、`cloud/contracts/`、G2 测试 |
| P0-02b | ExecutionContext、PathMap、fixture Runner、NullMemory 六方法、生命周期失败传播 | fixture 生命周期、同名合成用户私有目录、非法恢复用例 |
| P0-03a/b | 整个 Runner 自启动即位于 Seatbelt；私有 HOME/tmp/state/workspace；环境/FD 白名单；默认拒绝网络 | 文件越界/符号链接/Data 卷别名、真实 HOME、网络、输出洪量、取消/后代、watchdog、清理失败停槽测试 |
| P0-03c | 实际 Workspace.stream_query → Runtime → QwenPawAgent；确定性离线 ChatModel 与 write_marker；JSON 状态导出/重建 | S01/S02 native，包含完整工具调用与结果关联、文件摘要、游标和预算 |
| P0-04 | ReMe Light 依赖及六方法调查；缺失一致快照适配，明确不启用 | [ReMe 调查](../../cloud/reports/reme-investigation.md)；安装版本 reme-ai 0.4.1.11 |
| P0-05a | PostgreSQL 17.6；Core 与 files 独立 schema/事务；File API local_test；pin 后条件发布；引用持久保留 | 两个 SQL 迁移、HTTP/PG 故障及并发测试 |
| P0-05b | 明确屏障处注入响应丢失/发布故障；Core/File API 重启；提交后 SIGKILL Worker A，再由 B 恢复 | F01—F04、S01/S02 的 PG 状态与结构化导出证据 |
| P0-06 | 五道门槛、复跑命令、上游回归、限制和后续差距 | 本报告、机器报告及外部证据目录 |

## 门槛与用例映射

| 门槛 | 必需验收 |
|---|---|
| G1 环境可复现 | Python 3.12.11；uv 0.10.2；固定依赖及实际版本匹配；整个验收期间源码指纹不变；角色配置明确 |
| G2 控制/执行解耦 | 真实独立服务/Worker 进程经 HTTP 交互；身份、audience、Scope、协议版本拒绝；Worker 无 DB/签名密钥配置及 Core/psycopg 导入 |
| G3 真实 Runtime | fixture/native 分别执行；native S01/S02 与提交后杀 Worker 的恢复用例；恢复时验证自身历史、tool_call_id 关联、固定文件和下一片段 |
| G4 本机边界 | 真实 Seatbelt 正负向测试；包括 `/System/Volumes/Data` 别名；有界输出；控制期限；后代清理和未确认清理的槽隔离 |
| G5 持久协议 | 真实 PG 领取竞争、持久幂等记录、单写、CAS、封存/发布及重启查询；S01/S02 引用保留；F01—F04 |

- **F01**：两个认证 Worker 以并发屏障竞争；仅一个分配。事务提交后丢弃响应，用原 request_id 查回同一个 Attempt；不重复占槽。
- **F02**：Core 重启保留事实；到达数据库明确记录的租约期限后，ready/heartbeat 拒绝。watchdog 使用发送时刻的单调时钟，迟到响应不复活，控制链路丢失后固定期限停止。
- **F03**：pin 已持久化但发布前中断，Head 不变；发布后响应丢失按原 commit_id 查回同一次 revision。Core 和 File API 重启后记录可查；未决引用保留。
- **F04**：native Worker A 在已提交的确定屏障处被 SIGKILL；租约过期后扫描并由新 Worker B 通过 File API 继续第二片段。A 的旧 ready/续租/新提交拒绝，已提交结果仍可查，终态不被覆盖。
- **S01**：tenant=t1/user=u1、同 Agent 显示名、不同 s1/s2；分别在新 Worker 恢复。断言 Conversation 不含另一会话标记、工具关联完整、文件摘要正确，推进一方不改变另一方引用。
- **S02**：同 Workspace 的 s1/s2 预置真实已提交 Conversation；两个 Worker 并发竞争，未获准 Run 保持 QUEUED。PG 快照证明只有一个有效写者；清理完成后下一 Run 绑定新 revision，两次提交均保留另一会话的引用和内容。共享 Workspace 文件是预期行为，不声称会话之间的项目文件互相保密。

## 结果与证据

完整 P0 套件包含 **35 项**，fixture 和 native 分开计数；最终结果见机器报告。另有 **34 项上游相关回归通过**，覆盖 Runtime 错误处理、Workspace 路由、服务关闭和配置。Black 23.3.0、flake8、Python 编译、`git diff --check` 和 `uv pip check` 均已检查。

最终完整验收命令：

```sh
P0_TEST_DSN=postgresql://p0test@127.0.0.1:55432/qwenpaw_p0_utf8_test \
PYTHONPATH=src uv run --no-project --python .venv/bin/python \
cloud/accept.py --native \
--evidence-root /private/tmp/qwenpaw-p0-acceptance-20260911-r3
```

临时 PostgreSQL 实例已在验收结束后停止，数据库与引用全部保留；复跑前的启动命令见 cloud/README.md。

证据目录包括 JUnit、pytest 输出、每项集成测试的服务日志，以及 `P0-S01-evidence.json`、`P0-S02-evidence.json`、`P0-F04-evidence.json`。测试使用固定输入和真实 PG 状态，不以模型回答判定隔离正确。短期合成服务令牌仅保存在外部 0700 私有目录，未复制到仓库。

验收过程保留了失败证据：首次整体验收发现格式收尾导致条件更新检查的缩进回归，主代理修复后重新运行全套；另一次开发期运行拒绝了测试中途变化的源码指纹，没有放宽校验来通过测试。最终报告仅引用与交付代码指纹一致的复跑结果。

## 未启用能力与 P1 差距

- **ReMe 一致快照：UNSUPPORTED。** 六方法适配尚不存在，旁路调查已交付；P0 使用明确的 NullMemory，不把它记成 ReMe 集成通过。
- **Linux 生产隔离：UNSUPPORTED / P1 待验。** cgroup、PID/net namespace、跨 Pod OOM、受控出口与生产宿主策略均不由 macOS 结果代替。
- 原生工具范围仅 `write_marker`；真实模型、Shell、浏览器、MCP、外部副作用恢复、WAITING_INPUT、生产 Memory、自动 GC 均未启用，不计作 PASS。
- P0 使用 32 个未结束 Run 的静态准入和 admission 行串行化事务，不提供生产公平调度/配额或吞吐结论。File API 小文件上传与 complete 合为一个同步端点，version 固定为 1。
- 测试数据库及已用/未决引用保留，不自动删除；只有 Attempt 私有工作副本按清理协议销毁。数据库/文件环境重置须停服后显式进行，不能冒充 P2 补偿和 GC。
- P1 需落实 Linux 测试环境、后端能力与部署打包；P2/P3 再完成生产身份、存储、配额、公平调度、运维 GC 与 Memory/通用恢复。

## Subagent execution record

| 调用 | agent / model | 范围与结果 |
|---|---|---|
| spawn `explorer__runtime` | luna_explorer / gpt-5.6-luna | 只读定位 Workspace/Runtime、Session 和 ReMe 接入点，返回源码证据 |
| spawn `luna__files` | luna_medium / gpt-5.6-luna | File API、files 迁移和初始测试 |
| followup `luna__files` | luna_medium / gpt-5.6-luna | 一次范围内修订：并发上传/封存锁、请求体限制、schema 所有权；主代理补充真实 PG 并发/损坏/重启验收 |
| spawn `worker__runner` | luna_worker / gpt-5.6-luna | Runner、离线真实 Agent、NullMemory、entry、恢复与生命周期测试、ReMe 调查；通过消息完成范围内接口对齐和审查修订 |
| spawn `spark__style` | spark_executor / gpt-5.3-codex-spark | 机械格式/lint 修复；后续主代理整体验收发现并修复其引入的一处缩进回归 |

## Main-agent work

主代理负责需求与状态边界、版本化契约、服务身份与权限、Core 持久事务、Seatbelt、watchdog、Worker HTTP 集成、uv 环境、原生 PG 构建与启动，以及对子代理产物的审查和最终修复。独立实现/执行真实并发、响应丢失、服务重启、提交后杀 Worker、S01/S02 跨 Worker 恢复测试，运行上游回归并生成全部验收证据。
