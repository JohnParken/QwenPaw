# QwenPaw Office API Test Console

零前端依赖的本地测试控制台，用来验证独立 `qwenpaw-office` 服务的核心能力。
页面使用原生 HTML、CSS 和 JavaScript；随附的 Python 标准库服务器负责静态文件和同源反向代理，因此不需要修改 Office API 的 CORS 配置。

## 启动

先启动 Office API：

```bash
export QWENPAW_OFFICE_OPENAI_API_KEY='your-key'
.venv/bin/qwenpaw-office
```

默认使用内存存储，适合单机测试。然后启动测试控制台：

```bash
python3 test-tools/qwenpaw-office-api-console/server.py
```

打开 <http://127.0.0.1:8099>。默认代理到 <http://127.0.0.1:8090>。

如需修改地址：

```bash
QWENPAW_OFFICE_TEST_API_URL=http://127.0.0.1:8090 \
QWENPAW_OFFICE_TEST_PORT=8099 \
python3 test-tools/qwenpaw-office-api-console/server.py
```

## 可测试能力

- `/health/live`、`/health/ready` 和六个 Skill readiness。
- 可信 BFF 身份头，无 JWT。
- 创建、查询和关闭 Session。
- 上传并选择 Office、PDF、CSV、JSON、Parquet 等输入文件。
- JSON 或 SSE 多轮消息。
- Tool 和 Artifact SSE 事件。
- 主动取消正在执行的 Turn。
- 查看和下载文件、Artifact。

## 限制

- 仅绑定 loopback，不能作为生产 BFF 或生产前端使用。
- Tenant/User 输入只用于模拟可信 BFF，不代表真实身份认证。
- 浏览器只访问本地测试服务器；API 目标由启动环境变量控制，避免在页面中开放任意代理地址。
- 使用内存存储时，Office API 重启后 Session、文件和 Artifact 会消失。

## 自检

```bash
python3 -m unittest discover \
  -s test-tools/qwenpaw-office-api-console/tests \
  -p 'test_*.py'

node --check test-tools/qwenpaw-office-api-console/web/app.js
```
