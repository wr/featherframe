"""The spend records in our own SQLite (W-938): they outlive the process,
the rule runs with the insert, and the old ledger is carried over once."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from featherframe import spend
from featherframe.db import Database

T0 = datetime(2026, 9, 27, 18, 40)


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    # LocalStore looks for the old ledger in the data dir on first use.
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))


def _gate(db, **kw):
    return spend.Gate(spend.LocalStore(db), now=kw.pop("clock", lambda: T0), **kw)


def test_records_outlive_the_process(tmp_path):
    path = tmp_path / "ff.db"
    with pytest.raises(RuntimeError):
        with _gate(Database(path)).purchase("collage", "2026-09-27", model="m"):
            raise RuntimeError("the Container was stopped")
    # A new process, the same DB: the open record holds the day's collage.
    with pytest.raises(spend.Refused) as e:
        with _gate(Database(path)).purchase("collage", "2026-09-27", model="m"):
            pass
    assert e.value.reason == "subject"


def test_settle_and_snapshot(tmp_path):
    db = Database(tmp_path / "ff.db")
    gate = _gate(db)
    with gate.purchase("plate", "tyto-alba", model="gpt-image-2.5-sunburst", quality="medium") as p:
        p.settle({"output_tokens": 5}, 0.041)
    rows = gate.store.snapshot(0).rows
    assert len(rows) == 1 and rows[0].state == "settled" and rows[0].cost_usd == 0.041
    assert rows[0].auto is True and rows[0].quality == "medium"


def test_pause_and_resume_are_kept(tmp_path):
    path = tmp_path / "ff.db"
    times = iter(datetime(2026, 9, 27, 18, m) for m in range(0, 60, 5))
    gate = _gate(Database(path), clock=lambda: next(times), limit_usd=lambda: 100)
    for n in range(7):
        try:
            with gate.purchase("plate", f"s{n}", model="m"):
                pass
        except spend.Refused:
            pass
    again = spend.LocalStore(Database(path)).snapshot(0)
    assert again.pause == {"at": pytest.approx(datetime(2026, 9, 27, 18, 30).timestamp()), "count": 6}
    spend.LocalStore(Database(path)).resume(1.0)
    assert spend.LocalStore(Database(path)).snapshot(0).pause is None


def test_the_old_ledger_is_carried_over_once(tmp_path):
    ledger = tmp_path / "spend.jsonl"
    ledger.write_text("\n".join(json.dumps(e) for e in [
        {"at": "2026-09-27T11:51:21+00:00", "kind": "collage", "subject": "2026-09-27",
         "model": "gpt-image-2.5-sunburst", "quality": "max", "usage": None, "cost_usd": 0.199825},
        {"at": "2026-09-26T01:57:08+00:00", "kind": "describe", "subject": "Green-winged Teal",
         "model": "gpt-5.6-luna", "quality": None, "usage": None, "cost_usd": None},
    ]) + "\nnot json\n")
    db = Database(tmp_path / "ff.db")
    spend.LocalStore(db, ledger_path=ledger)
    spend.LocalStore(db, ledger_path=ledger)          # a second start imports nothing
    rows = spend.LocalStore(db).snapshot(0).rows
    assert sorted(r.kind for r in rows) == ["collage", "describe"]
    assert all(r.state == "settled" for r in rows)
    collage = next(r for r in rows if r.kind == "collage")
    assert collage.cost_usd == pytest.approx(0.199825) and collage.month == "2026-09"
    brief = next(r for r in rows if r.kind == "describe")
    assert brief.cost_usd == pytest.approx(spend.DESCRIBE_USD)


def test_an_interrupted_import_finishes_on_the_next_start(tmp_path):
    ledger = tmp_path / "spend.jsonl"
    ledger.write_text("\n".join(json.dumps(e) for e in [
        {"at": "2026-09-27T11:51:21+00:00", "kind": "collage", "subject": "2026-09-27",
         "model": "gpt-image-2.5-sunburst", "quality": "max", "usage": None, "cost_usd": 0.199825},
        {"at": "2026-09-26T01:57:08+00:00", "kind": "describe", "subject": "Green-winged Teal",
         "model": "gpt-5.6-luna", "quality": None, "usage": None, "cost_usd": None},
    ]))
    db = Database(tmp_path / "ff.db")
    # Simulate an interrupted first start: insert ledger-0 as if it got written
    db._conn.execute(
        "INSERT INTO spend(id, at, month, day, kind, subject, auto, model, quality, est_usd, "
        "cost_usd, state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        ("ledger-0", 1727435481.0, "2026-09", "2026-09-27", "collage", "2026-09-27", True,
         "gpt-image-2.5-sunburst", "max", 0.2138, None, "settled"))
    db._conn.commit()
    # Second start should not raise and should finish the import
    store = spend.LocalStore(db, ledger_path=ledger)
    rows = store.snapshot(0).rows
    assert len(rows) == 2
    assert all(r.state == "settled" for r in rows)
    assert sorted(r.kind for r in rows) == ["collage", "describe"]
    # The flag should be set
    assert db.get(spend._IMPORTED_KEY) is True
    # Third construction should import nothing
    store2 = spend.LocalStore(db, ledger_path=ledger)
    rows2 = store2.snapshot(0).rows
    assert len(rows2) == 2
