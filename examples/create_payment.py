"""Create a payment and print the page to send the payer to.

    CRYPTOLIKE_API_KEY_ID=ck_test_… CRYPTOLIKE_API_SECRET=… python examples/create_payment.py

Runs on your server. The browser only ever receives ``checkout_url`` / the invoice id.
"""

from cryptolike import ApiError, Client

api = Client()  # credentials from the environment

try:
    invoice = api.invoices.create(
        amount="10.50",
        currency="USD",
        order_id="A-1001",
        return_url="https://shop.example/orders/A-1001",
        idempotency_key="order-A-1001",  # a retry never creates a second payment
    )
    print("send the customer to", invoice.checkout_url)

    # The payer chooses the coin on the payment page among the ones you allow for this invoice
    # (here: every stablecoin you offer for invoices). Omit allowed_coin_ids to offer them all.
    stable = [
        c.id
        for c in api.currencies.list()
        if c.invoice_enabled is not False and not c.maintenance and c.ticker in ("USDT", "USDC")
    ]
    if stable:
        choice = api.invoices.create(
            amount="10.50",
            currency="USD",
            order_id="A-1002",
            allowed_coin_ids=stable,  # 422 no_allowed_coins when none is offered for invoices in USD
            idempotency_key="order-A-1002",
        )
        print("payer picks one of", len(choice.allowed_coin_ids or []), "coins at", choice.checkout_url)

    detail = api.invoices.get(invoice.id)
    print("status", detail.status, "events", len(detail.events))
except ApiError as e:
    # Stable machine code + human message; `field` for validation errors.
    print(f"API {e.status} {e.code}{f' ({e.field})' if e.field else ''}: {e.message}")
