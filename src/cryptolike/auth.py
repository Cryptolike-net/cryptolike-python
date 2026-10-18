"""Request signing (https://cryptolike.net/docs/auth §3). Runs on your server only — the secret never leaves it."""

from __future__ import annotations

import hashlib
import hmac
from typing import Union

Bytes = Union[bytes, bytearray, memoryview]

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def _to_bytes(body: Union[str, Bytes]) -> bytes:
    return body.encode("utf-8") if isinstance(body, str) else bytes(body)


def body_sha256(body: Union[str, Bytes] = b"") -> str:
    """Lower-case hex SHA-256 of the raw body bytes (a string is taken as UTF-8)."""
    data = _to_bytes(body)
    return EMPTY_SHA256 if not data else hashlib.sha256(data).hexdigest()


def canonical_string(timestamp: Union[int, str], method: str, path_query: str, body: Union[str, Bytes] = b"") -> str:
    """Four lines joined with LF, no trailing newline. ``path_query`` is the target exactly as sent."""
    return "\n".join([str(timestamp), method.upper(), path_query, body_sha256(body)])


def sign(secret: str, timestamp: Union[int, str], method: str, path_query: str, body: Union[str, Bytes] = b"") -> str:
    """``X-Signature``: hex HMAC-SHA256 of the canonical string, keyed with the secret as shown in the cabinet."""
    canonical = canonical_string(timestamp, method, path_query, body)
    return hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
