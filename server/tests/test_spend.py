"""The gate in front of every paid call (W-938): the rule, the estimates,
and a purchase's life from check to settle."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
import requests

from featherframe import spend
from featherframe.render.genart import GenerationError

CASES = json.loads((Path(__file__).parent / "fixtures" / "spend-cases.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_decide(case):
    rows = [spend.Record(**r) for r in case["rows"]]
    got = spend.decide(rows, case["paused"], case["resumed_at"],
                       spend.Record(**case["rec"]), spend.Rule(**case["rule"]), case["now"])
    assert got == case["expect"]


@pytest.mark.parametrize("kind, model, quality, usd", [
    ("plate", "gpt-image-2.5-sunburst", "medium", 0.039),
    ("plate", "gpt-image-2.5-sunburst", "max", 0.194),
    ("collage", "gpt-image-2.5-sunburst", "max", 0.2134),
    ("plate", "gpt-image-2", "high", 0.070),
    ("plate", "gpt-image-2.5-sunburst", "auto", 0.21),
    ("plate", "gemini-2.5-flash-image", None, 0.21),
    ("collage", "black-forest-labs/flux-kontext-pro", None, 0.231),
    ("plate", "a1111", None, 0.0),
    ("describe", "local:llama3", None, 0.0),
    ("describe", "gpt-5.6-luna", None, 0.002),
    ("weather", "gpt-5.6-luna", None, 0.012),
])
def test_estimates(kind, model, quality, usd):
    assert spend.estimate_usd(kind, model, quality) == pytest.approx(usd)


class _Resp:
    def __init__(self, code):
        self.status_code = code


@pytest.mark.parametrize("exc, refused", [
    (GenerationError("HTTP 400: bad size"), True),
    (GenerationError('HTTP 429: {"error": {"code": "insufficient_quota"}}'), True),
    (GenerationError("HTTP 401: invalid_api_key"), True),
    (requests.HTTPError("x", response=_Resp(402)), True),
    (requests.exceptions.ConnectTimeout("no route"), True),
    (GenerationError("HTTP 500: upstream"), False),
    (requests.exceptions.ReadTimeout("slow"), False),
    (requests.exceptions.ConnectionError("reset"), False),
    (RuntimeError("boom"), False),
])
def test_vendor_refused(exc, refused):
    assert spend.vendor_refused(exc) is refused


T0 = datetime(2026, 9, 27, 18, 40)


def _gate(store=None, **kw):
    clock = kw.pop("clock", lambda: T0)
    return spend.Gate(store or spend.MemoryStore(), now=clock, **kw)


def test_a_purchase_is_recorded_before_the_call_and_settled_after():
    gate = _gate()
    seen = []
    with gate.purchase("plate", "tyto-alba", model="gpt-image-2.5-sunburst",
                       quality="medium") as p:
        seen = gate.store.snapshot(0).rows
        p.settle({"output_tokens": 10}, 0.05)
    assert [r.state for r in seen] == ["open"]
    rows = gate.store.snapshot(0).rows
    assert rows[0].state == "settled" and rows[0].cost_usd == 0.05
    assert rows[0].month == "2026-09" and rows[0].day == "2026-09-27"


def test_an_unsettled_success_is_settled_at_its_estimate():
    gate = _gate()
    with gate.purchase("plate", "tyto-alba", model="gpt-image-2.5-sunburst", quality="max"):
        pass
    r = gate.store.snapshot(0).rows[0]
    assert r.state == "settled" and r.cost_usd == pytest.approx(0.194)


def test_a_vendor_refusal_is_released_and_anything_else_stays_open():
    gate = _gate()
    with pytest.raises(GenerationError):
        with gate.purchase("plate", "a", model="m"):
            raise GenerationError("HTTP 429: insufficient_quota")
    with pytest.raises(RuntimeError):
        with gate.purchase("plate", "b", model="m"):
            raise RuntimeError("connection reset mid-answer")
    states = {r.subject: r.state for r in gate.store.snapshot(0).rows}
    assert states == {"a": "released", "b": "open"}


def test_off_refuses_before_anything_is_recorded():
    gate = _gate(enabled=lambda: False)
    with pytest.raises(spend.Refused) as e:
        with gate.purchase("plate", "a", model="m"):
            pass
    assert e.value.reason == "off"
    assert gate.store.snapshot(0).rows == []


def test_a_store_that_cannot_record_refuses():
    class Down(spend.MemoryStore):
        def reserve(self, rec, rule):
            raise OSError("disk full")
    with pytest.raises(spend.Refused) as e:
        with _gate(Down()).purchase("plate", "a", model="m"):
            pass
    assert e.value.reason == "unreachable"


def test_the_runaway_trips_the_pause_and_resume_clears_it():
    times = iter(datetime(2026, 9, 27, 18, m) for m in range(0, 60, 5))
    gate = _gate(clock=lambda: next(times), limit_usd=lambda: 100)
    bought = 0
    for n in range(8):
        try:
            with gate.purchase("plate", f"s{n}", model="m"):
                bought += 1
        except spend.Refused as r:
            assert r.reason in ("runaway", "paused")
    assert bought == 6
    s = gate.summary()
    assert s["paused"]["count"] == 6
    gate.resume()
    assert gate.summary()["paused"] is None
    with gate.purchase("plate", "s9", model="m"):
        pass


def test_summary_adds_up_the_month_and_the_last_thirty_days():
    gate = _gate(limit_usd=lambda: 10)
    with gate.purchase("plate", "a", model="gpt-image-2.5-sunburst", quality="medium") as p:
        p.settle(None, 0.05)
    with gate.purchase("collage", "2026-09-27", model="gpt-image-2.5-sunburst", quality="medium") as p:
        p.settle(None, 0.06)
    s = gate.summary()
    assert s["month"] == "2026-09"
    assert s["usd"] == pytest.approx(0.11) and s["limit"] == 10
    assert s["by_kind_30d"] == {"plate": 1, "collage": 1}
    assert s["span_days"] == 1
    assert s["paused"] is None


def test_the_fence_follows_the_purchase():
    calls = []

    @spend.fenced
    def paid():
        calls.append(1)

    with pytest.raises(spend.Unguarded):
        paid()
    with _gate().purchase("plate", "a", model="m"):
        paid()
    assert calls == [1] and spend.active() is None


def test_unlimited_is_for_tests_and_tools():
    gate = spend.Gate.unlimited()
    for n in range(20):
        with gate.purchase("plate", f"s{n}", model="m"):
            pass
    assert len(gate.store.snapshot(0).rows) == 20
