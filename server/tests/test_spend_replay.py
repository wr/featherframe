"""26-28 Sep 2026, replayed (W-938): every server start bought the day's
collage and was stopped before it saved or recorded anything. The records
outlive the starts here as they do in the DB (and at the front door), so the
same failure buys one sheet, and a loop the subject rule would not catch
pauses at the seventh image in an hour."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from featherframe import spend
from featherframe.render.collage import CollageCell
from featherframe.render.genart import GeneratedArtProvider
from tests.test_genart import FakeModel

CELLS = [CollageCell("Blue Jay", "Cyanocitta cristata", 9),
         CollageCell("Carolina Wren", "Thryothorus ludovicianus", 4)]
T0 = datetime(2026, 9, 27, 18, 40)


class Stopped(BaseException):
    """The Container stopped mid-call: no exception handler runs."""


class BillsThenStops(FakeModel):
    name = "gpt-image-2.5-sunburst"
    quality = "max"

    def generate(self, prompt, size, refs):
        self.calls += 1          # OpenAI has the request: it bills
        raise Stopped()


def _start(n, store, model, tmp_path, monkeypatch, **kw):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / f"start{n}"))  # a fresh disk
    gate = spend.Gate(store, limit_usd=lambda: 1000,
                      now=lambda: T0 + timedelta(minutes=5 * n))
    p = GeneratedArtProvider(model, gate=gate, refs=[])
    p._describe = lambda common, sci: ("", True, [], None)
    return p


def test_a_hundred_stopped_starts_buy_one_sheet(tmp_path, monkeypatch):
    store, model = spend.MemoryStore(), BillsThenStops()
    for n in range(100):                                   # 8 h 20 min of starts
        p = _start(n, store, model, tmp_path, monkeypatch)
        try:
            p.day_composite(CELLS, date(2026, 9, 27), interval_s=6 * 3600)
        except Stopped:
            pass
    assert model.calls == 1


def test_a_loop_the_subject_rule_misses_pauses_at_the_seventh(tmp_path, monkeypatch):
    store, model = spend.MemoryStore(), FakeModel()
    for n in range(24):                                    # a new subject every start
        p = _start(n, store, model, tmp_path, monkeypatch)
        p.day_composite(CELLS, date(2026, 9, 1) + timedelta(days=n))
    assert model.calls == 6
    assert store.snapshot(0).pause is not None
