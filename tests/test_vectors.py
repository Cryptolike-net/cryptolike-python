import pytest

from cryptolike import WebhookVerificationError, body_sha256, canonical_string, sign, verify_webhook, webhook_signature


def test_api_request_vectors(vectors):
    vs = vectors["api_request"]["vectors"]
    assert len(vs) >= 6
    for v in vs:
        body = v["body"].encode("utf-8")
        assert body_sha256(body) == v["body_sha256"], v["name"]
        assert canonical_string(v["timestamp"], v["method"], v["path_query"], body) == v["canonical_string"], v["name"]
        assert sign(v["secret"], v["timestamp"], v["method"], v["path_query"], body) == v["signature"], v["name"]
        # str body / str timestamp / lower-case method sign the same
        assert sign(v["secret"], str(v["timestamp"]), v["method"].lower(), v["path_query"], v["body"]) == v["signature"], v["name"]


def test_webhook_vectors(vectors):
    vs = vectors["webhook"]["vectors"]
    valid = [v for v in vs if v["valid"]]
    invalid = [v for v in vs if not v["valid"]]
    assert len(valid) >= 5 and len(invalid) >= 4
    for v in valid:
        body = v["body"].encode("utf-8")
        assert webhook_signature(v["secret"], v["timestamp"], body) == v["signature"], v["name"]
        ev = verify_webhook(v["headers"], body, v["secret"], now=lambda v=v: v["timestamp"])
        assert ev.event_id == v["headers"]["X-Event-Id"], v["name"]
        assert ev.type == v["headers"]["X-Event-Type"], v["name"]
        assert ev.raw["event_id"] == ev.event_id
        # lower-case header names (WSGI-style) and a str body
        lower = {k.lower(): val for k, val in v["headers"].items()}
        assert verify_webhook(lower, v["body"], v["secret"], tolerance=None).event_id == ev.event_id, v["name"]
    for v in invalid:
        with pytest.raises(WebhookVerificationError) as ei:
            verify_webhook(v["headers"], v["body"].encode("utf-8"), v["secret"], now=lambda v=v: v["timestamp"])
        assert ei.value.reason == "invalid_signature", v["name"]


def _reason(fn):
    with pytest.raises(WebhookVerificationError) as ei:
        fn()
    return ei.value.reason


def test_webhook_tolerance_and_malformed(vectors):
    v = vectors["webhook"]["vectors"][0]
    body = v["body"].encode("utf-8")
    h = v["headers"]
    ts = v["timestamp"]
    assert _reason(lambda: verify_webhook(h, body, v["secret"], now=lambda: ts + 301)) == "timestamp_out_of_tolerance"
    assert _reason(lambda: verify_webhook(h, body, v["secret"], now=lambda: ts - 301)) == "timestamp_out_of_tolerance"
    assert verify_webhook(h, body, v["secret"], now=lambda: ts + 300).event_id
    assert _reason(lambda: verify_webhook(h, body, v["secret"], tolerance=60, now=lambda: ts + 61)) == "timestamp_out_of_tolerance"
    # wall clock: the 2023 vector is stale
    assert _reason(lambda: verify_webhook(h, body, v["secret"])) == "timestamp_out_of_tolerance"

    without = lambda name: {k: val for k, val in h.items() if k != name}  # noqa: E731
    assert _reason(lambda: verify_webhook(without("X-Signature"), body, v["secret"], tolerance=None)) == "missing_header"
    assert _reason(lambda: verify_webhook(without("X-Timestamp"), body, v["secret"], tolerance=None)) == "missing_header"
    assert _reason(lambda: verify_webhook({**h, "X-Timestamp": "1700000000.5"}, body, v["secret"], tolerance=None)) == "invalid_timestamp"
    assert _reason(lambda: verify_webhook({**h, "X-Timestamp": "-1"}, body, v["secret"], tolerance=None)) == "invalid_timestamp"
    # Unicode digits: str.isdigit() accepts them but int() does not — must be a refusal, not a crash
    for bad in ("\u00b2", "\u00b3", "\u0660", "\u2081", "\u00bd", "1\u00b2"):
        assert _reason(lambda bad=bad: verify_webhook({**h, "X-Timestamp": bad}, body, v["secret"], tolerance=None)) == "invalid_timestamp", bad
        assert _reason(lambda bad=bad: verify_webhook({**h, "X-Timestamp": bad}, body, v["secret"])) == "invalid_timestamp", bad
    assert _reason(lambda: verify_webhook({**h, "X-Signature": "sha256="}, body, v["secret"], tolerance=None)) == "invalid_signature"
    assert _reason(lambda: verify_webhook(h, body, "", tolerance=None)) == "invalid_signature"
    # re-serialised JSON = different bytes
    import json

    pretty = json.dumps(json.loads(v["body"]), indent=2).encode()
    assert _reason(lambda: verify_webhook(h, pretty, v["secret"], tolerance=None)) == "invalid_signature"
    junk = b"[1,2,3]"
    hj = {"X-Timestamp": str(ts), "X-Signature": webhook_signature(v["secret"], ts, junk)}
    assert _reason(lambda: verify_webhook(hj, junk, v["secret"], tolerance=None)) == "invalid_body"
