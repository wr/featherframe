"""Each of genart's paid calls goes through the gate (W-938)."""
from __future__ import annotations

from datetime import date, datetime

import pytest

from featherframe import spend
from featherframe.render.collage import CollageCell
from featherframe.render.genart import GenerationError, GeneratedArtProvider
from tests.test_genart import FakeModel, FakeTextModel

CELLS = [CollageCell("Blue Jay", "Cyanocitta cristata", 9),
         CollageCell("Carolina Wren", "Thryothorus ludovicianus", 4)]
DAY = date(2026, 9, 27)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


def _gate(**kw):
    clock = kw.pop("clock", lambda: datetime(2026, 9, 27, 18, 40))
    return spend.Gate(spend.MemoryStore(), now=clock, **kw)


def _kinds(gate):
    return sorted((r.kind, r.subject, r.state) for r in gate.store.snapshot(0).rows)


def _age_out(data_dir, day=DAY):
    """Put the day's sheet past the 3-minute repaint debounce, which would
    otherwise answer a second call before the gate is ever asked."""
    import json
    sidecar = data_dir / "collages" / f"{day.isoformat()}.json"
    meta = json.loads(sidecar.read_text())
    meta["created_ts"] = 0
    sidecar.write_text(json.dumps(meta))


def test_an_illustration_and_its_brief_are_recorded(data_dir):
    gate = _gate()
    p = GeneratedArtProvider(FakeModel(), gate=gate, text_model=FakeTextModel(), refs=[])
    assert p.artwork("Barn Owl", "Tyto alba") is not None
    assert _kinds(gate) == [("describe", "tyto-alba", "settled"),
                            ("plate", "tyto-alba", "settled")]


def test_a_refused_illustration_buys_nothing_and_falls_back(data_dir):
    model = FakeModel()
    p = GeneratedArtProvider(model, gate=_gate(enabled=lambda: False), refs=[])
    assert p.artwork("Barn Owl", "Tyto alba") is None
    assert model.calls == 0


def test_buy_new_off_serves_only_what_is_kept(data_dir):
    model = FakeModel()
    p = GeneratedArtProvider(model, gate=_gate(), refs=[])
    assert p.artwork("Barn Owl", "Tyto alba") is not None
    p.buy_new = False
    assert p.artwork("Barn Owl", "Tyto alba") is not None       # kept, still shown
    assert p.artwork("Veery", "Catharus fuscescens") is None     # never bought
    assert model.calls == 1


def test_a_maybe_billed_failure_holds_the_species_for_a_day(data_dir):
    model = FakeModel(fail=True)                                 # RuntimeError: maybe billed
    gate = _gate()
    p = GeneratedArtProvider(model, gate=gate, refs=[])
    assert p.artwork("Barn Owl", "Tyto alba") is None
    p._failed_at.clear()                                         # past the old 15 min cooldown
    model.fail = False
    assert p.artwork("Barn Owl", "Tyto alba") is None
    assert model.calls == 1
    assert ("plate", "tyto-alba", "open") in _kinds(gate)


def test_a_vendor_refusal_does_not_hold(data_dir):
    class Refuses(FakeModel):
        def generate(self, prompt, size, refs):
            self.calls += 1
            raise GenerationError("HTTP 400: bad request")
    gate = _gate()
    p = GeneratedArtProvider(Refuses(), gate=gate, refs=[])
    assert p.artwork("Barn Owl", "Tyto alba") is None
    assert ("plate", "tyto-alba", "released") in _kinds(gate)


def test_one_automatic_collage_per_interval(data_dir):
    model = FakeModel()
    p = GeneratedArtProvider(model, gate=_gate(), refs=[])
    assert p.day_composite(CELLS, DAY, interval_s=6 * 3600) is not None
    _age_out(data_dir)
    more = CELLS + [CollageCell("House Finch", "Haemorhous mexicanus", 2)]
    # Same interval, new species: the sheet on file is kept, nothing bought.
    art = p.day_composite(more, DAY, interval_s=6 * 3600)
    assert art is not None and model.calls == 1
    # The owner's repaint is not held to the interval.
    p.day_composite(more, DAY, force=True, auto=False, interval_s=6 * 3600)
    assert model.calls == 2


def test_the_nightly_sheet_is_its_own_subject(data_dir):
    model = FakeModel()
    gate = _gate()
    p = GeneratedArtProvider(model, gate=gate, refs=[])
    p.day_composite(CELLS, DAY, interval_s=6 * 3600)
    _age_out(data_dir)
    more = CELLS + [CollageCell("House Finch", "Haemorhous mexicanus", 2)]
    p.day_composite(more, DAY, nightly=True, interval_s=6 * 3600)
    assert model.calls == 2
    assert ("collage", "2026-09-27/nightly", "settled") in _kinds(gate)


def test_a_refused_collage_keeps_the_sheet_on_file(data_dir):
    p = GeneratedArtProvider(FakeModel(), gate=_gate(), refs=[])
    p.day_composite(CELLS, DAY)
    _age_out(data_dir)
    p.gate = _gate(enabled=lambda: False)
    more = CELLS + [CollageCell("House Finch", "Haemorhous mexicanus", 2)]
    art, painted = p.day_composite(more, DAY)
    assert [c.scientific_name for c in painted] == [c.scientific_name for c in CELLS]
