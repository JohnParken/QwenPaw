"""Worker HTTP clients, independent of Core, repository, and psycopg."""
from __future__ import annotations

import base64
import hashlib
import json
from uuid import uuid4

import httpx

from . import PROTOCOL_VERSION
from .contracts import FileRef, Limits


class APIClient:
    def __init__(
        self, base_url: str, token: str, limits: Limits | None = None
    ):
        self.limits = limits or Limits()
        self.base_url = base_url
        self.token = token

    def request(self, method, path, body=None):
        with httpx.Client(
            timeout=self.limits.request_timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = client.request(
                method,
                self.base_url + path,
                json=body,
                headers={
                    "Authorization": "Bearer " + self.token,
                    "X-Protocol-Version": PROTOCOL_VERSION,
                },
            )
            response.raise_for_status()
            return response.json()

    def post(self, path, body=None):
        return self.request("POST", path, body)

    def get(self, path):
        return self.request("GET", path)

    def claim(self, request_id):
        # Recovery queries the same request ID; never allocates a replacement.
        try:
            return self.post("/v1/claim", {"request_id": request_id})
        except httpx.TransportError:
            return self.get("/v1/claims/" + request_id)

    def download(self, ref):
        ref = FileRef.model_validate(ref)
        with httpx.Client(
            timeout=self.limits.request_timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            with client.stream(
                "GET",
                self.base_url + "/v1/files/" + ref.file_id,
                params={"version": ref.version},
                headers={
                    "Authorization": "Bearer " + self.token,
                    "X-Protocol-Version": PROTOCOL_VERSION,
                },
            ) as response:
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_bytes(8192):
                    data.extend(chunk)
                    if len(data) > self.limits.file_bytes:
                        raise ValueError("FILE_TOO_LARGE")
        if (
            len(data) != ref.size
            or hashlib.sha256(data).hexdigest() != ref.sha256
        ):
            raise ValueError("FILE_INTEGRITY_ERROR")
        return bytes(data)

    def upload(self, scope, data: bytes):
        if len(data) > self.limits.file_bytes:
            raise ValueError("FILE_TOO_LARGE")
        upload_id = uuid4().hex
        body = {
            "upload_id": upload_id,
            "scope": scope,
            "content_base64": base64.b64encode(data).decode(),
        }
        for attempt in range(self.limits.retries + 1):
            try:
                value = self.post("/v1/uploads", body)
                if value["status"] != "READY":
                    raise ValueError("FILE_NOT_READY")
                ref = FileRef.model_validate(value["ref"])
                if ref.sha256 != hashlib.sha256(
                    data
                ).hexdigest() or ref.size != len(data):
                    raise ValueError("UPLOAD_INTEGRITY_ERROR")
                return ref.model_dump()
            except httpx.TransportError:
                if attempt == self.limits.retries:
                    raise
