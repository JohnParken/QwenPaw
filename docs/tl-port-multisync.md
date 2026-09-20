# TL migration to multisync

Historical migration record. For the current service baseline and startup instructions,
see [Server v1](multi-user-server.md) and [TL setup](multi-user-tl.md).

Source: local `office` commit `0465276cc0df7dde98fe3d56af3b20ca2120eb62`.
The protocol specifications are preserved under `docs/skills/tl-llm-proxy` and
`docs/skills/tl-llm-standalone`. Their design-era status wording is historical;
this document records the implemented migration.

## Included

- Independent Node TL proxy, lockfile, build/package commands and offline tests.
- Python TL transport, strict prompt/tool codec, formatter, preview, provider
  registration/persistence, retry/fallback rules, startup defaults and logging.
- Model settings UI, TL fields, local Console preview events and cleanup.
- Native console and test launch scripts from office.
- Both office-console entry points now launch the shared current `/v1` test
  console; they no longer import office/cloud runtimes or use Session/Turn APIs.
- Existing multisync runtime injection, session isolation and TDSQL storage
  remain in place. No office runtime or desktop-removal changes were imported.

## Multi-user Worker configuration

Set the platform assistant definition, for example:

```json
{
  "version": "tl-platform-v1",
  "name": "assistant",
  "system_prompt": "Help the user. Use approved tools when needed.",
  "model_protocol": "tl",
  "model": "internal-route-label",
  "base_url": "http://127.0.0.1:8089",
  "context_size": 32768,
  "tl_config": {
    "app_id": "",
    "tr_code": "",
    "tr_version": "",
    "tool_calling_mode": "system_prompt"
  },
  "auto_memory": true,
  "tools": []
}
```

In containers, replace loopback with the reachable proxy/gateway address.
`QWENPAW_SERVER_MODEL_API_KEY` is the TL gateway credential, not the proxy's
external model key. Model routing is configured on the proxy/gateway; the
local `model` label is not sent in TL wire requests. Every model decision and
syntax correction creates a fresh TL session. Mutable state remains per run.
The default protocol remains `openai` for existing assistant definitions.
Increment the definition version when changing configuration; stored snapshots
are immutable, including newly added configuration fields.

Auto-memory extraction uses TL structured text output through the same codec;
it does not call an OpenAI endpoint or send response_format. TL has no
embedding endpoint, so TL definitions reject embedding_model and use scoped
lexical memory retrieval. Ordinary registered TL providers still support their
own custom headers through the existing provider configuration API.

At the time of the initial port, preview events were limited to the local Console.
Subsequent `/v1` streaming changes are documented in
[streaming-tool-console.md](streaming-tool-console.md). Preview text never
becomes an executable tool call.

## Test tools

Proxy (Node 22.22.1 or supported 24+ as declared in its package):

```bash
cd test-tools/tl-llm-proxy
npm ci
npm test
# Configure the proxy's .env from .env.example, then npm start.
```

Native console retains the original `/api` local QwenPaw app integration; see
`test-tools/native-console/README.md` for its separate launch commands.

The current multi-user console requires an already running API/Worker:

```bash
export QWENPAW_TEST_API_URL=http://127.0.0.1:8090
# Set QWENPAW_TEST_SERVICE_TOKEN to the API's BFF token via your secret environment.
python3 test-tools/server-console/server.py
```

Open `http://127.0.0.1:8100`. Either old office-console `server.py` path launches
this same implementation. See `test-tools/server-console/README.md`. No new
production BFF or end-user authentication service is introduced.

## Validation

- Python provider/server/preview/routing/roundtrip suite: 852 passed, 18 skipped.
- Real HTTP offline roundtrip: Worker -> Node proxy -> synthetic model ->
  persisted approval -> one remote tool invocation -> final response -> scoped
  memory extraction. Native tool fields are asserted absent on the upstream.
- Proxy: 28 tests passed. Native console: 32 tests and build passed.
- Main Console: 54 focused tests and TypeScript build check passed.
- Browser smoke (Playwright, synthetic local API): submit, streamed approval,
  approval decision, terminal completion and history refresh passed.
- Shared test-console proxy: four tests covering credential injection,
  Host/Origin/path/redirect rejection, invalid request framing and immediate SSE
  delivery. Both compatibility entry points tested. SSE parser: two tests,
  including every split boundary in a CRLF multiline frame.

The current local Node is 23.11.0, which runs these checks but is outside the
packages' declared supported production versions. No real model key, company
endpoint, TDSQL instance or Kubernetes cluster was used. Existing optional
integration skips are not treated as passes. Native-console format:check has
an inherited formatting issue in scripts/manage-live.mjs; that source was
preserved without unrelated reformatting.
