"""Static-address archive: archive an address you no longer give out, list the archive, restore.

    CRYPTOLIKE_API_KEY_ID=ck_test_… CRYPTOLIKE_API_SECRET=… python examples/static_address_archive.py <address-id>

An archived address is never deleted or re-issued. It leaves real-time scanning, but the platform
checks it for incoming transfers regularly (every 24 h by default); a transfer found is credited as
usual and restores the address automatically (webhook static_address.restored, reason "deposit")
— see examples/flask_webhook.py.
"""

import sys

from cryptolike import ApiError, Client

api = Client()  # credentials from the environment
address_id = sys.argv[1]

archived = api.archive_static_address(address_id, idempotency_key=f"archive-{address_id}")  # a repeat is a no-op
print("archived", archived.address, archived.archived_at, archived.archive_reason)

# The archive: same filters as the address list (search, coin_id, customer_id), newest first.
for a in api.iterate_archived_static_addresses(customer_id=archived.customer_id):
    print("in the archive:", a.id, a.address, a.archive_reason)  # manual | auto

try:
    active = api.restore_static_address(address_id, idempotency_key=f"restore-{address_id}")
    print("active again", active.address, active.restored_at)
except ApiError as e:
    # The customer got another active address for this coin meanwhile: one active address per
    # (customer, coin). Archive that one first if this is the address to keep.
    if e.status != 409:
        raise
    print("restore refused; active address:", e.details.get("active_address_id"))
