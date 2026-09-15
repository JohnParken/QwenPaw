# 精简办公 Agent：待解决事项

更新时间：2026-09-15

## P0：推送后必须先处理

- [ ] 重写 GitHub Actions。现有工作流仍引用已经删除的 `console/`、
  `website/`、`plugins/`、`tests/`、`e2e/` 和 `deploy/Dockerfile.web`。
- [ ] 将主 CI 收敛为 `cloud/tests`、六个 Skill readiness、Node audit、
  Python 锁校验和 `Dockerfile.office-worker` 构建。
- [ ] 保留必要的 CodeQL、安全扫描和 PR 治理；不要直接恢复旧产品构建。

## P1：运行边界

- [ ] 将 `src/qwenpaw/` 中仍被 `OfficeAgentBridge` 使用的代码抽取为独立
  `qwenpaw_core`：Agent、Schema、TL/OpenAI Provider、Skill Scanner 和必要
  Context。
- [ ] 完成抽取后删除 Browser、Coding、MCP、Memory、Channel、Cron、Hub、
  本地模型和其他兼容遗留模块，并进一步缩减基础依赖。
- [ ] 当前 `qwenpaw_cloud.__main__.Config` 仍以 `macos-dev`、loopback URL 和
  `_test` PostgreSQL 为严格基线；需要增加独立生产配置模型，不能直接放宽
  测试边界。
- [ ] 为 Core、File API 和 Worker 分别提供生产镜像/Deployment；目前仓库仅有
  Office Worker 镜像与 Worker Kubernetes 模板。

## P1：文件与 BFF

- [ ] 接入真实可信 BFF 身份认证和对象存储 File API。
- [ ] 验证上传 complete、不可变 FileRef、Artifact 发布及响应丢失后的幂等查回。
- [ ] 增加 Attempt 自动清理、Artifact 保留期限和失败发布补偿。
- [ ] `qwenpaw_test_bff` 和 `office-agent-console` 仅限开发环境，不能作为生产 BFF。

## P1：Agent 能力

- [ ] 使用真实 TL/OpenAI 模型完成 DOCX、XLSX、PPTX 的第一轮生成和第二轮修改。
- [ ] 记录 Skill 选择、脚本调用、结构验证、失败原因和模型输出，形成可复跑报告。
- [ ] 确认固定 Skill 脚本足以创建三件套；当前受控模式禁止任意 Python/Node
  内联代码，缺少固定生成脚本时 Agent 可能只能读取而不能完成复杂生成。
- [ ] 明确结构校验不等于 Office/WPS 最终渲染、分页、字体、对象重叠或公式重算。

## P2：生产验收

- [ ] 构建并启动真实 `Dockerfile.office-worker` 镜像。本机目前没有 Docker CLI。
- [ ] 完成 20、50、100 并发 Run 的 tenant/session/file/snapshot 隔离测试。
- [ ] 验证 Worker 失租、中断、整轮重试一次、旧 Attempt 禁止发布和进程树清理。
- [ ] 配置生产 PostgreSQL、备份、迁移/回滚、监控、告警和日志脱敏。
- [ ] 评估 Redis 是否仅承担容量、实时输出和短期协调，不改变 PostgreSQL 最终状态职责。

## 当前已验证

- Cloud 测试：`32 passed, 20 skipped`。
- 六个 Skill inventory/readiness 全部通过。
- Node 生产依赖审计：0 个已知漏洞。
- Python 编译、依赖锁和 diff 检查通过。
- 当前分支：`codex/slim-office-agent`。
- 精简提交：`b0a4b13d`。

## 安全恢复点

- 原完整实现仍保留在 `office` 分支（`847dba15`）。
- 本地 `stash@{0}` 保存了分支切换期间发现的未跟踪草稿；Git stash 不会随分支推送。
