# cryptolike (Python SDK)

Official **server-side** SDK for the CryptoLike merchant API (Python ≥ 3.10, `requests`; typed, `py.typed`).

- Signed requests (`X-API-Key` / `X-Timestamp` / `X-Signature`, HMAC-SHA256 — [Authentication](https://cryptolike.net/docs/auth))
- `Idempotency-Key` generated for every `POST` (pass your own to make retries exact)
- Retries with exponential backoff and jitter on `429` (honours `Retry-After`), `5xx` and transport errors
- Dataclasses for every object; amounts are `Decimal` — floats are refused
- `verify_webhook()` — signature and timestamp check of webhook deliveries ([Webhooks](https://cryptolike.net/docs/webhooks))

## The secret stays on your server

> The API secret signs requests *as you*: whoever has it can create payments, read balances and
> change your webhooks. Use this SDK from your backend only; never put the secret into code that
> ships to a browser or a mobile app. Your page needs only the invoice id / `checkout_url` your
> server obtained — see [Payment widget §1](https://cryptolike.net/docs/widget).

The client reads `CRYPTOLIKE_API_KEY_ID` and `CRYPTOLIKE_API_SECRET` from the environment when you
do not pass them, never logs them and redacts them from `repr(client)` — and therefore from
`print(client)`, f-strings and most log formatters. `vars(client)` still shows `_secret`.

## Install

```bash
pip install cryptolike
```

## Quickstart — to the first confirmed payment

```python
from cryptolike import Client, ApiError

api = Client()  # CRYPTOLIKE_API_KEY_ID + CRYPTOLIKE_API_SECRET from the environment

# 1. Create a payment on your server; send the customer to checkout_url.
invoice = api.invoices.create(
    amount="10.50", currency="USD", order_id="A-1001",
    return_url="https://shop.example/orders/A-1001",
    idempotency_key="order-A-1001",  # a retry never creates a second payment
)
redirect(invoice.checkout_url)

# 2. Learn the outcome from the webhook (below) — or poll:
detail = api.invoices.get(invoice.id)
if detail.status == "paid":
    fulfil(detail.order_id)
```

```python
# 3. Webhook receiver (Flask) — verify over the RAW body, dedupe by event_id, answer 2xx fast.
from flask import request, abort
from cryptolike import verify_webhook, WebhookVerificationError

@app.post("/hooks/cryptolike")
def hook():
    try:
        event = verify_webhook(request.headers, request.get_data(), os.environ["CRYPTOLIKE_WEBHOOK_SECRET"])
    except WebhookVerificationError:
        abort(401)
    if not already_seen(event.event_id):
        handle(event)  # event.type == "invoice.paid" → fulfil
    return "", 200
```

Full examples: [`examples/create_payment.py`](https://github.com/Cryptolike-net/cryptolike-python/blob/main/examples/create_payment.py), [`examples/flask_webhook.py`](https://github.com/Cryptolike-net/cryptolike-python/blob/main/examples/flask_webhook.py).

## Resources

| Resource | Methods |
|---|---|
| `api.invoices` | `create(amount, currency, *, coin_id, allowed_coin_ids, description, order_id, ttl_seconds, return_url, webhook_url, fee_paid_by, metadata, idempotency_by_order_id, idempotency_key)`, `get(id)`, `list(**filters)`, `iterate(**filters)`, `cancel(id)`, `export_csv(**filters)`, `coin_options(id)`, `select_coin(id, coin_id, *, idempotency_key)` |
| `api.addresses` | `create(coin_id, customer_id, *, label)`, `get(id, limit=, cursor=)`, `list`, `iterate`, `archive(id)`, `restore(id)` |
| `api` (archive) | `archive_static_address(id)`, `restore_static_address(id)`, `list_archived_static_addresses(**filters)`, `iterate_archived_static_addresses(**filters)` |
| `api.transactions` | `list`, `iterate`, `get`, `export_csv` |
| `api.balances` / `api.currencies` / `api.rates` | `list()` (`rates.list(fiat="EUR")`) |
| `api.withdrawals` | `estimate(coin_id, amount, *, to_address)`, `create(coin_id, amount, to_address, *, payout_id, comment)`, `get`, `list`, `iterate`, `cancel` |
| `api.webhooks.endpoints` | `list`, `create(url, events, *, secret)`, `get`, `update(id, *, url, events, enabled)`, `delete`, `rotate_secret` |
| `api.webhooks.deliveries` | `list`, `get`, `retry` |
| `api.embed` | `get()`, `set(allowed_origins)` — checkout widget allowlist |
| `api.request(method, path, query=, body=, idempotency_key=)` | any endpoint, signed |

### Static-address archive

Archiving stops offering an address and takes it out of real-time scanning; it is never deleted, and its address is never issued again. The platform checks archived addresses for incoming transfers regularly (every 24 h by default): a transfer found is credited as usual and restores the address automatically (webhook `static_address.restored`, reason `deposit`). Addresses with no incoming transfers for the platform's period (180 days by default) are archived automatically after a warning 7 days earlier (`static_address.archive_warning`, then `static_address.archived` with reason `auto`).

```python
api.archive_static_address(address_id)                    # POST /v1/addresses/{id}/archive — a repeat is a no-op
page = api.list_archived_static_addresses(search="shop")  # status=archived; coin_id, customer_id, limit, cursor
for a in api.iterate_archived_static_addresses():
    print(a.id, a.archive_reason)
api.restore_static_address(address_id)                    # 409 conflict + details["active_address_id"]: archive the other active address first
```

`event.typed_data()` of a verified `static_address.*` event returns `StaticAddressArchivedData` / `StaticAddressRestoredData` (`reason`, `deposit`, `conflict_with`) / `StaticAddressArchiveWarningData` (`archive_after`, `inactive_since`); constants `STATIC_ADDRESS_ARCHIVED|RESTORED|ARCHIVE_WARNING`. Full example: `examples/static_address_archive.py`, `examples/flask_webhook.py`.

List filters are keyword arguments named as in the API (`status="paid"`, `from_="2026-09-01T00:00:00Z"`
— `from_` because `from` is a keyword). `list()` returns a `Page` (`items`, `next_cursor`);
`iterate()` walks every page. Unknown response fields are kept in `.raw`.

## Payer chooses the coin

An invoice in a fiat currency (or a ticker on several networks) without `coin_id` lets the payer
choose the coin on the payment page; each option shows an estimate (≈) — the exact amount and
rate are fixed when the payer picks. `allowed_coin_ids` narrows the choice for one invoice; leave
it out to offer every coin you offer for invoices:

```python
ids = [c.id for c in api.currencies.list()  # signed: each coin carries invoice_enabled
       if c.invoice_enabled is not False and not c.maintenance and c.ticker == "USDT"]
inv = api.invoices.create(amount="49.90", currency="USD", allowed_coin_ids=ids)
# inv.allowed_coin_ids — the kept set; None = every coin offered for invoices.
```

The server keeps the ids you offer for invoices («Available for invoices» in the cabinet → Coins)
that match `currency`, drops the rest; none left → `ApiError` 422 `no_allowed_coins`
(`field == "allowed_coin_ids"`). Not combinable with `coin_id` (400). An empty list raises
`ValueError` before the request — a filter that found nothing never turns into "every coin".

## Your own payment page (host-to-host)

Instead of sending the payer to `checkout_url` you can show the payment inside your own checkout
([Host-to-host guide](https://cryptolike.net/docs/h2h)). Every call below runs **on your server**;
your page receives only what it renders.

```python
# 1. An invoice without coin_id — the payer picks the coin on your page.
inv = api.invoices.create(amount="49.90", currency="USD", order_id="A-1001", idempotency_key="order-A-1001")

# 2. The coins to offer, each with an informative ≈ amount (None + estimate_reason when it cannot be priced now).
options = [
    {"id": o.coin_id, "label": f"{o.ticker} · {o.network_name}", "approx": o.estimated_amount}
    for o in api.invoices.coin_options(inv.id)
]

# 3. The payer's choice fixes the coin, the address and — for a fiat amount — the rate.
#    One key per (invoice, coin): a retry replays the first answer instead of choosing twice.
pay = api.invoices.select_coin(inv.id, chosen_coin_id, idempotency_key=f"select-{inv.id}-{chosen_coin_id}")

# 4. The payment block of your page.
block = {
    "network": pay.network,                         # name the network next to the address — a transfer on another network is lost
    "address": pay.address,                         # with a copy button
    "amount": f"{pay.payer_amount} {pay.ticker}",   # exactly this amount (Decimal), with a copy button
    "qr": pay.qr_payload,                           # the bare address on every network (exchanges refuse a URI) — keep the amount next to it
    "wallet_link": pay.payment_uri,                 # "Open in wallet"
    "expires_at": pay.ttl_expires_at,               # countdown
}

# 5. Progress for the page: poll from your server (the browser never talks to the API).
now = api.invoices.get(inv.id)
progress = (now.status, now.confirmations, now.required_confirmations, now.remaining_amount, now.payment_uri)
```

- **Fulfil the order on the `invoice.paid` webhook only** (verified as below), never on what the
  page shows. `underpaid` — the payer may top up to the same address until `ttl_expires_at`: show
  `remaining_amount`, `payment_uri` already asks for the remainder. `overpaid_amount` above zero —
  credited, settle the difference with the customer. `late_payment` — a transfer after the
  invoice closed was credited; decide whether to fulfil or refund.
- `qr_payload` is the bare address on every network (never a scheme or an amount): many payers send from
  an exchange, and its withdrawal form accepts an address only. To offer a "QR with amount" for
  wallets (BIP-21, Bitcoin/Litecoin), encode `payment_uri` on the payer's choice.
- `payment_uri`, `qr_payload`, `confirmations` and `required_confirmations` are `None` until the
  coin is chosen; `confirmations` stays `None` while nothing was seen on the chain. Webhook payloads
  do not carry them — read the invoice.
- `select_coin` refusals: `409 conflict` — the coin is already chosen (also with the same coin) or
  the invoice is no longer `new`: read the invoice; `400` with `field == "coin_id"` — the coin is
  not among `coin_options`; `503` — the rate or an address is not available right now: the SDK
  retries it with the same key. `coin_options` on an invoice with a coin is `409`
  (`details["coin_id"]`).

## Errors

Every non-2xx response raises `ApiError` with `status`, `code` (stable machine code), `message`,
`details`, `field`, `retry_after` and `request_id`. Transport failures after all retries raise
`NetworkError`. Codes: [Authentication §5](https://cryptolike.net/docs/auth), [API §5](https://cryptolike.net/docs/api).
A payout with a key whose `withdraw` a developer requested and an owner/admin has not approved yet is
`403` `WITHDRAW_PENDING_APPROVAL` (`e.withdraw_pending_approval`): nothing was created, not retried —
approve the request in the cabinet (API keys); the key's other permissions work meanwhile.

## Retries and idempotency

`429` (after `Retry-After`, or backoff), `5xx` except `501`, and connection/timeout errors are
retried up to `max_retries` (default 3) with `min(retry_max, retry_base · 2^attempt)` full-jitter
backoff. Every `POST` carries an `Idempotency-Key` (yours, or a UUID generated once per call and
reused on each retry), so a retry never creates a second invoice, address or withdrawal. Each
attempt is signed with a fresh timestamp. A `401 auth.replay` — two identical requests signed
within the same second share a signature — waits for the next second and signs afresh.

## Options

```python
Client(key_id=None, secret=None, *, base_url=None, session=None, timeout=30.0,
       max_retries=3, retry_base=0.5, retry_max=8.0, retry_after_max=30.0,
       api_version="v1", app_info="my-shop/2.3")
```

## Webhook verification

`verify_webhook(headers, raw_body, secret, *, tolerance=300, now=None)` returns a `WebhookEvent`
(`event_id`, `type`, `created_at`, `data`, `raw`) or raises `WebhookVerificationError` (`reason`:
`missing_header` | `invalid_timestamp` | `timestamp_out_of_tolerance` | `invalid_signature` |
`invalid_body`). `raw_body` must be the bytes as received (Flask `request.get_data()`, Django
`request.body`). Answer `401`/`400` on failure, `2xx` on success, and deduplicate by `event_id` —
deliveries are at least once and may arrive out of order.
