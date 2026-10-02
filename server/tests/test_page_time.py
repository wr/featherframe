"""The page's "4 min ago" is the server's _ago, held to the same cases
(W-946): hosted/test/pagetime.test.ts runs the page's own function on them."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from featherframe.service import _ago

CASES = json.loads((Path(__file__).parent / "fixtures" / "page-time-cases.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=[str(c["secs"]) for c in CASES])
def test_ago(case):
    now = datetime(2026, 10, 2, 12, 0, 0)
    assert _ago(now - timedelta(seconds=case["secs"]), now) == case["text"]
