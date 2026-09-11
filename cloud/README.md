# P0 多用户 Harness（macos-dev）

实施范围以 `docs/design/QwenPaw_Multiuser_Harness_Implementation_Baseline_v5.md` 第 20.3 节为准。入口与桌面 Hub/Console 分离；采用真实 PostgreSQL、独立 Core/File API/单槽 Worker、整个 Runner 的 Seatbelt 边界、离线模型与 NullMemory。所有数据均为合成测试数据。

## 使用 uv 重建环境

从仓库根运行；Python 必须是 **3.12.11**，不隐式替换为 3.12.12。

```sh
uv python install 3.12.11
uv venv --python 3.12.11 .venv
uv pip sync --python .venv/bin/python cloud/requirements.lock
PYTHONPATH=src uv run --no-project --python .venv/bin/python -c 'import agentscope, fastapi, psycopg; print("imports OK")'
```

`cloud/requirements.lock` 是 uv 解析的完整固定版本依赖集合，包括 `test` 与 `cloud` extras。更新依赖属于新的 Runtime 版本，需重新验收：

```sh
uv pip compile pyproject.toml --extra test --extra cloud --python .venv/bin/python --output-file cloud/requirements.lock
```

本次验收在项目外 `/private/tmp/qwenpaw-p0-python` 安装了 uv 管理的解释器；该临时目录删除后按上述步骤重建 `.venv`。业务数据、服务测试签名密钥和运行目录也在项目外，未读取真实用户配置或模型凭据。

## PostgreSQL 测试前置

使用专用 UTF-8 PostgreSQL 测试库，库名以 `_test` 结尾。设置 `P0_TEST_DSN`，不得指向生产库。完整验收需要在测试实例上建测试库的权限，每个集成场景建立独立数据库并保留事实与引用。

本次实测 PostgreSQL **17.6**，本机源码构建，无 Docker/虚拟机；因 Homebrew 无法识别本机系统版本，使用了以下独立构建方式：

```sh
curl -fL https://ftp.postgresql.org/pub/source/v17.6/postgresql-17.6.tar.bz2 -o /private/tmp/postgresql-17.6.tar.bz2
mkdir /private/tmp/p0-pg-source
tar -xjf /private/tmp/postgresql-17.6.tar.bz2 -C /private/tmp/p0-pg-source --strip-components=1
cd /private/tmp/p0-pg-source
./configure --prefix=/private/tmp/p0-pg --without-icu --without-readline --without-zlib
make -j6
make install
mkdir -m 700 /private/tmp/p0-database
/private/tmp/p0-pg/bin/initdb -D /private/tmp/p0-database/data -U p0test --encoding=UTF8 --no-locale --auth=trust
/private/tmp/p0-pg/bin/pg_ctl -D /private/tmp/p0-database/data -l /private/tmp/p0-database/server.log -o '-h 127.0.0.1 -p 55432 -k /private/tmp/p0-database' start
/private/tmp/p0-pg/bin/createdb -h 127.0.0.1 -p 55432 -U p0test qwenpaw_p0_test
```

此示例是仅本机合成数据的测试实例，`trust` 认证不是生产配置。本次已建实例使用 `/private/tmp/qwenpaw-p0-pg` 和 `/private/tmp/qwenpaw-p0-dev/pgdata`，数据库为 `qwenpaw_p0_utf8_test`。验收后实例已停止，数据保留；复跑现有实例前先运行：

```sh
/private/tmp/qwenpaw-p0-pg/bin/pg_ctl -D /private/tmp/qwenpaw-p0-dev/pgdata -l /private/tmp/qwenpaw-p0-dev/postgres.log start
```

## 独立角色启动

在仓库根设置测试 DSN，并生成全新私有目录（命令不会自动启动服务）：

```sh
export P0_TEST_DSN=postgresql://p0test@127.0.0.1:55432/qwenpaw_p0_utf8_test
PYTHONPATH=src uv run --no-project --python .venv/bin/python cloud/bootstrap.py --root /private/tmp/qwenpaw-p0-example --executor native
```

在不同终端启动服务：

```sh
PYTHONPATH=src uv run --no-project --python .venv/bin/python -m qwenpaw_cloud files --config /private/tmp/qwenpaw-p0-example/files.json
PYTHONPATH=src uv run --no-project --python .venv/bin/python -m qwenpaw_cloud core --config /private/tmp/qwenpaw-p0-example/core.json
```

Worker 默认常驻、按单槽顺序领取，收到 SIGTERM/SIGINT 停止接单并停止活动 Runner。加入 `--once` 仅执行一个片段，无任务返回 `IDLE`，适合观察交接。两个进程可并发运行，各自只有一个 Slot：

```sh
PYTHONPATH=src uv run --no-project --python .venv/bin/python -m qwenpaw_cloud worker --config /private/tmp/qwenpaw-p0-example/worker-a.json
PYTHONPATH=src uv run --no-project --python .venv/bin/python -m qwenpaw_cloud worker --config /private/tmp/qwenpaw-p0-example/worker-b.json
```

Worker 配置拒绝 DSN 和服务签名密钥，只收到自身身份令牌与分配后获得的 Scope 文件能力。运行端不继承父进程代理、模型凭据、数据库环境或 FD。未通过 Seatbelt 的 native 启动不会降级成裸进程。可用 `--executor fixture` 生成明确标记的协议桩配置；fixture 不加载 QwenPaw，也不计入真实 Runtime 验收。

## 复跑验收

```sh
export P0_TEST_DSN=postgresql://p0test@127.0.0.1:55432/qwenpaw_p0_utf8_test
PYTHONPATH=src uv run --no-project --python .venv/bin/python cloud/accept.py --native --evidence-root /private/tmp/qwenpaw-p0-acceptance-new
```

使用新的证据目录；测试运行期间不要修改源码或依赖，指纹变化会拒绝恢复。测试需要本机网络、创建 PG 测试库和 `sandbox-exec` 权限。在 Codex 外层沙箱阻止这些操作时，需要使用对应执行权限，不能关闭内层 Seatbelt 绕过验收。

输出包括 `acceptance.json`、`junit.xml`、`pytest.log`、每个场景的独立服务配置/日志以及 S01/S02/F04 的结构化证据。证据目录含短期合成测试令牌，保持 0700；仓库报告不复制令牌。`cloud/reports/acceptance.json` 是汇总，人工审查报告见 `docs/design/P0_Implementation_Acceptance.md`。

## 协议与恢复约束

- `X-Protocol-Version: p0.v1`；服务令牌验证签名、issuer、audience、role、subject、期限及 Scope/分配关系。协议样本与 OpenAPI 在 `cloud/contracts/`。
- Run 创建和 claim 使用稳定 `request_id`；相同 ID 改参冲突。claim 结果可查询，过期的原分配不会被同 ID 替换。
- ready/heartbeat 必须属于当前 Attempt 和 epoch，且租约未过期。租约以 PG 时间为准；Worker watchdog 以发送时刻的 monotonic 时间保守计时，迟到回复不复活执行。
- File API 的 `POST /v1/uploads` 同步完成小文件上传与 complete，返回 `{status: READY, ref: FileRef}`；固定 version=1，文件 ID 不可变。不返回存储定位信息。
- checkpoint 先持久 PREPARING，再经独立 File API pin 完整引用集合，持久 PINNED，最后复核租约/revision 发布 COMMITTED。稳定 `commit_id` 可查回发布结果。原生导出是 JSON，不执行 pickle/代码反序列化。
- 一个 Scope 至多一个有效写 Attempt，终态后仍须清理确认才能复用槽。Workspace 提交仅替换当前 Session 的 Conversation 引用，保留其他会话与共享文件。新会话只初始化空历史，仍加载当前 Workspace 文件。
- native 是实际 `Workspace.stream_query → Runtime → QwenPawAgent`。离线 ChatModel 只调用批准的 `write_marker`，两段使用固定 marker，保存完整工具关联、文件、游标与累计片段预算。每个已提交边界结束 Runner，恢复使用新进程和 File API。

## P0 边界及 P1 差距

这是源码运行的固定、小规模 P0 验证：全局静态准入上限 32 个未结束 Run，数据库 admission 行串行化事务；不声称已完成公平调度、吞吐或生产配额。参数在 `Limits` 固定并校验，单次原生片段默认 60 秒、租约 12 秒、续租间隔 1 秒、安全余量 3 秒、请求超时 2 秒、停止宽限 1 秒、每流输出 64 KiB、单文件 1 MiB。

不启用模型联网、Shell/浏览器/MCP、插件安装、WAITING_INPUT、通用外部副作用恢复或自动 GC。失租恢复仅适用于此无外部副作用、私有文件且发布受 fencing 保护的固定 profile。未决与已用引用全部保留；清理只销毁 Attempt 工作副本，不删除业务引用。测试后先停服务，再显式重置专用测试环境；不以重置代替 P2 GC/补偿验收。

Linux cgroup、PID/net namespace、跨 Pod 资源隔离、生产身份/存储、长期 Memory、业务取消/暂停链路、完整公平调度与发布打包属于后续。Seatbelt 仅声明已测试的文件/网络边界及受控后代清理，不提供 Linux 级资源硬隔离。ReMe 调查结果见 `cloud/reports/reme-investigation.md`，不以 NullMemory 的通过结果声称 ReMe 已支持恢复。
