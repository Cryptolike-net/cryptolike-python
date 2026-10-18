import json
from pathlib import Path

import pytest

VECTORS = Path(__file__).resolve().parents[1] / "testdata" / "vectors.json"


@pytest.fixture(scope="session")
def vectors() -> dict:
    """testdata/vectors.json — generated from the platform's own signer (https://cryptolike.net/docs/sdk/vectors.json)."""
    return json.loads(VECTORS.read_text(encoding="utf-8"))
