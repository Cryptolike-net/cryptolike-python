"""Server-side client of the CryptoLike merchant API.

The API secret signs requests *as you*; keep it in your environment or secret manager and use
this client from your backend only — never from code that ships to a browser or a mobile app.
"""

from __future__ import annotations

import json
import os
import random
import time
import uuid
from decimal import Decimal
from typing import Any, Callable, Iterator, List, Mapping, Optional, Sequence, TypeVar, Union
from urllib.parse import quote, urlencode

import requests

from ._version import USER_AGENT
from .auth import sign
from .errors import ApiError, NetworkError
from .types import (
    Balance,
    CoinOption,
    Currency,
    EmbedSettings,
    Invoice,
    Model,
    Page,
    Rate,
    StaticAddress,
    StaticAddressCard,
    Transaction,
    WebhookDelivery,
    WebhookEndpoint,
    WebhookEndpointCreated,
    Withdrawal,
    WithdrawalEstimate,
)

DEFAULT_BASE_URL = "https://api.cryptolike.net"
# Lower-case names a caller-supplied header may never replace: they are the signature itself.
_SIGNED_HEADERS = frozenset({"x-api-key", "x-timestamp", "x-signature"})

Amount = Union[str, int, Decimal]
M = TypeVar("M", bound=Model)


def _amount(value: Amount, name: str = "amount") -> str:
    """Decimal string for the wire. Floats are refused: they cannot represent money exactly."""
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError(f"{name}: use a str or Decimal, not {type(value).__name__} (floats are inexact)")
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    raise TypeError(f"{name}: unsupported type {type(value).__name__}")


def _path(value: str) -> str:
    """Percent-escapes an id inside a path segment (as encodeURIComponent / url.PathEscape do).

    Without it an id carrying ``?``, ``/`` or a space would change the request target the server
    sees while the signature still verifies, or be normalised by urllib after signing.
    """
    return quote(str(value), safe="")


def _clean(params: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in params.items():
        if v is None:
            continue
        if isinstance(v, Decimal):
            v = format(v, "f")
        elif isinstance(v, bool):
            v = "true" if v else "false"
        out[k] = v
    return out


class Client:
    """One instance per API key; safe to share between threads (``requests.Session`` is).

    >>> api = Client()  # CRYPTOLIKE_API_KEY_ID + CRYPTOLIKE_API_SECRET from the environment
    >>> inv = api.invoices.create(amount="10.50", currency="USD", order_id="A-1001")
    """

    def __init__(
        self,
        key_id: Optional[str] = None,
        secret: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        session: Optional[requests.Session] = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        retry_base: float = 0.5,
        retry_max: float = 8.0,
        retry_after_max: float = 30.0,
        api_version: str = "v1",
        app_info: Optional[str] = None,
        now: Optional[Callable[[], float]] = None,
        sleep: Optional[Callable[[float], None]] = None,
        idempotency_key: Optional[Callable[[], str]] = None,
    ) -> None:
        key_id = key_id or os.environ.get("CRYPTOLIKE_API_KEY_ID")
        secret = secret or os.environ.get("CRYPTOLIKE_API_SECRET")
        if not key_id:
            raise ValueError("cryptolike: key_id is required (argument or env CRYPTOLIKE_API_KEY_ID)")
        if not secret:
            raise ValueError("cryptolike: secret is required (argument or env CRYPTOLIKE_API_SECRET)")
        self._key_id = key_id
        self._secret = secret
        self.base_url = (base_url or os.environ.get("CRYPTOLIKE_API_URL") or DEFAULT_BASE_URL).rstrip("/")
        self._session = session or requests.Session()
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_base = retry_base
        self.retry_max = retry_max
        self.retry_after_max = retry_after_max
        self.api_version = api_version
        self.user_agent = f"{USER_AGENT} {app_info}" if app_info else USER_AGENT
        self._now = now or time.time
        self._sleep = sleep or time.sleep
        self._new_idempotency_key = idempotency_key or (lambda: str(uuid.uuid4()))

        self.invoices = Invoices(self)
        self.addresses = Addresses(self)
        self.transactions = Transactions(self)
        self.balances = Balances(self)
        self.currencies = Currencies(self)
        self.rates = Rates(self)
        self.withdrawals = Withdrawals(self)
        self.webhooks = Webhooks(self)
        self.embed = Embed(self)

    def __repr__(self) -> str:
        return f"Client(key_id={self._key_id!r}, base_url={self.base_url!r}, secret='[redacted]')"

    # ---- static-address archive (the same calls as ``addresses.*``, named after the feature) ---

    def archive_static_address(self, address_id: str, *, idempotency_key: Optional[str] = None) -> StaticAddress:
        """Archive a static address (``POST /v1/addresses/{id}/archive``): no longer offered, out of
        real-time scanning, never deleted or re-issued. Archived addresses are checked for incoming
        transfers regularly (every 24 h by default); a transfer found is credited as usual and
        restores the address automatically (webhook ``static_address.restored``, reason
        ``deposit``). Archiving an archived address is a no-op. Webhook ``static_address.archived``."""
        return self.addresses.archive(address_id, idempotency_key=idempotency_key)

    def restore_static_address(self, address_id: str, *, idempotency_key: Optional[str] = None) -> StaticAddress:
        """Restore an archived static address (``POST /v1/addresses/{id}/restore``); restoring an
        active one is a no-op. ``ApiError`` ``409 conflict`` with ``details["active_address_id"]``
        when the customer already has another active address for the currency — archive that one
        first."""
        return self.addresses.restore(address_id, idempotency_key=idempotency_key)

    def list_archived_static_addresses(self, **filters: Any) -> Page[StaticAddress]:
        """One page of archived static addresses (``status=archived``), newest first. Filters:
        search, coin_id, customer_id, limit, cursor."""
        return self.addresses.list(**{**filters, "status": "archived"})

    def iterate_archived_static_addresses(self, **filters: Any) -> Iterator[StaticAddress]:
        """Every archived static address matching the filters, page by page."""
        return self.addresses.iterate(**{**filters, "status": "archived"})

    # ---- transport ---------------------------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Mapping[str, Any]] = None,
        body: Optional[Mapping[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        headers: Optional[Mapping[str, str]] = None,
        raw: bool = False,
    ) -> Any:
        """Signed request with retries. Returns the parsed JSON (``raw=True``: the response text).

        Prefer the typed resources; use this for an endpoint added after this SDK version.
        """
        method = method.upper()
        qs = urlencode(_clean(query or {}))
        path_query = f"{path}?{qs}" if qs else path
        data = b"" if body is None else json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        idem = idempotency_key or (self._new_idempotency_key() if method == "POST" else None)

        attempt = 0
        last_ts: Optional[int] = None
        while True:
            ts = int(self._now())
            if ts == last_ts:
                # Timestamps are whole seconds, so a retry inside the same second would repeat the
                # signature byte for byte and the server accepts a signature once: a retry after a
                # genuine 5xx would come back as ``401 auth.replay``. Wait the second out first.
                self._sleep(_until_next_second(self._now()))
                ts = int(self._now())
            last_ts = ts
            h: dict[str, str] = {
                "Accept": "text/csv" if raw else "application/json",
                "User-Agent": self.user_agent,
                "X-API-Version": self.api_version,
            }
            # Caller headers first: they may refine Accept, never the signed ones.
            if headers:
                h.update({k: v for k, v in headers.items() if k.lower() not in _SIGNED_HEADERS})
            h["X-API-Key"] = self._key_id
            h["X-Timestamp"] = str(ts)
            h["X-Signature"] = sign(self._secret, ts, method, path_query, data)
            if data:
                h["Content-Type"] = "application/json"
            if idem:
                h["Idempotency-Key"] = idem
            try:
                res = self._session.request(method, self.base_url + path_query, headers=h, data=data or None, timeout=self.timeout, allow_redirects=False)
            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt >= self.max_retries:
                    raise NetworkError(f"{method} {path_query}: {e.__class__.__name__}", e) from e
                self._sleep(self._backoff(attempt))
                attempt += 1
                continue

            if 200 <= res.status_code < 300:
                if not res.content:
                    return None
                ctype = res.headers.get("Content-Type", "")
                return res.json() if (not raw and "application/json" in ctype) else res.text

            retry_after = _retry_after(res.headers.get("Retry-After"))
            err = ApiError(res.status_code, _json_or_none(res), retry_after=retry_after, request_id=res.headers.get("X-Request-Id"))
            if err.status == 401 and err.code == "auth.replay" and attempt < self.max_retries:
                # Timestamps are whole seconds: an identical request within the same second carries
                # the same signature and the server accepts a signature once. Wait for the next
                # second and sign afresh (POSTs keep their Idempotency-Key: nothing is created twice).
                self._sleep(_until_next_second(self._now()))
                attempt += 1
                continue
            if not _retryable(res.status_code) or attempt >= self.max_retries:
                raise err
            delay = retry_after if retry_after is not None else self._backoff(attempt)
            if delay > self.retry_after_max:
                raise err
            self._sleep(delay)
            attempt += 1

    def _backoff(self, attempt: int) -> float:
        cap = min(self.retry_max, self.retry_base * (2**attempt))
        return random.uniform(0, cap)

    def _paginate(self, item_type: type[M], path: str, query: dict[str, Any]) -> Iterator[M]:
        cursor: Optional[str] = None
        while True:
            page = Page.of(item_type, self.request("GET", path, query={**query, "cursor": cursor}))
            yield from page.items
            if not page.next_cursor:
                return
            cursor = page.next_cursor


def _until_next_second(now: float) -> float:
    """Seconds until the clock crosses into the next whole second (+10 ms of margin)."""
    return 1.0 - (now % 1.0) + 0.01


def _retryable(status: int) -> bool:
    return status == 429 or (status >= 500 and status != 501)


def _retry_after(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        n = float(value)
    except ValueError:
        return None
    return n if n >= 0 else None


def _json_or_none(res: requests.Response) -> Optional[dict[str, Any]]:
    if "application/json" not in res.headers.get("Content-Type", ""):
        return None
    try:
        out = res.json()
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


# ---- resources -------------------------------------------------------------------------------


class _Resource:
    def __init__(self, client: Client) -> None:
        self._c = client


class Currencies(_Resource):
    def list(self) -> list[Currency]:
        """Coins this key's merchant can use (``read``)."""
        return [Currency.from_dict(d) for d in self._c.request("GET", "/v1/currencies").get("currencies", [])]


class Rates(_Resource):
    def list(self, fiat: Optional[str] = None) -> list[Rate]:
        """Latest quote of every enabled coin against ``fiat`` (default: the merchant's default fiat)."""
        return [Rate.from_dict(d) for d in self._c.request("GET", "/v1/rates", query={"fiat": fiat}).get("rates", [])]


class Balances(_Resource):
    def list(self) -> list[Balance]:
        """Available / pending / on-hold balance per coin."""
        return [Balance.from_dict(d) for d in self._c.request("GET", "/v1/balances").get("balances", [])]


class Invoices(_Resource):
    def create(
        self,
        amount: Amount,
        currency: str,
        *,
        coin_id: Optional[str] = None,
        description: Optional[str] = None,
        order_id: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
        return_url: Optional[str] = None,
        webhook_url: Optional[str] = None,
        fee_paid_by: Optional[str] = None,
        metadata: Optional[dict[str, str]] = None,
        idempotency_by_order_id: Optional[bool] = None,
        allowed_coin_ids: Optional[Sequence[str]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Invoice:
        """Create a payment; send the payer to ``checkout_url``. ``idempotency_key`` defaults to a random UUID.

        ``metadata``: up to 20 string pairs, returned in the API and webhooks, never shown to the payer.
        ``idempotency_by_order_id=True`` (needs ``order_id``): the open invoice of the same order is
        returned instead of a new one; a different amount or an order that already received funds is
        ``409 conflict`` (``details.reason``: ``order_invoice_mismatch`` / ``order_has_payment``).
        ``allowed_coin_ids`` (``Currency.id``, 1..100, not with ``coin_id``): the coins the payer may
        choose from on the payment page; ``None`` — every coin you offer for invoices. The server
        keeps the ones offered for invoices that match ``currency``; none left is
        ``422 no_allowed_coins``. An empty list raises ``ValueError`` before the request (a filter
        that found nothing never turns into "every coin").
        """
        if allowed_coin_ids is not None:
            if isinstance(allowed_coin_ids, str):
                raise TypeError("allowed_coin_ids must be a list of coin ids, not a string")
            allowed_coin_ids = list(allowed_coin_ids)
            if not allowed_coin_ids:
                raise ValueError("allowed_coin_ids is empty — pass None to offer every coin you offer for invoices")
        body = _clean(
            {
                "amount": _amount(amount),
                "currency": currency,
                "coin_id": coin_id,
                "description": description,
                "order_id": order_id,
                "ttl_seconds": ttl_seconds,
                "return_url": return_url,
                "webhook_url": webhook_url,
                "fee_paid_by": fee_paid_by,
                "metadata": metadata,
                "idempotency_by_order_id": idempotency_by_order_id,
                "allowed_coin_ids": allowed_coin_ids,
            }
        )
        return Invoice.from_dict(self._c.request("POST", "/v1/invoices", body=body, idempotency_key=idempotency_key))

    def get(self, invoice_id: str) -> Invoice:
        """The invoice with its status timeline (``events``)."""
        return Invoice.from_dict(self._c.request("GET", f"/v1/invoices/{_path(invoice_id)}"))

    def list(self, **filters: Any) -> Page[Invoice]:
        """One page, newest first. Filters: status, coin_id, from_, to, min_amount, max_amount, order_id, search, limit, cursor."""
        return Page.of(Invoice, self._c.request("GET", "/v1/invoices", query=_filters(filters)))

    def iterate(self, **filters: Any) -> Iterator[Invoice]:
        """Every invoice matching the filters, page by page."""
        return self._c._paginate(Invoice, "/v1/invoices", _filters(filters))

    def cancel(self, invoice_id: str, *, idempotency_key: Optional[str] = None) -> Invoice:
        """Only status ``new`` can be cancelled (otherwise ``409 conflict``)."""
        return Invoice.from_dict(self._c.request("POST", f"/v1/invoices/{_path(invoice_id)}/cancel", idempotency_key=idempotency_key))

    def export_csv(self, **filters: Any) -> str:
        """The whole filtered list as CSV text."""
        return self._c.request("GET", "/v1/invoices", query={**_filters(filters), "format": "csv"}, raw=True)

    def coin_options(self, invoice_id: str) -> List[CoinOption]:  # typing.List: `list` is a method of this class
        """Coins the payer may choose for an invoice created without ``coin_id`` — the list of the payment page,
        each with an informative ``≈`` ``estimated_amount``. Show it in your own UI and fix the choice with
        ``select_coin``. ``409 conflict`` (``details["coin_id"]``) once the coin is chosen. Permission ``read``."""
        out = self._c.request("GET", f"/v1/invoices/{_path(invoice_id)}/coin-options")
        return [CoinOption.from_dict(o) for o in out.get("coin_options") or []]

    def select_coin(self, invoice_id: str, coin_id: str, *, idempotency_key: Optional[str] = None) -> Invoice:
        """Fix the coin of an invoice created without ``coin_id`` — the same choice as the payer's on the payment
        page: allocates the address, prices a fiat amount now and locks the rate. Returns the invoice with
        ``address``, ``payer_amount``, ``payment_uri``, ``qr_payload`` and ``required_confirmations``.

        Only once and only while the invoice is ``new`` (otherwise ``409``, also with the same coin); a coin
        outside ``coin_options`` is ``400`` (``field == "coin_id"``); ``503`` (rate or address not available
        right now) is retried with the same ``idempotency_key``, which defaults to a random UUID.
        Permission ``create_invoice``.
        """
        body = {"coin_id": coin_id}
        return Invoice.from_dict(self._c.request("POST", f"/v1/invoices/{_path(invoice_id)}/coin", body=body, idempotency_key=idempotency_key))


class Addresses(_Resource):
    def create(self, coin_id: str, customer_id: str, *, label: Optional[str] = None, idempotency_key: Optional[str] = None) -> StaticAddress:
        """One active address per (coin, customer); a second request is ``409``."""
        body = _clean({"coin_id": coin_id, "customer_id": customer_id, "label": label})
        return StaticAddress.from_dict(self._c.request("POST", "/v1/addresses", body=body, idempotency_key=idempotency_key))

    def get(self, address_id: str, *, limit: Optional[int] = None, cursor: Optional[str] = None) -> StaticAddressCard:
        """The address with one page of its deposit history."""
        return StaticAddressCard.from_dict(self._c.request("GET", f"/v1/addresses/{_path(address_id)}", query={"limit": limit, "cursor": cursor}))

    def list(self, **filters: Any) -> Page[StaticAddress]:
        return Page.of(StaticAddress, self._c.request("GET", "/v1/addresses", query=_filters(filters)))

    def iterate(self, **filters: Any) -> Iterator[StaticAddress]:
        return self._c._paginate(StaticAddress, "/v1/addresses", _filters(filters))

    def archive(self, address_id: str, *, idempotency_key: Optional[str] = None) -> StaticAddress:
        """Stops offering the address. Archived addresses are still checked for incoming transfers
        regularly; a transfer is credited and restores the address automatically (webhook
        ``static_address.restored``). Archiving an archived address is a no-op."""
        return StaticAddress.from_dict(self._c.request("POST", f"/v1/addresses/{_path(address_id)}/archive", idempotency_key=idempotency_key))

    def restore(self, address_id: str, *, idempotency_key: Optional[str] = None) -> StaticAddress:
        """Makes an archived address active again (restoring an active one is a no-op).
        ``ApiError`` ``409 conflict`` with ``details["active_address_id"]`` when the customer already
        has another active address for the currency — archive that one first."""
        return StaticAddress.from_dict(self._c.request("POST", f"/v1/addresses/{_path(address_id)}/restore", idempotency_key=idempotency_key))


class Transactions(_Resource):
    def list(self, **filters: Any) -> Page[Transaction]:
        return Page.of(Transaction, self._c.request("GET", "/v1/transactions", query=_filters(filters)))

    def iterate(self, **filters: Any) -> Iterator[Transaction]:
        return self._c._paginate(Transaction, "/v1/transactions", _filters(filters))

    def get(self, transaction_id: str) -> Transaction:
        return Transaction.from_dict(self._c.request("GET", f"/v1/transactions/{_path(transaction_id)}"))

    def export_csv(self, **filters: Any) -> str:
        """The whole filtered list as CSV text (amounts as exact decimal text)."""
        return self._c.request("GET", "/v1/transactions", query={**_filters(filters), "format": "csv"}, raw=True)


class Withdrawals(_Resource):
    def estimate(self, coin_id: str, amount: Amount, *, to_address: Optional[str] = None) -> WithdrawalEstimate:
        """Fees and the total debit for ``amount`` of a coin — nothing is created."""
        body = _clean({"coin_id": coin_id, "amount": _amount(amount), "to_address": to_address})
        return WithdrawalEstimate.from_dict(self._c.request("POST", "/v1/withdrawals/estimate", body=body))

    def create(
        self,
        coin_id: str,
        amount: Amount,
        to_address: str,
        *,
        payout_id: Optional[str] = None,
        comment: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Withdrawal:
        """Queue a payout (``withdraw``; verified merchant). Give ``payout_id`` — repeating it with the
        same parameters returns the existing withdrawal instead of creating a second one."""
        body = _clean({"coin_id": coin_id, "amount": _amount(amount), "to_address": to_address, "payout_id": payout_id, "comment": comment})
        return Withdrawal.from_dict(self._c.request("POST", "/v1/withdrawals", body=body, idempotency_key=idempotency_key))

    def get(self, withdrawal_id: str) -> Withdrawal:
        return Withdrawal.from_dict(self._c.request("GET", f"/v1/withdrawals/{_path(withdrawal_id)}"))

    def list(self, **filters: Any) -> Page[Withdrawal]:
        return Page.of(Withdrawal, self._c.request("GET", "/v1/withdrawals", query=_filters(filters)))

    def iterate(self, **filters: Any) -> Iterator[Withdrawal]:
        return self._c._paginate(Withdrawal, "/v1/withdrawals", _filters(filters))

    def cancel(self, withdrawal_id: str, *, idempotency_key: Optional[str] = None) -> Withdrawal:
        """Only before processing starts (``pending``, ``review``, ``awaiting_approval``)."""
        return Withdrawal.from_dict(self._c.request("POST", f"/v1/withdrawals/{_path(withdrawal_id)}/cancel", idempotency_key=idempotency_key))


class WebhookEndpoints(_Resource):
    def list(self) -> list[WebhookEndpoint]:
        return [WebhookEndpoint.from_dict(d) for d in self._c.request("GET", "/v1/webhooks/endpoints").get("endpoints", [])]

    def create(self, url: str, events: list[str], *, secret: Optional[str] = None, idempotency_key: Optional[str] = None) -> WebhookEndpointCreated:
        """The response carries the signing ``secret`` once — store it on your server."""
        body = _clean({"url": url, "events": events, "secret": secret})
        return WebhookEndpointCreated.from_dict(self._c.request("POST", "/v1/webhooks/endpoints", body=body, idempotency_key=idempotency_key))

    def get(self, endpoint_id: str) -> WebhookEndpoint:
        return WebhookEndpoint.from_dict(self._c.request("GET", f"/v1/webhooks/endpoints/{_path(endpoint_id)}"))

    def update(self, endpoint_id: str, *, url: Optional[str] = None, events: Optional[list[str]] = None, enabled: Optional[bool] = None) -> WebhookEndpoint:
        body = {k: v for k, v in {"url": url, "events": events, "enabled": enabled}.items() if v is not None}
        return WebhookEndpoint.from_dict(self._c.request("PATCH", f"/v1/webhooks/endpoints/{_path(endpoint_id)}", body=body))

    def delete(self, endpoint_id: str) -> None:
        self._c.request("DELETE", f"/v1/webhooks/endpoints/{_path(endpoint_id)}")

    def rotate_secret(self, endpoint_id: str, *, idempotency_key: Optional[str] = None) -> str:
        """New secret, shown once; the old one stops verifying immediately."""
        return str(self._c.request("POST", f"/v1/webhooks/endpoints/{_path(endpoint_id)}/rotate-secret", idempotency_key=idempotency_key)["secret"])


class WebhookDeliveries(_Resource):
    def list(self, **filters: Any) -> Page[WebhookDelivery]:
        return Page.of(WebhookDelivery, self._c.request("GET", "/v1/webhooks/deliveries", query=_filters(filters)))

    def get(self, delivery_id: str) -> WebhookDelivery:
        return WebhookDelivery.from_dict(self._c.request("GET", f"/v1/webhooks/deliveries/{_path(delivery_id)}"))

    def retry(self, delivery_id: str, *, idempotency_key: Optional[str] = None) -> WebhookDelivery:
        return WebhookDelivery.from_dict(self._c.request("POST", f"/v1/webhooks/deliveries/{_path(delivery_id)}/retry", idempotency_key=idempotency_key))


class Webhooks(_Resource):
    def __init__(self, client: Client) -> None:
        super().__init__(client)
        self.endpoints = WebhookEndpoints(client)
        self.deliveries = WebhookDeliveries(client)


class Embed(_Resource):
    """Checkout widget allowlist (https://cryptolike.net/docs/widget §2)."""

    def get(self) -> EmbedSettings:
        return EmbedSettings.from_dict(self._c.request("GET", "/v1/embed"))

    def set(self, allowed_origins: list[str]) -> EmbedSettings:
        """Replaces the allowlist as a whole (``[]`` switches embedding off)."""
        return EmbedSettings.from_dict(self._c.request("PUT", "/v1/embed", body={"allowed_origins": allowed_origins}))


def _filters(filters: Mapping[str, Any]) -> dict[str, Any]:
    """``from_`` → ``from`` (a Python keyword); everything else as given."""
    return {("from" if k == "from_" else k): v for k, v in filters.items()}
