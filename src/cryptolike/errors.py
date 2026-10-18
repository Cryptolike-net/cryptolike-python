"""Exceptions raised by the SDK. None of them ever carries the API secret or a webhook secret."""

from __future__ import annotations

from typing import Any, Optional


class CryptoLikeError(Exception):
    """Base class of every SDK error."""


#: ``403`` code of a payout (create, cancel) with a key whose ``withdraw`` permission a developer
#: requested and an owner/admin has not approved yet: nothing was created or held, the
#: Idempotency-Key is still free. Not retried — retry once the request is approved in the cabinet.
WITHDRAW_PENDING_APPROVAL = "withdraw_pending_approval"


class ApiError(CryptoLikeError):
    """A non-2xx response the client could not recover from by retrying.

    Branch on ``code`` (stable machine code, https://cryptolike.net/docs/auth §5 / https://cryptolike.net/docs/api §5); ``message`` is
    for humans and may change.
    """

    def __init__(
        self,
        status: int,
        body: Optional[dict[str, Any]] = None,
        *,
        retry_after: Optional[float] = None,
        request_id: Optional[str] = None,
    ) -> None:
        err = (body or {}).get("error") if isinstance(body, dict) else None
        err = err if isinstance(err, dict) else {}
        self.status = status
        self.code: str = str(err.get("code") or "http_error")
        self.details: dict[str, Any] = err.get("details") if isinstance(err.get("details"), dict) else {}
        self.retry_after = retry_after
        self.request_id = request_id
        super().__init__(str(err.get("message") or f"HTTP {status}"))

    @property
    def message(self) -> str:
        return str(self.args[0])

    @property
    def withdraw_pending_approval(self) -> bool:
        """The payout was refused because the key's ``withdraw`` waits for an owner/admin's approval."""
        return self.code == WITHDRAW_PENDING_APPROVAL

    @property
    def field(self) -> Optional[str]:
        """``details.field`` of a ``validation_error``, when present."""
        f = self.details.get("field")
        return f if isinstance(f, str) else None

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"ApiError(status={self.status}, code={self.code!r}, message={self.message!r})"


class NetworkError(CryptoLikeError):
    """The request could not be completed at the transport level after all retries."""

    def __init__(self, message: str, cause: Optional[BaseException] = None) -> None:
        super().__init__(message)
        self.__cause__ = cause


class WebhookVerificationError(CryptoLikeError):
    """A webhook delivery must be rejected; ``reason`` says why."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
