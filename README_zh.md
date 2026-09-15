# QwenPaw 精简办公 Agent

本分支只维护多用户办公 Agent 后端：Core、File API、Worker、Runner、
六个内置 Skill、Attempt 受控执行和 Office 文件结构验证。

## 目录

```text
src/qwenpaw_cloud/       正式服务主线
src/qwenpaw/             暂时保留的原生 Agent Runtime 兼容内核
src/qwenpaw_test_bff/    开发测试 File API
cloud/                   协议、迁移、Kubernetes 与测试
deploy/                  Worker 镜像及锁定 Node 依赖
test-tools/
  office-agent-console/  本地上传、多轮对话及产物下载页面
```

`src/qwenpaw/` 暂时不能继续物理裁剪，因为 `OfficeAgentBridge` 仍复用其中的
`QwenPawAgent`、TL/OpenAI Provider、Schema 和 Skill Scanner。新增功能应进入
`qwenpaw_cloud`，不要恢复旧 Console、插件、频道、官网、MCP 或独立
`qwenpaw.office` 服务。

## 本地验证

```bash
UV_CACHE_DIR=/tmp/uv-cache uv sync --locked --extra cloud --extra office --extra test
cd deploy/office-node && npm ci --omit=dev --ignore-scripts && cd ../..

export QWENPAW_OFFICE_NODE_PATH="$PWD/deploy/office-node/node_modules"
UV_CACHE_DIR=/tmp/uv-cache uv run --extra office \
  python -m qwenpaw_cloud.office_runtime --require-ready
```

配置 `OFFICE_MODEL_PROVIDER`、`OFFICE_MODEL_ID`、
`OFFICE_MODEL_BASE_URL` 以及可选的 `OFFICE_MODEL_API_KEY` 后启动测试页面：

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --extra office \
  python test-tools/office-agent-console/server.py
```

浏览器访问 <http://127.0.0.1:8099>。
