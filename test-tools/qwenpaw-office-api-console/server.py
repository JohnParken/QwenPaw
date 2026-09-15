#!/usr/bin/env python3
"""Loopback-only static server and Office API proxy with no dependencies."""

from __future__ import annotations

import argparse
import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

WEB_ROOT = Path(__file__).resolve().with_name("web")
PROXY_PREFIX = "/office-api"
MAX_PROXY_BODY = 64 * 1024 * 1024
FORWARDED_REQUEST_HEADERS = {
    "accept",
    "content-type",
    "x-tenant-id",
    "x-user-id",
    "x-request-id",
    "x-trace-id",
    "x-file-name",
    "x-session-id",
}
FORWARDED_RESPONSE_HEADERS = {
    "cache-control",
    "content-disposition",
    "content-type",
    "etag",
}


def validate_api_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Office API URL must be an absolute HTTP(S) URL")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError("Office API URL must not contain credentials, query, or fragment")
    return value.rstrip("/")


def make_handler(api_url: str) -> type[SimpleHTTPRequestHandler]:
    upstream = validate_api_url(api_url)

    class Handler(SimpleHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

        def log_message(self, message: str, *args: Any) -> None:
            print(f"[office-console] {self.address_string()} {message % args}")

        def end_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self'; script-src 'self'; "
                "connect-src 'self'; img-src 'self' blob:; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'",
            )
            super().end_headers()

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _proxy(self) -> None:
            suffix = self.path[len(PROXY_PREFIX):]
            if not suffix.startswith("/"):
                self._send_json(404, {"detail": "proxy path not found"})
                return
            length_header = self.headers.get("Content-Length")
            try:
                length = int(length_header) if length_header else 0
            except ValueError:
                self._send_json(400, {"detail": "invalid Content-Length"})
                return
            if length < 0 or length > MAX_PROXY_BODY:
                self._send_json(413, {"detail": "request body exceeds proxy limit"})
                return
            body = self.rfile.read(length) if length else None
            headers = {
                name: value
                for name, value in self.headers.items()
                if name.lower() in FORWARDED_REQUEST_HEADERS
            }
            request = Request(
                upstream + suffix,
                data=body,
                headers=headers,
                method=self.command,
            )
            response: Any = None
            try:
                response = urlopen(request, timeout=600)
            except HTTPError as exc:
                response = exc
            except (URLError, TimeoutError, OSError) as exc:
                self._send_json(502, {"detail": f"Office API unavailable: {exc}"})
                return

            try:
                self.send_response(response.status)
                for name, value in response.headers.items():
                    if name.lower() in FORWARDED_RESPONSE_HEADERS:
                        self.send_header(name, value)
                self.end_headers()
                is_sse = response.headers.get_content_type() == "text/event-stream"
                if is_sse:
                    while True:
                        line = response.readline()
                        if not line:
                            break
                        self.wfile.write(line)
                        self.wfile.flush()
                else:
                    while True:
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                response.close()
            finally:
                response.close()

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/config.json":
                self._send_json(200, {"apiUrl": upstream})
            elif self.path.startswith(PROXY_PREFIX + "/"):
                self._proxy()
            else:
                super().do_GET()

        def do_HEAD(self) -> None:  # noqa: N802
            if self.path.startswith(PROXY_PREFIX + "/"):
                self._proxy()
            else:
                super().do_HEAD()

        def do_POST(self) -> None:  # noqa: N802
            if self.path.startswith(PROXY_PREFIX + "/"):
                self._proxy()
            else:
                self.send_error(404)

        def do_DELETE(self) -> None:  # noqa: N802
            if self.path.startswith(PROXY_PREFIX + "/"):
                self._proxy()
            else:
                self.send_error(404)

    return Handler


def create_server(host: str, port: int, api_url: str) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("test console must bind to loopback")
    server = ThreadingHTTPServer((host, port), make_handler(api_url))
    server.daemon_threads = True
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("QWENPAW_OFFICE_TEST_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("QWENPAW_OFFICE_TEST_PORT", "8099")))
    parser.add_argument("--api-url", default=os.environ.get("QWENPAW_OFFICE_TEST_API_URL", "http://127.0.0.1:8090"))
    args = parser.parse_args()
    server = create_server(args.host, args.port, args.api_url)
    print(f"Office test console: http://{args.host}:{server.server_port}")
    print(f"Office API target:   {validate_api_url(args.api_url)}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
