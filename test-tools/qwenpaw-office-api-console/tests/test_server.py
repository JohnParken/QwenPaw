from __future__ import annotations

import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("office_console_server", ROOT / "server.py")
assert SPEC and SPEC.loader
SERVER_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVER_MODULE)


class UpstreamHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return None

    def do_GET(self):  # noqa: N802
        if self.path == "/api/v1/health/live":
            body = json.dumps({"live": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        response = json.dumps({
            "tenant": self.headers.get("X-Tenant-Id"),
            "request": self.headers.get("X-Request-Id"),
            "body": body.decode(),
        }).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


class ConsoleServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
        cls.upstream_thread = Thread(target=cls.upstream.serve_forever, daemon=True)
        cls.upstream_thread.start()
        upstream_url = f"http://127.0.0.1:{cls.upstream.server_port}"
        cls.console = SERVER_MODULE.create_server("127.0.0.1", 0, upstream_url)
        cls.console_thread = Thread(target=cls.console.serve_forever, daemon=True)
        cls.console_thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.console.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.console.shutdown()
        cls.console.server_close()
        cls.upstream.shutdown()
        cls.upstream.server_close()

    def test_serves_ui_and_config(self):
        with urlopen(self.base_url + "/") as response:
            self.assertIn(b"QwenPaw Office API", response.read())
            self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        with urlopen(self.base_url + "/config.json") as response:
            self.assertTrue(json.load(response)["apiUrl"].startswith("http://127.0.0.1:"))

    def test_proxies_identity_headers_and_body(self):
        request = Request(
            self.base_url + "/office-api/api/v1/sessions",
            data=b'{"metadata":{}}',
            headers={
                "Content-Type": "application/json",
                "X-Tenant-Id": "tenant-test",
                "X-Request-Id": "request-test",
            },
            method="POST",
        )
        with urlopen(request) as response:
            data = json.load(response)
        self.assertEqual(data["tenant"], "tenant-test")
        self.assertEqual(data["request"], "request-test")
        self.assertEqual(data["body"], '{"metadata":{}}')

    def test_rejects_non_loopback_binding_and_bad_proxy_path(self):
        with self.assertRaises(ValueError):
            SERVER_MODULE.create_server("0.0.0.0", 0, "http://127.0.0.1:1")
        try:
            urlopen(self.base_url + "/office-api", timeout=2)
        except HTTPError as exc:
            self.assertEqual(exc.code, 404)
        else:
            self.fail("expected HTTP 404")


if __name__ == "__main__":
    unittest.main()
