"""Flask webhook receiver: verify over the RAW body, deduplicate by event_id, acknowledge fast
(https://cryptolike.net/docs/webhooks §3–§4).

    CRYPTOLIKE_WEBHOOK_SECRET=whsec_… flask --app examples/flask_webhook run
"""

import os

from flask import Flask, abort, request

from cryptolike import (
    StaticAddressArchivedData,
    StaticAddressArchiveWarningData,
    StaticAddressRestoredData,
    WebhookVerificationError,
    verify_webhook,
)

SECRET = os.environ["CRYPTOLIKE_WEBHOOK_SECRET"]
app = Flask(__name__)
seen: set[str] = set()  # use your database in production


@app.post("/hooks/cryptolike")
def hook():
    try:
        # request.get_data() is the body as received — never re-serialise request.json
        event = verify_webhook(request.headers, request.get_data(), SECRET)  # default tolerance: 300 s
    except WebhookVerificationError as e:
        abort(401 if e.reason == "invalid_signature" else 400)

    if event.event_id in seen:  # duplicate delivery — already handled
        return "", 200
    seen.add(event.event_id)

    # `data` is the full state of the object at `created_at`; deliveries may arrive out of order —
    # decide by data["invoice"]["status"], not by arrival.
    if event.type == "invoice.paid":
        invoice = event.data["invoice"]
        print("paid", invoice.get("order_id"), invoice.get("paid_amount"))
        # fulfil the order (after responding, e.g. via a task queue)
    elif event.type in ("invoice.expired", "invoice.cancelled"):
        print(event.type, event.data["invoice"]["id"])
    elif event.type.startswith("static_address."):
        data = event.typed_data()  # StaticAddress*Data for the three archive events
        if isinstance(data, StaticAddressArchiveWarningData):
            print("will be archived", data.address.customer_id, data.archive_after)  # unless a transfer arrives
        elif isinstance(data, StaticAddressArchivedData):
            print("archived", data.address.id, data.reason)  # manual | auto — stop showing it to the customer
        elif isinstance(data, StaticAddressRestoredData):
            # reason "deposit": a transfer reached the archived address; it is credited with its own deposit.* events
            print("active again", data.address.id, data.reason, data.deposit.txid if data.deposit else None)
            if data.conflict_with:
                print("the customer has two active addresses; archive one:", data.conflict_with)
    elif event.type == "ping":
        pass  # test event from the cabinet
    return "", 200
