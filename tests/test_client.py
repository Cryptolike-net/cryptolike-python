import json
import os
from decimal import Decimal

import pytest
import requests

from cryptolike import (
    STATIC_ADDRESS_ARCHIVE_WARNING,
    STATIC_ADDRESS_ARCHIVED,
    STATIC_ADDRESS_RESTORED,
    WITHDRAW_PENDING_APPROVAL,
    ApiError,
    Client,
    Invoice,
    NetworkError,
    StaticAddressArchivedData,
    StaticAddressArchiveWarningData,
    StaticAddressRestoredData,
    sign,
    verify_webhook,
    webhook_signature,
)

SECRET = "kZ1Yv7pQ2xL9mN4tR8wE3uH6jB5cF0aD1gS2hK7lP9o"
KEY = "ck_test_4f9k2m7p1q8r3s6t0u5v9w2x"


def resp(status, body=None, headers=None, text=None):
    r = requests.Response()
    r.status_code = status
    r.headers.update(headers or {})
    if text is not None:
        r._content = text.encode("utf-8")
    elif body is not None:
        r._content = json.dumps(body).encode("utf-8")
        r.headers.setdefault("Content-Type", "application/json; charset=utf-8")
    else:
        r._content = b""
    return r


class FakeSession:
    """Records requests; answers from a queue (the last answer repeats). Callables may raise."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.seen = []

    def request(self, method, url, headers=None, data=None, timeout=None, allow_redirects=None):
        self.seen.append({"method": method, "url": url, "headers": dict(headers), "data": data, "timeout": timeout})
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return a() if callable(a) else a


def make(answers, **kw):
    fs = FakeSession(answers)
    sleeps = []
    opts = dict(base_url="https://api.test/", session=fs, now=lambda: 1758000000, sleep=sleeps.append)
    opts.update(kw)
    return Client(KEY, SECRET, **opts), fs, sleeps


def test_create_invoice_signs_and_sets_headers():
    api, fs, sleeps = make([resp(201, {"id": "inv-1", "amount": "10.50", "status": "new", "checkout_url": "https://pay/x"})])
    inv = api.invoices.create(amount="10.50", currency="USD", order_id="A-1001")
    assert isinstance(inv, Invoice) and inv.id == "inv-1" and inv.amount == Decimal("10.50")
    r = fs.seen[0]
    assert r["method"] == "POST" and r["url"] == "https://api.test/v1/invoices"
    assert r["data"] == b'{"amount":"10.50","currency":"USD","order_id":"A-1001"}'
    h = r["headers"]
    assert h["X-API-Key"] == KEY and h["X-Timestamp"] == "1758000000"
    assert h["X-Signature"] == "7878d965e6eb95b80df12e34e9466d40a8e12aa612653f72760f5dc5c06323a1"  # https://cryptolike.net/docs/auth §3 vector
    assert h["Content-Type"] == "application/json" and h["X-API-Version"] == "v1"
    assert h["User-Agent"].startswith("cryptolike-sdk-python/")
    assert len(h["Idempotency-Key"]) == 36
    assert r["timeout"] == 30.0

    api.invoices.create(amount=Decimal("1.5"), currency="USD", idempotency_key="order-1")
    assert fs.seen[1]["headers"]["Idempotency-Key"] == "order-1"
    assert fs.seen[1]["data"] == b'{"amount":"1.5","currency":"USD"}'

    with pytest.raises(TypeError):
        api.invoices.create(amount=10.5, currency="USD")


def test_get_and_query_signing():
    api, fs, _ = make([resp(200, {"balances": [{"coin_id": "c", "available": "1.25", "pending": "0", "hold": "0"}]})])
    bal = api.balances.list()
    assert bal[0].available == Decimal("1.25")
    g = fs.seen[0]
    assert "Idempotency-Key" not in g["headers"] and "Content-Type" not in g["headers"] and g["data"] is None
    assert g["headers"]["X-Signature"] == sign(SECRET, 1758000000, "GET", "/v1/balances", b"")

    api.invoices.list(status="paid", from_="2026-09-01T00:00:00Z", limit=50)
    q = fs.seen[1]
    assert q["url"] == "https://api.test/v1/invoices?status=paid&from=2026-09-01T00%3A00%3A00Z&limit=50"
    assert q["headers"]["X-Signature"] == sign(SECRET, 1758000000, "GET", "/v1/invoices?status=paid&from=2026-09-01T00%3A00%3A00Z&limit=50", b"")


def test_every_resource_hits_the_documented_route():
    page = {"items": [], "next_cursor": None, "balances": [], "currencies": [], "rates": [], "endpoints": [], "allowed_origins": [], "secret": "s", "address": {}, "history": {}}
    api, fs, _ = make([resp(200, page)])
    calls = [
        (lambda: api.currencies.list(), "GET", "/v1/currencies"),
        (lambda: api.rates.list(fiat="EUR"), "GET", "/v1/rates?fiat=EUR"),
        (lambda: api.balances.list(), "GET", "/v1/balances"),
        (lambda: api.addresses.create("c", "u1", label="L"), "POST", "/v1/addresses"),
        (lambda: api.addresses.get("a1", limit=5), "GET", "/v1/addresses/a1?limit=5"),
        (lambda: api.addresses.list(customer_id="u1"), "GET", "/v1/addresses?customer_id=u1"),
        (lambda: api.addresses.archive("a1"), "POST", "/v1/addresses/a1/archive"),
        (lambda: api.addresses.restore("a1"), "POST", "/v1/addresses/a1/restore"),
        (lambda: api.transactions.list(kind="deposit"), "GET", "/v1/transactions?kind=deposit"),
        (lambda: api.transactions.get("t1"), "GET", "/v1/transactions/t1"),
        (lambda: api.withdrawals.estimate("c", "1"), "POST", "/v1/withdrawals/estimate"),
        (lambda: api.withdrawals.create("c", "1", "bc1q", payout_id="p-1"), "POST", "/v1/withdrawals"),
        (lambda: api.withdrawals.get("w1"), "GET", "/v1/withdrawals/w1"),
        (lambda: api.withdrawals.list(status="completed"), "GET", "/v1/withdrawals?status=completed"),
        (lambda: api.withdrawals.cancel("w1"), "POST", "/v1/withdrawals/w1/cancel"),
        (lambda: api.webhooks.endpoints.list(), "GET", "/v1/webhooks/endpoints"),
        (lambda: api.webhooks.endpoints.create("https://shop.example/hook", ["*"]), "POST", "/v1/webhooks/endpoints"),
        (lambda: api.webhooks.endpoints.get("e1"), "GET", "/v1/webhooks/endpoints/e1"),
        (lambda: api.webhooks.endpoints.update("e1", enabled=False), "PATCH", "/v1/webhooks/endpoints/e1"),
        (lambda: api.webhooks.endpoints.delete("e1"), "DELETE", "/v1/webhooks/endpoints/e1"),
        (lambda: api.webhooks.endpoints.rotate_secret("e1"), "POST", "/v1/webhooks/endpoints/e1/rotate-secret"),
        (lambda: api.webhooks.deliveries.list(status="failed"), "GET", "/v1/webhooks/deliveries?status=failed"),
        (lambda: api.webhooks.deliveries.get("d1"), "GET", "/v1/webhooks/deliveries/d1"),
        (lambda: api.webhooks.deliveries.retry("d1"), "POST", "/v1/webhooks/deliveries/d1/retry"),
        (lambda: api.embed.get(), "GET", "/v1/embed"),
        (lambda: api.embed.set(["https://shop.example"]), "PUT", "/v1/embed"),
    ]
    for fn, _, _ in calls:
        fn()
    for i, (_, method, path) in enumerate(calls):
        r = fs.seen[i]
        assert (r["method"], r["url"]) == (method, "https://api.test" + path), path
        assert r["headers"]["X-Signature"] == sign(SECRET, 1758000000, method, path, r["data"] or b""), path
        if method == "POST":
            assert r["headers"]["Idempotency-Key"], path
    assert fs.seen[18]["data"] == b'{"enabled":false}'


def test_addresses_restore_idempotency_fields_and_conflict():
    api, fs, sleeps = make(
        [
            resp(200, {"id": "a1", "status": "active", "total_received": "0", "archived_at": "2026-10-01T10:00:00Z", "archive_reason": "auto", "restored_at": "2026-10-04T10:00:00Z", "restore_reason": "manual"}),
            resp(409, {"error": {"code": "conflict", "message": "customer already has an active address", "details": {"field": "customer_id", "active_address_id": "a2"}}}),
        ]
    )
    a = api.addresses.restore("a1", idempotency_key="restore-a1")
    assert (a.status, a.archive_reason, a.restore_reason, a.restored_at) == ("active", "auto", "manual", "2026-10-04T10:00:00Z")
    assert a.restore_conflict_with is None and a.archive_warned_at is None
    r = fs.seen[0]
    assert (r["method"], r["url"]) == ("POST", "https://api.test/v1/addresses/a1/restore")
    assert r["headers"]["Idempotency-Key"] == "restore-a1"
    assert r["headers"]["X-Signature"] == sign(SECRET, 1758000000, "POST", "/v1/addresses/a1/restore", r["data"] or b"")
    with pytest.raises(ApiError) as ei:
        api.addresses.restore("a1")
    e = ei.value
    assert (e.status, e.code, e.field, e.details["active_address_id"]) == (409, "conflict", "customer_id", "a2")
    assert len(fs.seen) == 2 and sleeps == []
    assert fs.seen[1]["headers"]["Idempotency-Key"] not in ("", "restore-a1")


def test_static_address_archive_shortcuts():
    archived = {"id": "a1", "status": "archived", "total_received": "0", "archived_at": "2026-10-04T10:00:00Z", "archive_reason": "manual"}
    api, fs, _ = make(
        [
            resp(200, archived),
            resp(200, {**archived, "status": "active", "restored_at": "2026-10-04T11:00:00Z", "restore_reason": "manual"}),
            resp(200, {"items": [archived], "next_cursor": "c2"}),
            resp(200, {"items": [archived], "next_cursor": "c2"}),
            resp(200, {"items": [{**archived, "id": "a2"}], "next_cursor": None}),
            resp(409, {"error": {"code": "conflict", "message": "customer already has an active address", "details": {"field": "customer_id", "active_address_id": "a9"}}}),
        ]
    )
    a = api.archive_static_address("a/1", idempotency_key="arch-a1")
    assert (a.status, a.archive_reason) == ("archived", "manual")
    r = fs.seen[0]
    assert (r["method"], r["url"]) == ("POST", "https://api.test/v1/addresses/a%2F1/archive")
    assert r["headers"]["Idempotency-Key"] == "arch-a1" and not r["data"]
    assert r["headers"]["X-Signature"] == sign(SECRET, 1758000000, "POST", "/v1/addresses/a%2F1/archive", b"")

    a = api.restore_static_address("a1")
    assert (a.status, a.restore_reason) == ("active", "manual")
    assert fs.seen[1]["url"] == "https://api.test/v1/addresses/a1/restore"
    assert fs.seen[1]["headers"]["Idempotency-Key"]

    # status=archived is forced, even over a caller's status.
    page = api.list_archived_static_addresses(search="shop", status="active")
    assert [x.id for x in page.items] == ["a1"] and page.next_cursor == "c2"
    g = fs.seen[2]
    assert (g["method"], g["url"]) == ("GET", "https://api.test/v1/addresses?search=shop&status=archived")
    assert "Idempotency-Key" not in g["headers"]
    assert g["headers"]["X-Signature"] == sign(SECRET, 1758000000, "GET", "/v1/addresses?search=shop&status=archived", b"")

    assert [x.id for x in api.iterate_archived_static_addresses(coin_id="c1")] == ["a1", "a2"]
    assert fs.seen[3]["url"] == "https://api.test/v1/addresses?coin_id=c1&status=archived"
    assert fs.seen[4]["url"].startswith("https://api.test/v1/addresses?") and "status=archived" in fs.seen[4]["url"] and "cursor=c2" in fs.seen[4]["url"]
    assert len(fs.seen) == 5

    with pytest.raises(ApiError) as ei:
        api.restore_static_address("a1")
    assert (ei.value.status, ei.value.details["active_address_id"]) == (409, "a9")


def _signed_event(type_, data):
    secret, ts = "whsec_test_static_archive", 1758000000
    body = json.dumps({"event_id": "e-" + type_, "type": type_, "created_at": "2026-10-04T10:00:00Z", "data": data})
    return verify_webhook({"X-Timestamp": str(ts), "X-Signature": webhook_signature(secret, ts, body)}, body, secret, now=lambda: ts)


def test_static_address_webhook_typed_data():
    addr = {"id": "a1", "address": "TAddr", "coin_id": "c1", "coin": "USDT", "network": "tron", "customer_id": "u1", "status": "active", "total_received": "5.5", "tx_count": 1, "created_at": "2026-04-01T00:00:00Z"}
    ev = _signed_event(STATIC_ADDRESS_RESTORED, {"address": addr, "reason": "deposit", "conflict_with": "a2", "deposit": {"chain_transaction_id": "t1", "txid": "ab12", "output_index": 0, "amount": "5.5", "block_height": 77}})
    d = ev.typed_data()
    assert isinstance(d, StaticAddressRestoredData)
    assert (d.reason, d.conflict_with, d.address.customer_id, d.address.coin) == ("deposit", "a2", "u1", "USDT")
    assert d.address.total_received == Decimal("5.5")
    assert (d.deposit.txid, d.deposit.amount, d.deposit.block_height) == ("ab12", Decimal("5.5"), 77)

    d = _signed_event(STATIC_ADDRESS_ARCHIVED, {"address": {**addr, "status": "archived", "archive_reason": "auto"}, "reason": "auto"}).typed_data()
    assert isinstance(d, StaticAddressArchivedData) and d.reason == "auto" and d.address.archive_reason == "auto"

    d = _signed_event(STATIC_ADDRESS_ARCHIVE_WARNING, {"address": addr, "reason": "auto", "archive_after": "2026-10-11T10:00:00Z", "inactive_since": "2026-04-14T10:00:00Z"}).typed_data()
    assert isinstance(d, StaticAddressArchiveWarningData) and (d.archive_after, d.inactive_since) == ("2026-10-11T10:00:00Z", "2026-04-14T10:00:00Z")

    assert _signed_event("invoice.paid", {"invoice": {"id": "i1"}}).typed_data() is None


def test_api_error_no_retry_on_4xx():
    api, fs, sleeps = make([resp(400, {"error": {"code": "validation_error", "message": "amount must be positive", "details": {"field": "amount"}}}, {"X-Request-Id": "req-1"})])
    with pytest.raises(ApiError) as ei:
        api.invoices.create(amount="-1", currency="USD")
    e = ei.value
    assert (e.status, e.code, e.field, e.request_id, e.message) == (400, "validation_error", "amount", "req-1", "amount must be positive")
    assert len(fs.seen) == 1 and sleeps == []
    assert SECRET not in repr(e) and SECRET not in repr(api)


def test_withdraw_pending_approval_is_typed_and_not_retried():
    """A key whose withdraw waits for an owner/admin's approval — a typed 403, not retried."""
    api, fs, sleeps = make([resp(403, {"error": {"code": "withdraw_pending_approval", "message": "waiting", "details": {"required": "withdraw"}}})])
    with pytest.raises(ApiError) as ei:
        api.withdrawals.create(coin_id="c1", amount="0.1", to_address="bc1q")
    e = ei.value
    assert (e.status, e.code, e.withdraw_pending_approval) == (403, WITHDRAW_PENDING_APPROVAL, True)
    assert len(fs.seen) == 1 and sleeps == []


def test_retries_429_retry_after_then_5xx_backoff_same_idempotency_key():
    clock = iter(range(1758000000, 1758000100))
    api, fs, sleeps = make(
        [
            resp(429, {"error": {"code": "rate_limited", "message": "slow"}}, {"Retry-After": "2"}),
            resp(503, {"error": {"code": "unavailable", "message": "later"}}),
            resp(201, {"id": "inv-2"}),
        ],
        now=lambda: next(clock),
        retry_base=0.1,
        retry_max=0.4,
    )
    inv = api.invoices.create(amount="1", currency="USD")
    assert inv.id == "inv-2" and len(fs.seen) == 3
    assert sleeps[0] == 2.0 and 0 <= sleeps[1] <= 0.2
    assert len({r["headers"]["Idempotency-Key"] for r in fs.seen}) == 1
    assert [r["headers"]["X-Timestamp"] for r in fs.seen] == ["1758000000", "1758000001", "1758000002"]
    assert fs.seen[0]["headers"]["X-Signature"] != fs.seen[1]["headers"]["X-Signature"]


def test_gives_up_after_max_retries_and_skips_501_and_long_retry_after():
    api, fs, _ = make([resp(502, {"error": {"code": "unavailable", "message": "bad gateway"}})], max_retries=2, retry_base=0.001)
    with pytest.raises(ApiError) as ei:
        api.balances.list()
    assert ei.value.status == 502 and len(fs.seen) == 3

    api, fs, _ = make([resp(501, {"error": {"code": "not_implemented", "message": "no"}})])
    with pytest.raises(ApiError):
        api.balances.list()
    assert len(fs.seen) == 1

    api, fs, _ = make([resp(429, {"error": {"code": "rate_limited", "message": "no"}}, {"Retry-After": "120"})])
    with pytest.raises(ApiError) as ei:
        api.balances.list()
    assert ei.value.retry_after == 120 and len(fs.seen) == 1

    api, fs, _ = make([resp(429, {"error": {"code": "rate_limited", "message": "no"}})], max_retries=0)
    with pytest.raises(ApiError):
        api.balances.list()
    assert len(fs.seen) == 1


def test_transport_errors_retry_then_network_error():
    def boom():
        raise requests.ConnectionError("dns")

    api, fs, _ = make([boom], max_retries=2, retry_base=0.001)
    with pytest.raises(NetworkError):
        api.balances.list()
    assert len(fs.seen) == 3

    api, fs, _ = make([boom, resp(200, {"balances": []})], retry_base=0.001)
    assert api.balances.list() == []


def test_csv_and_empty_bodies():
    api, fs, _ = make([resp(200, headers={"Content-Type": "text/csv; charset=utf-8"}, text="id,amount\n1,2\n"), resp(204)])
    assert api.transactions.export_csv(kind="deposit") == "id,amount\n1,2\n"
    assert fs.seen[0]["url"] == "https://api.test/v1/transactions?kind=deposit&format=csv"
    assert fs.seen[0]["headers"]["Accept"] == "text/csv"
    assert api.webhooks.endpoints.delete("e1") is None


def test_iterate_follows_cursor():
    api, fs, _ = make([resp(200, {"items": [{"id": "1"}, {"id": "2"}], "next_cursor": "c2"}), resp(200, {"items": [{"id": "3"}], "next_cursor": None})])
    assert [i.id for i in api.invoices.iterate(status="paid")] == ["1", "2", "3"]
    assert fs.seen[0]["url"] == "https://api.test/v1/invoices?status=paid"
    assert fs.seen[1]["url"] == "https://api.test/v1/invoices?status=paid&cursor=c2"


def test_credentials_from_env(monkeypatch):
    monkeypatch.setenv("CRYPTOLIKE_API_KEY_ID", KEY)
    monkeypatch.setenv("CRYPTOLIKE_API_SECRET", SECRET)
    monkeypatch.setenv("CRYPTOLIKE_API_URL", "https://api.env.test/")
    api = Client(session=FakeSession([resp(200, {})]))
    assert api.base_url == "https://api.env.test"
    assert "[redacted]" in repr(api) and SECRET not in repr(api)
    monkeypatch.delenv("CRYPTOLIKE_API_SECRET")
    with pytest.raises(ValueError, match="secret is required"):
        Client(session=FakeSession([resp(200, {})]))
    monkeypatch.delenv("CRYPTOLIKE_API_KEY_ID")
    with pytest.raises(ValueError, match="key_id is required"):
        Client(secret=SECRET)


def test_retry_in_the_same_second_is_signed_afresh():
    # The server takes the replay slot before the handler runs, so a 5xx has already spent it:
    # repeating the same signature would turn a 500 into ``401 auth.replay``. Only sleeping moves
    # this clock, so the backoff alone (<= 10 ms) cannot reach the next second.
    clock = [1758000000.0]

    def _sleep(s):
        sleeps.append(s)
        clock[0] += s

    sleeps = []
    api, fs, _ = make(
        [resp(503, {"error": {"code": "unavailable", "message": "later"}}), resp(200, {"balances": []})],
        now=lambda: clock[0],
        sleep=_sleep,
        retry_base=0.01,
        retry_max=0.01,
    )
    assert api.balances.list() == []
    assert len(fs.seen) == 2
    assert [r["headers"]["X-Timestamp"] for r in fs.seen] == ["1758000000", "1758000001"]
    assert fs.seen[0]["headers"]["X-Signature"] != fs.seen[1]["headers"]["X-Signature"]
    assert fs.seen[1]["headers"]["X-Signature"] == sign(SECRET, 1758000001, "GET", "/v1/balances", b"")
    assert 0 < sleeps[1] <= 1.01


def test_caller_headers_never_replace_the_signed_ones():
    api, fs, _ = make([resp(200, {"balances": []})])
    api.request("GET", "/v1/balances", headers={"Accept": "text/csv", "x-api-key": "ck_live_attacker", "X-Signature": "deadbeef", "x-timestamp": "1"})
    h = fs.seen[0]["headers"]
    assert h["Accept"] == "text/csv"
    assert h["X-API-Key"] == KEY and h["X-Timestamp"] == "1758000000"
    assert h["X-Signature"] == sign(SECRET, 1758000000, "GET", "/v1/balances", b"")
    # No lower-case twin either: requests would send both headers.
    assert sorted(k for k in h if k.lower() in {"x-api-key", "x-signature", "x-timestamp"}) == ["X-API-Key", "X-Signature", "X-Timestamp"]


def test_ids_are_escaped_in_the_path():
    api, fs, _ = make([resp(200, {"id": "x", "events": []})])
    api.invoices.get("x?format=csv&limit=100000")
    api.invoices.cancel("a b/../v1/balances")
    assert fs.seen[0]["url"] == "https://api.test/v1/invoices/x%3Fformat%3Dcsv%26limit%3D100000"
    assert fs.seen[1]["url"] == "https://api.test/v1/invoices/a%20b%2F..%2Fv1%2Fbalances/cancel"
    for r in fs.seen:
        # The signature covers the target exactly as sent — urllib cannot normalise it afterwards.
        assert r["headers"]["X-Signature"] == sign(SECRET, 1758000000, r["method"], r["url"].removeprefix("https://api.test"), b"")


def test_auth_replay_waits_for_next_second_and_resigns():
    clock = iter(range(1758000000, 1758000100))
    api, fs, sleeps = make(
        [resp(401, {"error": {"code": "auth.replay", "message": "this request was already accepted"}}), resp(200, {"balances": []})],
        now=lambda: next(clock),
    )
    assert api.balances.list() == []
    assert len(fs.seen) == 2 and 0 < sleeps[0] <= 1.01
    assert fs.seen[0]["headers"]["X-Signature"] != fs.seen[1]["headers"]["X-Signature"]

    api, fs, _ = make([resp(401, {"error": {"code": "auth.invalid_signature", "message": "no"}})])
    with pytest.raises(ApiError) as ei:
        api.balances.list()
    assert ei.value.code == "auth.invalid_signature" and len(fs.seen) == 1


def test_create_invoice_allowed_coin_ids():
    """The payer chooses among allowed_coin_ids; sent only when given, read back, 422 not retried."""
    api, fs, sleeps = make(
        [
            resp(201, {"id": "inv-2", "status": "new", "amount": "25", "coin_id": None, "allowed_coin_ids": ["c-1", "c-2"]}),
            resp(201, {"id": "inv-3", "status": "new", "amount": "25", "allowed_coin_ids": None}),
            resp(422, {"error": {"code": "no_allowed_coins", "message": "none left", "details": {"field": "allowed_coin_ids"}}}),
            resp(200, {"currencies": [{"id": "c-1", "ticker": "USDT", "invoice_enabled": True}, {"id": "c-9", "ticker": "BTC", "invoice_enabled": False}, {"id": "c-0", "ticker": "LTC"}]}),
        ]
    )
    inv = api.invoices.create(amount="25", currency="USD", allowed_coin_ids=("c-1", "c-2"))
    assert fs.seen[0]["data"] == b'{"amount":"25","currency":"USD","allowed_coin_ids":["c-1","c-2"]}'
    assert inv.allowed_coin_ids == ["c-1", "c-2"] and inv.coin_id is None

    inv = api.invoices.create(amount="25", currency="USD")
    assert b"allowed_coin_ids" not in fs.seen[1]["data"]
    assert inv.allowed_coin_ids is None

    with pytest.raises(ApiError) as e:
        api.invoices.create(amount="25", currency="BTC", allowed_coin_ids=["c-1"])
    assert e.value.status == 422 and e.value.code == "no_allowed_coins" and e.value.field == "allowed_coin_ids"
    assert len(fs.seen) == 3 and sleeps == []

    for bad, exc in (([], ValueError), ("c-1", TypeError)):
        with pytest.raises(exc):
            api.invoices.create(amount="25", currency="USD", allowed_coin_ids=bad)
    assert len(fs.seen) == 3, "an empty set never reaches the API (it would read as every coin)"

    cur = api.currencies.list()
    assert [c.invoice_enabled for c in cur] == [True, False, None]
