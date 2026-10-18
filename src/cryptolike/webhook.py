"""Webhook signature verification (https://cryptolike.net/docs/webhooks §3)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Union

from .errors import WebhookVerificationError
from .types import Model, StaticAddressArchivedData, StaticAddressArchiveWarningData, StaticAddressRestoredData

Bytes = Union[bytes, bytearray, memoryview]


STATIC_ADDRESS_ARCHIVED = "static_address.archived"
STATIC_ADDRESS_RESTORED = "static_address.restored"
STATIC_ADDRESS_ARCHIVE_WARNING = "static_address.archive_warning"

# Event type → typed ``data`` (``WebhookEvent.typed_data()``).
EVENT_DATA_TYPES: dict[str, type[Model]] = {
    STATIC_ADDRESS_ARCHIVED: StaticAddressArchivedData,
    STATIC_ADDRESS_RESTORED: StaticAddressRestoredData,
    STATIC_ADDRESS_ARCHIVE_WARNING: StaticAddressArchiveWarningData,
}


@dataclass
class WebhookEvent:
    """A verified delivery. ``data`` is the full state of the object at ``created_at``."""

    event_id: str
    type: str
    created_at: str
    data: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def typed_data(self) -> Optional[Model]:
        """``data`` as a typed object for the types in ``EVENT_DATA_TYPES``
        (``static_address.*`` → ``StaticAddress*Data``); ``None`` for other types."""
        cls = EVENT_DATA_TYPES.get(self.type)
        return cls.from_dict(self.data) if cls is not None else None


def webhook_signature(secret: str, timestamp: Union[int, str], raw_body: Union[str, Bytes]) -> str:
    """The ``X-Signature`` the platform computes: ``sha256=hex(HMAC-SHA256(secret, timestamp + "." + body))``."""
    body = raw_body.encode("utf-8") if isinstance(raw_body, str) else bytes(raw_body)
    mac = hmac.new(secret.encode("utf-8"), f"{timestamp}.".encode("utf-8") + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def _header(headers: Mapping[str, Any], name: str) -> Optional[str]:
    for candidate in (name, name.lower(), name.upper()):
        v = headers.get(candidate)
        if v is not None:
            return v[0] if isinstance(v, (list, tuple)) else str(v)
    # Frameworks that expose a case-insensitive mapping via .get already matched above; fall back to a scan.
    lname = name.lower()
    for k, v in headers.items():
        if str(k).lower() == lname:
            return v[0] if isinstance(v, (list, tuple)) else str(v)
    return None


def verify_webhook(
    headers: Mapping[str, Any],
    raw_body: Union[str, Bytes],
    secret: str,
    *,
    tolerance: Optional[int] = 300,
    now: Optional[Callable[[], float]] = None,
) -> WebhookEvent:
    """Verify a delivery and return the parsed event, or raise ``WebhookVerificationError``.

    ``raw_body`` must be the bytes exactly as received (Flask: ``request.get_data()``; Django:
    ``request.body``) — never a re-serialised JSON object. ``tolerance`` is the maximum
    ``|now - X-Timestamp|`` in seconds; ``None`` disables the check (offline tests only).
    """
    if not isinstance(secret, str) or not secret:
        raise WebhookVerificationError("invalid_signature", "webhook secret is empty")
    ts = _header(headers, "X-Timestamp")
    sig = _header(headers, "X-Signature")
    if not ts:
        raise WebhookVerificationError("missing_header", "X-Timestamp header is missing")
    if not sig:
        raise WebhookVerificationError("missing_header", "X-Signature header is missing")
    # isdigit() accepts "²"/"¹", which int() below would raise on; isascii() keeps the refusal
    # identical to the other SDKs (/^[0-9]+$/, ParseInt, ctype_digit), which reject "٢" as well.
    if not (ts.isdecimal() and ts.isascii()):
        raise WebhookVerificationError("invalid_timestamp", "X-Timestamp is not Unix seconds")
    if tolerance is not None:
        current = (now or time.time)()
        if abs(current - int(ts)) > tolerance:
            raise WebhookVerificationError("timestamp_out_of_tolerance", f"X-Timestamp is outside the {tolerance} s tolerance")

    expected = webhook_signature(secret, ts, raw_body)
    # Exact bytes: "sha256=" prefix and lower-case hex, as the platform sends them.
    if not hmac.compare_digest(expected.encode("ascii"), sig.encode("utf-8", "surrogateescape")):
        raise WebhookVerificationError("invalid_signature", "X-Signature does not match")

    body = raw_body.encode("utf-8") if isinstance(raw_body, str) else bytes(raw_body)
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise WebhookVerificationError("invalid_body", "body is not JSON") from e
    if not isinstance(parsed, dict) or not isinstance(parsed.get("event_id"), str) or not isinstance(parsed.get("type"), str):
        raise WebhookVerificationError("invalid_body", "body is not a webhook event")
    return WebhookEvent(
        event_id=parsed["event_id"],
        type=parsed["type"],
        created_at=str(parsed.get("created_at", "")),
        data=parsed.get("data") if isinstance(parsed.get("data"), dict) else {},
        raw=parsed,
    )
