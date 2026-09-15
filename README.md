# QwenPaw Slim Office Agent

This branch contains the focused multi-user office Agent backend extracted
from QwenPaw. It supports multi-turn document work, immutable file references,
built-in Skills, Attempt-scoped execution, and structural Office validation.

## Repository layout

```text
src/qwenpaw_cloud/       Core, File API, Worker, Runner and Office Agent bridge
src/qwenpaw/             Compatibility Agent runtime used by OfficeAgentBridge
src/qwenpaw_test_bff/    Development-only File API facade
cloud/                   Contracts, migrations, Kubernetes and backend tests
deploy/                  Office Worker image and locked Node dependencies
test-tools/
  office-agent-console/  Local upload/chat/artifact validation UI
```

`src/qwenpaw/` remains temporarily because the office bridge reuses the native
`QwenPawAgent`, TL/OpenAI providers, schema types and Skill scanner. New product
features must be implemented under `qwenpaw_cloud`; do not restore the legacy
Console, plugins, channels, website, MCP or standalone `qwenpaw.office` API.

## Runtime inventory

Install the focused environment:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv sync --locked --extra cloud --extra office --extra test
cd deploy/office-node && npm ci --omit=dev --ignore-scripts && cd ../..
```

Verify the two providers and six built-in Skills:

```bash
QWENPAW_OFFICE_NODE_PATH="$PWD/deploy/office-node/node_modules" \
UV_CACHE_DIR=/tmp/uv-cache uv run --extra office \
python -m qwenpaw_cloud.office_runtime --require-ready
```

## Local Agent validation UI

Configure `OFFICE_MODEL_PROVIDER`, `OFFICE_MODEL_ID`,
`OFFICE_MODEL_BASE_URL` and, for OpenAI, `OFFICE_MODEL_API_KEY`. Then run:

```bash
QWENPAW_OFFICE_NODE_PATH="$PWD/deploy/office-node/node_modules" \
UV_CACHE_DIR=/tmp/uv-cache uv run --extra office \
python test-tools/office-agent-console/server.py
```

Open <http://127.0.0.1:8099>, upload a DOCX/XLSX/PPTX, and continue in the
same session to validate multi-turn editing.

## Tests

```bash
QWENPAW_OFFICE_NODE_PATH="$PWD/deploy/office-node/node_modules" \
UV_CACHE_DIR=/tmp/uv-cache uv run --extra test --extra office \
pytest -q cloud/tests
```

The production Worker image is defined by `deploy/Dockerfile.office-worker`.
The local test console is not a production BFF and binds only to loopback.
