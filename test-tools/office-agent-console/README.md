# QwenPaw Office Agent Console

Local-only native web console for testing the real `OfficeAgentBridge` without
PostgreSQL, BFF, Core API, or object storage. Sessions disappear when the
process exits and must never be exposed outside loopback.

## Run

```bash
export OFFICE_MODEL_PROVIDER=openai
export OFFICE_MODEL_ID='your-model'
export OFFICE_MODEL_BASE_URL='https://your-endpoint/v1'
export OFFICE_MODEL_API_KEY='your-secret'
export QWENPAW_OFFICE_NODE_PATH="$PWD/deploy/office-node/node_modules"

UV_CACHE_DIR=/tmp/uv-cache uv run --extra office \
  python test-tools/office-agent-console/server.py
```

Open <http://127.0.0.1:8099>. Upload `.docx`, `.xlsx`, or `.pptx`, then ask the
Agent to create a new file under `output/`. Continue in the same page to test
multi-turn editing.

On macOS, keep the default `OFFICE_SHELL_MODE=sandboxed`. Use
`trusted_container` only inside the non-root production-like container.

## Limits

- Local test utility, not an authenticated production service.
- Maximum 10 files, 50 MiB each.
- Structural verification does not prove Office/WPS visual rendering.
- Attempts are stored under `/tmp/qwenpaw-office-console` by default.
