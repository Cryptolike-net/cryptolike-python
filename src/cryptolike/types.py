"""Typed views of API objects (https://cryptolike.net/docs/api). Amounts are ``Decimal`` — never floats.

Every dataclass is built with ``from_dict`` which ignores fields this SDK version does not know
(the API only adds fields within v1); the full response is kept in ``raw``.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, ClassVar, Generic, Optional, TypeVar, get_args, get_origin, get_type_hints

T = TypeVar("T", bound="Model")


def _convert(value: Any, hint: Any) -> Any:
    origin = get_origin(hint)
    if origin is not None and type(None) in get_args(hint):  # Optional[X]
        if value is None:
            return None
        inner = [a for a in get_args(hint) if a is not type(None)]
        return _convert(value, inner[0]) if len(inner) == 1 else value
    if hint is Decimal:
        return Decimal(str(value)) if value is not None else None
    if origin is list and get_args(hint):
        item = get_args(hint)[0]
        return [_convert(v, item) for v in (value or [])]
    if isinstance(hint, type) and issubclass(hint, Model) and isinstance(value, dict):
        return hint.from_dict(value)
    return value


@dataclass
class Model:
    """Base of every typed object; ``raw`` is the JSON as received."""

    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    _hints: ClassVar[Optional[dict[str, Any]]] = None

    @classmethod
    def from_dict(cls: type[T], data: dict[str, Any]) -> T:
        if cls._hints is None:
            cls._hints = get_type_hints(cls)
        kwargs: dict[str, Any] = {}
        for f in dataclasses.fields(cls):
            if f.name == "raw" or f.name not in data:
                continue
            kwargs[f.name] = _convert(data[f.name], cls._hints[f.name])
        obj = cls(**kwargs)
        obj.raw = data
        return obj


# ---- catalogue -------------------------------------------------------------------------------


@dataclass
class Currency(Model):
    id: str = ""
    ticker: str = ""
    name: str = ""
    network: str = ""
    network_name: str = ""
    decimals: int = 0
    confirmations: int = 0
    min_deposit: Decimal = Decimal(0)
    maintenance: bool = False
    icon_url: Optional[str] = None
    invoice_enabled: Optional[bool] = None
    """«Available for invoices» (signed ``currencies.list`` only; ``None`` in the public catalogue).
    ``False``: still usable for static addresses and payouts, not offered on invoices."""


@dataclass
class Rate(Model):
    coin_id: str = ""
    ticker: str = ""
    network: str = ""
    rate: Decimal = Decimal(0)
    at: str = ""
    stale: bool = False


@dataclass
class Balance(Model):
    coin_id: str = ""
    ticker: str = ""
    network: str = ""
    available: Decimal = Decimal(0)
    pending: Decimal = Decimal(0)
    hold: Decimal = Decimal(0)


# ---- invoices --------------------------------------------------------------------------------


@dataclass
class InvoiceEvent(Model):
    from_status: Optional[str] = None
    to_status: str = ""
    at: str = ""
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class Invoice(Model):
    id: str = ""
    status: str = ""
    currency: str = ""
    amount: Decimal = Decimal(0)
    coin_id: Optional[str] = None
    ticker: Optional[str] = None
    network: Optional[str] = None
    address: Optional[str] = None
    amount_crypto: Optional[Decimal] = None
    payer_amount: Optional[Decimal] = None
    paid_amount: Decimal = Decimal(0)
    remaining_amount: Decimal = Decimal(0)
    overpaid_amount: Decimal = Decimal(0)
    amount_fiat: Optional[Decimal] = None
    fiat_currency: Optional[str] = None
    rate_locked: Optional[Decimal] = None
    rate_locked_at: Optional[str] = None
    late_payment: bool = False
    fee_paid_by: str = "merchant"
    order_id: Optional[str] = None
    description: Optional[str] = None
    return_url: Optional[str] = None
    webhook_url: Optional[str] = None
    metadata: Optional[dict[str, str]] = None
    """Your key → value pairs from creation (never shown to the payer)."""
    allowed_coin_ids: Optional[list[str]] = None
    """Coins the payer may choose from; ``None`` — every coin you offer for invoices."""
    checkout_url: str = ""
    ttl_expires_at: str = ""
    expired_at: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    events: list[InvoiceEvent] = field(default_factory=list)
    """Status timeline — only on ``invoices.get``."""

    @property
    def is_final(self) -> bool:
        return self.status in ("paid", "expired", "cancelled")


# ---- static addresses ------------------------------------------------------------------------


@dataclass
class StaticAddress(Model):
    id: str = ""
    coin_id: str = ""
    ticker: str = ""
    network: str = ""
    address: str = ""
    customer_id: str = ""
    label: Optional[str] = None
    status: str = ""
    total_received: Decimal = Decimal(0)
    tx_count: int = 0
    last_deposit_at: Optional[str] = None
    created_at: str = ""
    archived_at: Optional[str] = None
    """Last archive (``None`` when never archived)."""
    archive_reason: Optional[str] = None
    """``manual`` | ``auto`` (no incoming transfers for the auto-archive period)."""
    restored_at: Optional[str] = None
    """Last restore (``None`` when never restored)."""
    restore_reason: Optional[str] = None
    """``manual`` | ``deposit`` (an incoming transfer reached the archived address)."""
    restore_conflict_with: Optional[str] = None
    """Id of the customer's other active address when a deposit restored this one (both stay active)."""
    archive_warned_at: Optional[str] = None
    """The automatic-archive warning was sent (webhook ``static_address.archive_warning``)."""


# ---- static-address archive webhooks (https://cryptolike.net/docs/webhooks §5) ------------------------------------


@dataclass
class WebhookStaticAddress(Model):
    """The address inside ``static_address.*`` / ``deposit.*`` webhooks (``coin`` is the ticker)."""

    id: str = ""
    address: str = ""
    coin_id: str = ""
    coin: str = ""
    network: str = ""
    customer_id: str = ""
    label: Optional[str] = None
    status: str = ""
    total_received: Decimal = Decimal(0)
    tx_count: int = 0
    last_deposit_at: Optional[str] = None
    created_at: str = ""
    archived_at: Optional[str] = None
    archive_reason: Optional[str] = None
    restored_at: Optional[str] = None
    restore_reason: Optional[str] = None
    restore_conflict_with: Optional[str] = None


@dataclass
class StaticAddressRestoreDeposit(Model):
    """The incoming transfer that reached an archived address (credited with its own ``deposit.*`` events)."""

    chain_transaction_id: str = ""
    txid: str = ""
    output_index: int = 0
    amount: Decimal = Decimal(0)
    block_height: int = 0


@dataclass
class StaticAddressArchivedData(Model):
    """``data`` of ``static_address.archived``: ``reason`` ``manual`` (you) | ``auto``."""

    address: Optional[WebhookStaticAddress] = None
    reason: str = ""


@dataclass
class StaticAddressRestoredData(Model):
    """``data`` of ``static_address.restored``: ``reason`` ``manual`` | ``deposit`` (with ``deposit``).
    ``conflict_with`` — the customer's other active address for the currency (both stay active)."""

    address: Optional[WebhookStaticAddress] = None
    reason: str = ""
    deposit: Optional[StaticAddressRestoreDeposit] = None
    conflict_with: Optional[str] = None


@dataclass
class StaticAddressArchiveWarningData(Model):
    """``data`` of ``static_address.archive_warning``: archived automatically after ``archive_after``
    unless an incoming transfer arrives (no transfers since ``inactive_since``)."""

    address: Optional[WebhookStaticAddress] = None
    reason: str = ""
    archive_after: str = ""
    inactive_since: str = ""


# ---- transactions ----------------------------------------------------------------------------


@dataclass
class Transaction(Model):
    id: str = ""
    kind: str = ""
    created_at: str = ""
    coin_id: str = ""
    ticker: str = ""
    network: str = ""
    amount: Decimal = Decimal(0)
    fee: Optional[Decimal] = None
    net_amount: Optional[Decimal] = None
    status: str = ""
    confirmations: Optional[int] = None
    required_confirmations: Optional[int] = None
    txid: Optional[str] = None
    output_index: Optional[int] = None
    address: Optional[str] = None
    invoice_id: Optional[str] = None
    order_id: Optional[str] = None
    static_address_id: Optional[str] = None
    customer_id: Optional[str] = None
    late_payment: bool = False
    confirmed_at: Optional[str] = None
    comment: Optional[str] = None


@dataclass
class StaticAddressCard(Model):
    address: StaticAddress = field(default_factory=StaticAddress)
    history: "Page[Transaction]" = field(default_factory=lambda: Page())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StaticAddressCard":
        obj = cls(address=StaticAddress.from_dict(data.get("address") or {}), history=Page.of(Transaction, data.get("history") or {}))
        obj.raw = data
        return obj


# ---- withdrawals -----------------------------------------------------------------------------


@dataclass
class WithdrawalEstimate(Model):
    coin_id: str = ""
    ticker: str = ""
    network: str = ""
    amount: Decimal = Decimal(0)
    platform_fee: Decimal = Decimal(0)
    network_fee: Decimal = Decimal(0)
    total_debit: Decimal = Decimal(0)
    min_amount: Decimal = Decimal(0)
    network_fee_mode: str = ""
    address_warning: Optional[str] = None


@dataclass
class Withdrawal(Model):
    id: str = ""
    status: str = ""
    coin_id: str = ""
    ticker: str = ""
    network: str = ""
    amount: Decimal = Decimal(0)
    platform_fee: Decimal = Decimal(0)
    network_fee_estimated: Decimal = Decimal(0)
    network_fee_charged: Optional[Decimal] = None
    total_debit: Decimal = Decimal(0)
    to_address: str = ""
    payout_id: Optional[str] = None
    comment: Optional[str] = None
    txid: Optional[str] = None
    confirmations: int = 0
    review_reason: Optional[str] = None
    failure_reason: Optional[str] = None
    failure_code: Optional[str] = None
    address_warning: Optional[str] = None
    batch_id: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    completed_at: Optional[str] = None

    @property
    def is_final(self) -> bool:
        return self.status in ("completed", "failed", "rejected", "cancelled")


# ---- webhooks --------------------------------------------------------------------------------


@dataclass
class WebhookEndpoint(Model):
    id: str = ""
    kind: str = ""
    invoice_id: Optional[str] = None
    url: str = ""
    events: list[str] = field(default_factory=list)
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""


@dataclass
class WebhookEndpointCreated(Model):
    """``secret`` is shown once — store it on your server, never log it."""

    endpoint: WebhookEndpoint = field(default_factory=WebhookEndpoint)
    secret: str = field(default="", repr=False)


@dataclass
class WebhookDelivery(Model):
    id: str = ""
    endpoint_id: str = ""
    event_id: str = ""
    event_type: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    attempt: int = 0
    status: str = ""
    response_code: Optional[int] = None
    response_body: Optional[str] = None
    last_error: Optional[str] = None
    next_attempt_at: Optional[str] = None
    last_attempt_at: Optional[str] = None
    delivered_at: Optional[str] = None
    created_at: str = ""


@dataclass
class EmbedSettings(Model):
    allowed_origins: list[str] = field(default_factory=list)


# ---- pagination ------------------------------------------------------------------------------

I = TypeVar("I", bound=Model)


@dataclass
class Page(Generic[I]):
    """One page of a cursor-paginated list; pass ``next_cursor`` back as ``cursor`` for the next one."""

    items: list[I] = field(default_factory=list)
    next_cursor: Optional[str] = None

    @classmethod
    def of(cls, item_type: type[I], data: dict[str, Any]) -> "Page[I]":
        return cls(items=[item_type.from_dict(d) for d in data.get("items") or []], next_cursor=data.get("next_cursor"))

    def __iter__(self):
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)
