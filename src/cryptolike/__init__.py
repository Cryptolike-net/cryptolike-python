"""cryptolike — official server-side SDK for the CryptoLike merchant API.

    from cryptolike import Client, verify_webhook

    api = Client()  # CRYPTOLIKE_API_KEY_ID + CRYPTOLIKE_API_SECRET from the environment
    invoice = api.invoices.create(amount="10.50", currency="USD", order_id="A-1001")

The API secret must stay on your server (https://cryptolike.net/docs/widget §1).
"""

from ._version import USER_AGENT, VERSION
from .auth import body_sha256, canonical_string, sign
from .client import Client
from .errors import WITHDRAW_PENDING_APPROVAL, ApiError, CryptoLikeError, NetworkError, WebhookVerificationError
from .types import (
    Balance,
    Currency,
    EmbedSettings,
    Invoice,
    InvoiceEvent,
    Page,
    Rate,
    StaticAddress,
    StaticAddressArchivedData,
    StaticAddressArchiveWarningData,
    StaticAddressCard,
    StaticAddressRestoreDeposit,
    StaticAddressRestoredData,
    Transaction,
    WebhookDelivery,
    WebhookEndpoint,
    WebhookEndpointCreated,
    WebhookStaticAddress,
    Withdrawal,
    WithdrawalEstimate,
)
from .webhook import (
    EVENT_DATA_TYPES,
    STATIC_ADDRESS_ARCHIVE_WARNING,
    STATIC_ADDRESS_ARCHIVED,
    STATIC_ADDRESS_RESTORED,
    WebhookEvent,
    verify_webhook,
    webhook_signature,
)

__version__ = VERSION
__all__ = [
    "Client",
    "ApiError",
    "WITHDRAW_PENDING_APPROVAL",
    "CryptoLikeError",
    "NetworkError",
    "WebhookVerificationError",
    "WebhookEvent",
    "verify_webhook",
    "webhook_signature",
    "EVENT_DATA_TYPES",
    "STATIC_ADDRESS_ARCHIVED",
    "STATIC_ADDRESS_RESTORED",
    "STATIC_ADDRESS_ARCHIVE_WARNING",
    "sign",
    "canonical_string",
    "body_sha256",
    "Balance",
    "Currency",
    "EmbedSettings",
    "Invoice",
    "InvoiceEvent",
    "Page",
    "Rate",
    "StaticAddress",
    "StaticAddressCard",
    "StaticAddressArchivedData",
    "StaticAddressRestoredData",
    "StaticAddressArchiveWarningData",
    "StaticAddressRestoreDeposit",
    "WebhookStaticAddress",
    "Transaction",
    "WebhookDelivery",
    "WebhookEndpoint",
    "WebhookEndpointCreated",
    "Withdrawal",
    "WithdrawalEstimate",
    "VERSION",
    "USER_AGENT",
]
