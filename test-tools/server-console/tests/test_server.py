import http.client
import importlib.util
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

spec = importlib.util.spec_from_file_location(
    "test_console", Path(__file__).parents[1] / "server.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
TOKEN = "synthetic-service-token-" + "x" * 32


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path.endswith("/events"):
            self.server.last_event_id = self.headers.get("Last-Event-ID")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b'id: 2\ndata: {"text":"early"}\n\n')
            self.wfile.flush()
            self.server.release.wait(3)
            self.server.finished.set()
            self.wfile.write(b"event: end\ndata: {}\n\n")
        elif self.path == "/ready":
            self.send_response(302)
            self.send_header("Location", "/credentials-must-not-follow")
            self.end_headers()
        else:
            self.do_POST()

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.seen.append((self.path, dict(self.headers), body))
        payload = b'{"id":"test"}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        self.upstream.release = threading.Event()
        self.upstream.finished = threading.Event()
        self.upstream.seen = []
        self.thread = threading.Thread(target=self.upstream.serve_forever, daemon=True)
        self.thread.start()
        self.proxy = module.create_server(
            port=0, target=f"http://127.0.0.1:{self.upstream.server_port}", token=TOKEN
        )
        self.proxy_thread = threading.Thread(
            target=self.proxy.serve_forever, daemon=True
        )
        self.proxy_thread.start()
        self.url = f"http://127.0.0.1:{self.proxy.server_port}"

    def tearDown(self):
        self.upstream.release.set()
        self.proxy.shutdown()
        self.proxy.server_close()
        self.upstream.shutdown()
        self.upstream.server_close()
        self.thread.join()
        self.proxy_thread.join()

    def request(self, path, headers=None, body=None):
        return urlopen(
            Request(
                self.url + path,
                data=body,
                headers={"X-QwenPaw-User": "alice", **(headers or {})},
            ),
            timeout=5,
        )

    def test_token_injection_and_no_browser_secret(self):
        with self.request(
            "/v1/runs", {"Authorization": "Bearer browser-value"}, b"{}"
        ) as response:
            self.assertEqual(response.status, 200)
        _, headers, body = self.upstream.seen[0]
        self.assertEqual(headers["Authorization"], "Bearer " + TOKEN)
        self.assertEqual(headers["X-Qwenpaw-User"], "alice")
        self.assertEqual(body, b"{}")
        with urlopen(self.url) as response:
            self.assertNotIn(TOKEN.encode(), response.read())
            self.assertIn(
                "default-src 'self'", response.headers["Content-Security-Policy"]
            )

    def test_origin_paths_binding_and_redirect_protection(self):
        for path, headers, status in [
            ("/v1/runs", {"Origin": "https://evil.example"}, 403),
            ("/v1/runs", {"Host": "evil.example"}, 403),
            ("/v1/../credentials", {}, 403),
            ("/v1/%2e%2e/credentials", {}, 403),
            ("/ready", {}, 502),
        ]:
            with self.assertRaises(HTTPError) as failure:
                self.request(path, headers)
            self.assertEqual(failure.exception.code, status)
        self.assertEqual(self.upstream.seen, [])
        with self.assertRaises(ValueError):
            module.create_server(host="0.0.0.0", token=TOKEN)
        with self.assertRaises(ValueError):
            module.validate_target("https://user:pass@example.com")

    def test_sse_flushed_before_upstream_finishes(self):
        with self.request("/v1/runs/test/events", {"Last-Event-ID": "1"}) as response:
            self.assertEqual(response.readline(), b"id: 2\n")
            self.assertEqual(self.upstream.last_event_id, "1")
            self.assertFalse(
                self.upstream.finished.is_set(),
                "Proxy buffered SSE until upstream completed",
            )
            self.upstream.release.set()
            self.assertIn(b"event: end", response.read())

    def test_invalid_body_length(self):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.proxy.server_port, timeout=3
        )
        try:
            connection.request(
                "POST",
                "/v1/runs",
                headers={"Content-Length": "-1", "X-QwenPaw-User": "alice"},
            )
            self.assertEqual(connection.getresponse().status, 400)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
