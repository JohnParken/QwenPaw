# Multi-user API console compatibility entry

This entry now runs the shared [server-console](../server-console/README.md)
against the current `/v1` API. It no longer requires OfficeAgentBridge,
qwenpaw_cloud, the office extra, or the office-specific Session/Turn API.

Use `QWENPAW_TEST_API_URL`, `QWENPAW_TEST_SERVICE_TOKEN` (required, 32+ characters),
and optionally `QWENPAW_TEST_CONSOLE_PORT` (default 8100), then run this
directory's `server.py`. All three launch paths use the same UI and security checks.
