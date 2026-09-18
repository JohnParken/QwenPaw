# Current multi-user API test console

A loopback-only developer UI for `/v1/runs`, durable SSE replay, cancellation,
approvals, sessions/history and files. The two migrated office-console launch
paths are aliases of this implementation and no longer need an office backend.

Start API/Worker using the platform configuration, then configure:

```bash
export QWENPAW_TEST_API_URL=http://127.0.0.1:8090
# Set QWENPAW_TEST_SERVICE_TOKEN to the API BFF token (at least 32 characters).
python3 test-tools/server-console/server.py
```

Open `http://127.0.0.1:8100`. Override the port with
`QWENPAW_TEST_CONSOLE_PORT`. The token remains in the Python proxy; it is never
rendered in browser assets. The selected user supplies X-QwenPaw-User for local
isolation tests. This is deliberately not end-user authentication: never deploy
this console as a production BFF.

Use an external session name when submitting a task. The returned internal
session UUID is populated separately for explicit file import. Uploads return
file IDs; include them as attachments or import them when a session is idle.
There is no GET /v1/files collection endpoint: use known/uploaded IDs and Get
file for a presigned download URL. Uploaded IDs are kept per user in page memory.

SSE uses Last-Event-ID, ignores acknowledged event IDs, limits retained display
text, and retries interrupted streams up to five times. Reconnect retries
manually. Switching users or submitting a new run disconnects the old stream;
this does not cancel the old server task. Approve/Deny and Cancel are explicit
operations. A browser disconnect never silently resubmits a task.

The proxy binds IPv4 loopback, validates Host/Origin, rejects redirects and
arbitrary proxy paths, and applies request/time limits. These are developer
protections, not a replacement for platform authentication or authorization.

```bash
python3 -m unittest discover -s test-tools/server-console/tests -p 'test_*.py'
node --test test-tools/server-console/tests/sse.test.mjs
```
