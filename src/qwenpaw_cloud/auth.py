"""Independent test service identities and short-lived scoped capabilities.

Only for the loopback macos-dev profile. P2 supplies production BFF identity.
"""
import base64
import hashlib
import hmac
import json
import time

from fastapi import HTTPException, Request

from .contracts import canonical


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Auth:
    def __init__(self, key: str):
        if len(key) < 32:
            raise ValueError(
                "test signing key must contain at least 32 characters"
            )
        self.key = key.encode()

    def issue(
        self, role: str, subject: str, audience: str, *, ttl=3600, **claims
    ) -> str:
        payload = _encode(
            canonical(
                {
                    **claims,
                    "role": role,
                    "sub": subject,
                    "aud": audience,
                    "iss": "p0-test",
                    "exp": int(time.time()) + ttl,
                }
            )
        )
        signature = _encode(
            hmac.new(self.key, payload.encode(), hashlib.sha256).digest()
        )
        return payload + "." + signature

    def verify(
        self, token: str, audience: str, roles: tuple[str, ...]
    ) -> dict:
        try:
            payload, signature = token.split(".")
            expected = _encode(
                hmac.new(self.key, payload.encode(), hashlib.sha256).digest()
            )
            if not hmac.compare_digest(signature, expected):
                raise ValueError("signature")
            claims = json.loads(
                base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
            )
            if (
                claims["iss"] != "p0-test"
                or claims["aud"] != audience
                or claims["role"] not in roles
                or claims["exp"] <= time.time()
                or not claims["sub"]
            ):
                raise ValueError("claims")
            return claims
        except (ValueError, KeyError, TypeError):
            raise HTTPException(401, "INVALID_SERVICE_IDENTITY") from None

    def request(self, request: Request, audience: str, *roles: str) -> dict:
        value = request.headers.get("authorization", "")
        if not value.startswith("Bearer "):
            raise HTTPException(401, "SERVICE_IDENTITY_REQUIRED")
        return self.verify(value[7:], audience, roles)
