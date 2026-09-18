#!/usr/bin/env python3
"""Loopback developer BFF. No token is exposed to browser clients."""

from __future__ import annotations

import http.client
import http.server
import ipaddress
import json
import mimetypes
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAX_BODY = 20 * 1024 * 1024 + 65536  # file upload plus multipart framing
READ_TIMEOUT = 60
ROUTES = re.compile(
    r"/(?:health|ready|v1/(?:runs(?:/[a-zA-Z0-9-]+(?:/(?:events|cancel))?)?|sessions(?:/[a-zA-Z0-9-]+/(?:messages|files))?|files(?:/[a-zA-Z0-9-]+)?|approvals/[a-zA-Z0-9-]+/decision|memory))"
)


def validate_target(target):
    parsed = urllib.parse.urlsplit(target)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "API URL must be an HTTP(S) origin without credentials or path"
        )
    _ = parsed.port  # validates malformed ports
    return target.rstrip("/")


def local_origin(origin, port):
    try:
        parsed = urllib.parse.urlsplit(origin)
        return (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            and (parsed.port or 80) == port
            and not parsed.username
            and not parsed.password
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


def local_host(value, port):
    return local_origin("http://" + value, port)


def check_request(path, headers, server_port):
    parsed = urllib.parse.urlsplit(path)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or not ROUTES.fullmatch(parsed.path)
    ):
        return "upstream path is not allowed"
    if not local_host(headers.get("Host", ""), server_port):
        return "untrusted Host"
    origin = headers.get("Origin")
    if origin and not local_origin(origin, server_port):
        return "untrusted Origin"
    if headers.get("Sec-Fetch-Site") == "cross-site":
        return "cross-site request rejected"
    return None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class Handler(http.server.BaseHTTPRequestHandler):
    # Closing each downstream response gives SSE/JSON an unambiguous EOF.
    protocol_version = "HTTP/1.0"

    def setup(self):
        super().setup()
        self.connection.settimeout(READ_TIMEOUT)

    def _json(self, status, payload):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _proxy(self):
        error = check_request(self.path, self.headers, self.server.server_port)
        if error:
            self._json(403, {"error": error})
            return
        user = self.headers.get("X-QwenPaw-User", "")
        if (
            not user
            or len(user) > 256
            or any(ord(c) < 32 or ord(c) == 127 for c in user)
        ):
            self._json(400, {"error": "X-QwenPaw-User is required"})
            return
        try:
            if (
                self.headers.get("Transfer-Encoding")
                or len(self.headers.get_all("Content-Length", [])) > 1
            ):
                raise ValueError
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0:
                raise ValueError
        except ValueError:
            self._json(400, {"error": "invalid request framing"})
            return
        if length > MAX_BODY:
            self._json(413, {"error": "request body too large"})
            return
        sent = False
        try:
            body = self.rfile.read(length) if length else b""
            if len(body) != length:
                self._json(400, {"error": "incomplete body"})
                return
            headers = {
                "Authorization": "Bearer " + self.server.service_token,
                "X-QwenPaw-User": user,
            }
            for name in ("Content-Type", "Accept", "Last-Event-ID"):
                if self.headers.get(name):
                    headers[name] = self.headers[name]
            request = urllib.request.Request(
                self.server.target + self.path,
                data=body or None,
                method=self.command,
                headers=headers,
            )
            # Neither follow redirects (which could forward credentials) nor use
            # process proxy variables to reroute authenticated developer traffic.
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), NoRedirect()
            )
            try:
                response = opener.open(request, timeout=READ_TIMEOUT)
            except urllib.error.HTTPError as exc:
                if 300 <= exc.code < 400:
                    exc.close()
                    self._json(502, {"error": "upstream redirects are forbidden"})
                    return
                response = exc
            with response:
                self.send_response(response.status)
                self.send_header(
                    "Content-Type",
                    response.headers.get("Content-Type", "application/octet-stream"),
                )
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("Connection", "close")
                self.end_headers()
                sent = True
                while chunk := response.read1(65536):
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except (OSError, urllib.error.URLError, http.client.HTTPException):
            if not sent:
                self._json(502, {"error": "upstream unavailable"})
        finally:
            self.close_connection = True

    def do_GET(self):
        if self.path in {"/", "/app.js", "/style.css", "/sse.js"}:
            if not local_host(self.headers.get("Host", ""), self.server.server_port):
                self._json(403, {"error": "untrusted Host"})
                return
            name = "index.html" if self.path == "/" else self.path[1:]
            raw = (ROOT / "web" / name).read_bytes()
            self.send_response(200)
            self.send_header(
                "Content-Type", mimetypes.guess_type(name)[0] or "text/plain"
            )
            self.send_header("Content-Length", str(len(raw)))
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'",
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)
        else:
            self._proxy()

    def do_POST(self):
        self._proxy()

    def do_DELETE(self):
        self._proxy()

    def log_message(self, *_args):
        pass


def create_server(host="127.0.0.1", port=8100, target=None, token=None):
    if not ipaddress.ip_address(host).is_loopback or ":" in host:
        raise ValueError("Bind to an IPv4 loopback address only")
    token = (
        token if token is not None else os.environ.get("QWENPAW_TEST_SERVICE_TOKEN", "")
    )
    if len(token) < 32 or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError(
            "QWENPAW_TEST_SERVICE_TOKEN requires at least 32 printable characters"
        )
    target = validate_target(
        target or os.environ.get("QWENPAW_TEST_API_URL", "http://127.0.0.1:8090")
    )
    server = http.server.ThreadingHTTPServer((host, port), Handler)
    server.service_token, server.target = token, target
    return server


def main():
    server = create_server(
        port=int(os.environ.get("QWENPAW_TEST_CONSOLE_PORT", "8100"))
    )
    print(f"Server console: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
