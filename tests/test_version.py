import re
from pathlib import Path

from cryptolike import USER_AGENT, VERSION


def test_version_matches_pyproject():
    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    m = re.search(r'^version = "([^"]+)"', text, re.M)
    assert m and m.group(1) == VERSION
    assert USER_AGENT == f"cryptolike-sdk-python/{VERSION}"
