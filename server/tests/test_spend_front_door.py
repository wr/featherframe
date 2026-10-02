"""On Cloud the front door keeps the spend records (W-938): the server
reserves there, and a front door it cannot reach buys nothing."""
from __future__ import annotations

import itertools
from dataclasses import asdict
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI, Request
from starlette.testclient import TestClient

from featherframe import hosted, spend
from featherframe.db import Database

T0 = datetime(2026, 9, 27, 18, 40)


def fake_door():
    """The routes of hosted/src/household.ts spendRoute, on a MemoryStore."""
    door = FastAPI()
    book = spend.MemoryStore()
    door.state.book, door.state.imported, door.state.snapshots = book, [], 0

    @door.post("/h/spend/reserve")
    async def reserve(request: Request):
        body = await request.json()
        reason = book.reserve(spend.Record(**body["record"]), spend.Rule(**body["rule"]))
        return {"ok": True} if reason is None else {"ok": False, "reason": reason}

    @door.post("/h/spend/settle")
    async def settle(request: Request):
        b = await request.json()
        book.settle(b["id"], b["state"], b["cost_usd"], None)
        return {"ok": True}

    @door.get("/h/spend/snapshot")
    async def snapshot(since: float = 0):
        door.state.snapshots += 1
        s = book.snapshot(since)
        return {"rows": [asdict(r) for r in s.rows], "pause": s.pause, "resumed_at": s.resumed_at}

    @door.post("/h/spend/resume")
    async def resume(request: Request):
        book.resume((await request.json())["now"])
        return {"ok": True}

    @door.post("/h/spend/import")
    async def imp(request: Request):
        rows = (await request.json())["rows"]
        door.state.imported.extend(rows)
        return {"added": len(rows)}

    return door


@pytest.fixture
def link(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    door = fake_door()
    return door, hosted.HostedLink("http://door/h", "k", tmp_path / "data", session=TestClient(door))


def test_a_purchase_is_reserved_and_settled_at_the_door(link):
    door, ln = link
    gate = spend.Gate(spend.FrontDoorStore(ln), now=lambda: T0)
    with gate.purchase("collage", "2026-09-27", model="gpt-image-2.5-sunburst", quality="max") as p:
        assert [r.state for r in door.state.book.snapshot(0).rows] == ["open"]
        p.settle(None, 0.2)
    assert [r.state for r in door.state.book.snapshot(0).rows] == ["settled"]
    assert gate.summary()["usd"] == pytest.approx(0.2)


def test_the_door_refuses_and_the_gate_says_why(link):
    door, ln = link
    gate = spend.Gate(spend.FrontDoorStore(ln), now=lambda: T0)
    with pytest.raises(RuntimeError):
        with gate.purchase("collage", "2026-09-27", model="m"):
            raise RuntimeError("stopped mid-call")
    with pytest.raises(spend.Refused) as e:
        with gate.purchase("collage", "2026-09-27", model="m"):
            pass
    assert e.value.reason == "subject"


def test_an_unreachable_door_buys_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    ln = hosted.HostedLink("http://127.0.0.1:9/h", "k", tmp_path / "data")
    gate = spend.Gate(spend.FrontDoorStore(ln), now=lambda: T0)
    with pytest.raises(spend.Refused) as e:
        with gate.purchase("plate", "tyto-alba", model="m"):
            pass
    assert e.value.reason == "unreachable"


def test_the_servers_own_records_go_to_the_door_once(link, tmp_path):
    door, ln = link
    db = Database(tmp_path / "ff.db")
    local = spend.Gate(spend.LocalStore(db), now=lambda: T0)
    with local.purchase("plate", "tyto-alba", model="m") as p:
        p.settle(None, 0.05)
    spend.FrontDoorStore(ln, local_db=db)
    spend.FrontDoorStore(ln, local_db=db)
    assert [r["subject"] for r in door.state.imported] == ["tyto-alba"]


def test_a_snapshot_is_kept_for_a_few_seconds_and_a_write_drops_it(link):
    """The page polls status() every 30 s; a page left open must not ask the
    front door twice a poll, and a resume must never read back stale."""
    door, ln = link
    clock = [1000.0]
    store = spend.FrontDoorStore(ln, clock=lambda: clock[0])
    store.snapshot(10.0)
    clock[0] += spend.SNAPSHOT_TTL_S - 1
    store.snapshot(10.0)
    assert door.state.snapshots == 1              # the second came from the kept one
    store.snapshot(5.0)
    assert door.state.snapshots == 2              # an earlier `since` wants rows it lacks
    store.resume(T0.timestamp())
    store.snapshot(5.0)
    assert door.state.snapshots == 3              # a resume made the next one fresh
    clock[0] += spend.SNAPSHOT_TTL_S + 1
    store.snapshot(5.0)
    assert door.state.snapshots == 4              # and it goes stale on its own


def test_a_later_since_is_answered_from_the_kept_snapshot(link):
    """`Gate.summary` asks since = now - 30 days, which moves with every call,
    so a kept snapshot serves any `since` at or after its own."""
    door, ln = link
    store = spend.FrontDoorStore(ln, clock=lambda: 1000.0)
    at = T0.timestamp()
    for n, when in enumerate((at, at + 100)):
        rec = spend.Record(id=f"r{n}", at=when, month="2026-09", day="2026-09-27", kind="plate",
                           subject=f"s{n}", auto=False, model="m", quality=None, est_usd=0.07)
        assert store.reserve(rec, spend.Rule(limit_usd=10, runaway_per_hour=None,
                                             window_s=None)) is None
    assert [r.id for r in store.snapshot(at - 10).rows] == ["r0", "r1"]
    assert [r.id for r in store.snapshot(at + 50).rows] == ["r1"]
    assert door.state.snapshots == 1


def test_the_gate_summary_asks_the_door_once_a_poll(link):
    door, ln = link
    seconds = itertools.count()
    gate = spend.Gate(spend.FrontDoorStore(ln), now=lambda: T0 + timedelta(seconds=next(seconds)))
    gate.summary()
    gate.summary()
    assert door.state.snapshots == 1


def test_a_reserve_and_a_settle_drop_the_kept_snapshot(link):
    door, ln = link
    gate = spend.Gate(spend.FrontDoorStore(ln), now=lambda: T0)
    assert gate.summary()["count"] == 0
    assert gate.summary()["count"] == 0
    assert door.state.snapshots == 1
    with gate.purchase("plate", "tyto-alba", model="m") as p:
        assert gate.summary()["count"] == 1       # the reserve dropped it
        p.settle(None, 0.05)
        assert gate.summary()["usd"] == pytest.approx(0.05)   # and so did the settle


def _service(tmp_path, monkeypatch, ln, db):
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    monkeypatch.setenv("FEATHERFRAME_DB", str(tmp_path / "ff.db"))
    monkeypatch.setattr(hosted, "_active", ln)
    from featherframe.service import FeatherframeService
    return FeatherframeService(db)


def test_on_cloud_the_service_buys_through_the_front_door(link, tmp_path, monkeypatch):
    door, ln = link
    svc = _service(tmp_path, monkeypatch, ln, Database(tmp_path / "ff.db"))
    assert hosted.link() is ln
    assert isinstance(svc.spend_gate.store, spend.FrontDoorStore)


def test_a_door_away_at_start_leaves_the_server_on_its_own_records(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    db = Database(tmp_path / "ff.db")
    with spend.Gate(spend.LocalStore(db), now=lambda: T0).purchase(
            "plate", "tyto-alba", model="m") as p:
        p.settle(None, 0.05)
    gone = hosted.HostedLink("http://127.0.0.1:9/h", "k", tmp_path / "data")
    svc = _service(tmp_path, monkeypatch, gone, db)
    assert isinstance(svc.spend_gate.store, spend.LocalStore)


def test_off_cloud_the_service_keeps_its_own_records(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    svc = _service(tmp_path, monkeypatch, None, Database(tmp_path / "ff.db"))
    assert hosted.link() is None
    assert isinstance(svc.spend_gate.store, spend.LocalStore)
