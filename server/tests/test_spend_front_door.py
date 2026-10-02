"""On Cloud the front door keeps the spend records (W-938): the server
reserves there, and a front door it cannot reach buys nothing."""
from __future__ import annotations

import asyncio
import itertools
import json
from dataclasses import asdict
from datetime import datetime, timedelta

import pytest
import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.testclient import TestClient

from featherframe import hosted, spend
from featherframe.db import Database

T0 = datetime(2026, 9, 27, 18, 40)


def fake_door():
    """The routes of hosted/src/household.ts spendRoute, on a MemoryStore."""
    door = FastAPI()
    book = spend.MemoryStore()
    door.state.book, door.state.imported, door.state.snapshots = book, [], 0
    door.state.import_bodies, door.state.import_fails = [], False

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
        if door.state.import_fails:
            return JSONResponse({"error": "down"}, status_code=500)
        body = await request.json()
        rows = body["rows"]
        door.state.imported.extend(rows)
        door.state.import_bodies.append(body)
        added = 0
        for x in rows:                            # the door keeps each record once
            if x["id"] not in book._rows:
                book._rows[x["id"]] = spend.Record(**x)
                added += 1
        # SpendBook.importRows: the later resume, and the server's pause
        # unless the door has one or was resumed since it.
        book._resumed_at = max(book._resumed_at, float(body.get("resumed_at") or 0))
        pause = body.get("pause")
        if pause and book._pause is None and pause["at"] > book._resumed_at:
            book._pause = pause
        return {"added": added}

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


class AnswerLost(spend.MemoryStore):
    """A front door that took the reservation, but whose answer never came back."""

    def reserve(self, rec, rule):
        super().reserve(rec, rule)
        raise requests.ReadTimeout("door slow")


def test_a_reservation_whose_answer_was_lost_is_released():
    """The vendor was never called, so nothing holds the subject or the money."""
    store = AnswerLost()
    gate = spend.Gate(store, now=lambda: T0)
    with pytest.raises(spend.Refused) as e:
        with gate.purchase("plate", "tyto-alba", model="m"):
            raise AssertionError("bought on a reservation the door never answered")
    assert e.value.reason == "unreachable"
    assert [(r.state, r.cost_usd) for r in store.snapshot(0).rows] == [("released", 0.0)]


def _local_record(db):
    local = spend.Gate(spend.LocalStore(db), now=lambda: T0)
    with local.purchase("plate", "tyto-alba", model="m") as p:
        p.settle(None, 0.05)


def test_the_servers_own_records_go_to_the_door_once_a_start(link, tmp_path):
    """Once a process, not once a DB: an older image may have run in between
    (a rollout) and recorded more. The door keeps each record once."""
    door, ln = link
    db = Database(tmp_path / "ff.db")
    _local_record(db)
    first = spend.FrontDoorStore(ln, local_db=db)
    second = spend.FrontDoorStore(ln, local_db=db)
    assert door.state.imported == []              # building a store asks nothing of the door
    rule = spend.Rule(limit_usd=10, runaway_per_hour=None, window_s=None)
    for n, store in enumerate((first, first, second)):
        rec = spend.Record(id=f"r{n}", at=T0.timestamp(), month="2026-09", day="2026-09-27",
                           kind="plate", subject=f"s{n}", auto=False, model="m", quality=None,
                           est_usd=0.07)
        assert store.reserve(rec, rule) is None
    assert [r["subject"] for r in door.state.imported] == ["tyto-alba", "tyto-alba"]
    assert [r.subject for r in door.state.book.snapshot(0).rows].count("tyto-alba") == 1


def test_a_pause_from_before_the_front_door_stays_on(link, tmp_path):
    """Paused on the server's own records: it reaches the door with them, and
    once the owner resumes there a new start does not pause it again."""
    door, ln = link
    db = Database(tmp_path / "ff.db")
    paused_at = T0.timestamp() - 600
    db.set("ai_pause", {"at": paused_at, "count": 6})
    db.set("ai_resumed_at", T0.timestamp() - 7200)
    gate = spend.Gate(spend.FrontDoorStore(ln, local_db=db), now=lambda: T0)
    with pytest.raises(spend.Refused) as e:
        with gate.purchase("collage", "2026-09-27", model="m"):
            raise AssertionError("bought while paused")
    assert e.value.reason == "paused"
    assert [(b["pause"], b["resumed_at"]) for b in door.state.import_bodies] == \
        [({"at": paused_at, "count": 6}, T0.timestamp() - 7200)]
    gate.resume()
    later = spend.Gate(spend.FrontDoorStore(ln, local_db=db), now=lambda: T0 + timedelta(minutes=1))
    with later.purchase("collage", "2026-09-27", model="m") as p:
        p.settle(None, 0.2)


def test_the_month_figure_has_the_servers_own_records_before_any_purchase(link, tmp_path):
    door, ln = link
    db = Database(tmp_path / "ff.db")
    _local_record(db)
    gate = spend.Gate(spend.FrontDoorStore(ln, local_db=db), now=lambda: T0)
    s = gate.summary()
    assert s["unreachable"] is False and s["usd"] == pytest.approx(0.05) and s["count"] == 1
    assert [r["subject"] for r in door.state.imported] == ["tyto-alba"]


def test_a_summary_stands_when_the_import_fails(link, tmp_path):
    door, ln = link
    door.state.import_fails = True
    db = Database(tmp_path / "ff.db")
    _local_record(db)
    ticks = itertools.count(0, 100)               # every read is past the kept one's TTL
    gate = spend.Gate(spend.FrontDoorStore(ln, local_db=db, clock=lambda: next(ticks)),
                      now=lambda: T0)
    assert gate.summary()["unreachable"] is False
    door.state.import_fails = False               # the next read tries it again
    assert gate.summary()["unreachable"] is False
    assert [r["subject"] for r in door.state.imported] == ["tyto-alba"]


def test_a_snapshot_reads_records_with_fields_it_does_not_know(tmp_path):
    """A newer front door may keep more about each record."""
    door = FastAPI()
    row = {**asdict(spend.Record(id="r1", at=T0.timestamp(), month="2026-09", day="2026-09-27",
                                 kind="plate", subject="tyto-alba", auto=True, model="m",
                                 quality=None, est_usd=0.07)), "billed_by": "openai"}

    @door.get("/h/spend/snapshot")
    async def snapshot():
        return {"rows": [row], "pause": None, "resumed_at": 0}

    ln = hosted.HostedLink("http://door/h", "k", tmp_path / "data", session=TestClient(door))
    assert [r.subject for r in spend.FrontDoorStore(ln).snapshot(0.0).rows] == ["tyto-alba"]


class Flaky:
    """A session whose door can be away: every call fails while `down`."""

    def __init__(self, inner) -> None:
        self.inner, self.down, self.headers = inner, False, {}
        self.gets: list = []                      # the timeout of each GET asked

    def post(self, *a, **kw):
        if self.down:
            raise requests.ConnectionError("door away")
        return self.inner.post(*a, **kw)

    def get(self, *a, **kw):
        self.gets.append(kw.get("timeout"))
        if self.down:
            raise requests.ConnectionError("door away")
        return self.inner.get(*a, **kw)


def test_a_door_that_is_away_buys_nothing_and_the_import_waits_for_it(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    door = fake_door()
    session = Flaky(TestClient(door))
    ln = hosted.HostedLink("http://door/h", "k", tmp_path / "data", session=session)
    db = Database(tmp_path / "ff.db")
    _local_record(db)
    session.down = True
    gate = spend.Gate(spend.FrontDoorStore(ln, local_db=db), now=lambda: T0)   # builds fine
    with pytest.raises(spend.Refused) as e:
        with gate.purchase("plate", "strix-varia", model="m"):
            raise AssertionError("bought on a door that was away")
    assert e.value.reason == "unreachable"
    assert door.state.book.snapshot(0).rows == [] and door.state.imported == []
    session.down = False                          # the door is back
    with gate.purchase("plate", "strix-varia", model="m") as p:
        p.settle(None, 0.07)
    assert [r["subject"] for r in door.state.imported] == ["tyto-alba"]
    with gate.purchase("plate", "bubo-virginianus", model="m"):
        pass
    assert len(door.state.imported) == 1          # once


def test_the_door_saying_ok_is_not_enough_unless_it_says_true(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    door = FastAPI()

    @door.post("/h/spend/reserve")
    async def reserve():
        return {"ok": "yes", "reason": "limit"}

    ln = hosted.HostedLink("http://door/h", "k", tmp_path / "data", session=TestClient(door))
    gate = spend.Gate(spend.FrontDoorStore(ln), now=lambda: T0)
    with pytest.raises(spend.Refused) as e:
        with gate.purchase("plate", "tyto-alba", model="m"):
            pass
    assert e.value.reason == "limit"


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


def test_a_door_away_at_start_still_leaves_the_server_on_the_door(tmp_path, monkeypatch):
    """Not on its own records: it starts, and every purchase refuses."""
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    db = Database(tmp_path / "ff.db")
    _local_record(db)
    gone = hosted.HostedLink("http://127.0.0.1:9/h", "k", tmp_path / "data")
    svc = _service(tmp_path, monkeypatch, gone, db)
    assert isinstance(svc.spend_gate.store, spend.FrontDoorStore)
    with pytest.raises(spend.Refused) as e:
        with svc.spend_gate.purchase("plate", "strix-varia", model="m"):
            pass
    assert e.value.reason == "unreachable"


def test_the_old_ledger_reaches_the_door_on_cloud(link, tmp_path, monkeypatch):
    door, ln = link
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "spend.jsonl").write_text(json.dumps(
        {"at": "2026-09-27T11:51:21+00:00", "kind": "collage", "subject": "2026-09-27",
         "model": "gpt-image-2.5-sunburst", "quality": "max", "usage": None,
         "cost_usd": 0.199825}) + "\n")
    svc = _service(tmp_path, monkeypatch, ln, Database(tmp_path / "ff.db"))
    assert door.state.imported == []
    with svc.spend_gate.purchase("plate", "tyto-alba", model="m") as p:
        p.settle(None, 0.05)
    assert [(r["kind"], r["subject"], r["state"]) for r in door.state.imported] == \
        [("collage", "2026-09-27", "settled")]


def test_off_cloud_the_service_keeps_its_own_records(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    svc = _service(tmp_path, monkeypatch, None, Database(tmp_path / "ff.db"))
    assert hosted.link() is None
    assert isinstance(svc.spend_gate.store, spend.LocalStore)


# -- reads soft-fail; buying stays strict -------------------------------------
class DeadStore:
    """A front door that cannot be reached."""

    def reserve(self, rec, rule):
        raise requests.ConnectionError("door away")

    def settle(self, *a):
        raise requests.ConnectionError("door away")

    def snapshot(self, since):
        raise requests.ConnectionError("door away")

    def resume(self, now):
        raise requests.ConnectionError("door away")


def test_a_summary_it_cannot_read_is_the_last_good_one_or_an_empty_one(tmp_path):
    flaky = Flaky(TestClient(fake_door()))
    ln = hosted.HostedLink("http://door/h", "k", tmp_path / "data", session=flaky)
    ticks = itertools.count(0, 100)               # every read is past the kept one's TTL
    gate = spend.Gate(spend.FrontDoorStore(ln, clock=lambda: next(ticks)), now=lambda: T0)
    flaky.down = True
    empty = gate.summary()
    assert empty == {"month": "2026-09", "usd": 0.0, "limit": 10.0, "count": 0, "paused": None,
                     "by_kind_30d": {}, "span_days": 0, "unreachable": True}
    flaky.down = False
    with gate.purchase("plate", "tyto-alba", model="m") as p:
        p.settle(None, 0.05)
    good = gate.summary()
    assert good["unreachable"] is False and good["usd"] == pytest.approx(0.05)
    flaky.down = True
    last = gate.summary()
    assert last["unreachable"] is True and last["usd"] == pytest.approx(0.05) and last["count"] == 1


def test_a_read_that_failed_is_not_asked_again_for_a_while(tmp_path):
    """A door that hangs costs one short wait, not one per poll and per page."""
    flaky = Flaky(TestClient(fake_door()))
    ln = hosted.HostedLink("http://door/h", "k", tmp_path / "data", session=flaky)
    clock = [1000.0]
    gate = spend.Gate(spend.FrontDoorStore(ln, clock=lambda: clock[0]), now=lambda: T0)
    flaky.down = True
    assert gate.summary()["unreachable"] is True
    clock[0] += spend.SNAPSHOT_RETRY_S - 1
    assert gate.summary()["unreachable"] is True
    assert flaky.gets == [spend.SNAPSHOT_TIMEOUT_S]    # one GET, with the read's own short wait
    flaky.down = False
    clock[0] += 2
    assert gate.summary()["unreachable"] is False
    assert len(flaky.gets) == 2


def test_a_write_clears_a_failed_read(tmp_path):
    flaky = Flaky(TestClient(fake_door()))
    ln = hosted.HostedLink("http://door/h", "k", tmp_path / "data", session=flaky)
    store = spend.FrontDoorStore(ln, clock=lambda: 1000.0)
    flaky.down = True
    with pytest.raises(requests.ConnectionError):
        store.snapshot(0.0)
    with pytest.raises(requests.ConnectionError):
        store.snapshot(0.0)
    assert len(flaky.gets) == 1
    flaky.down = False
    store.resume(T0.timestamp())
    assert store.snapshot(0.0).resumed_at == T0.timestamp()
    assert len(flaky.gets) == 2


@pytest.fixture
def dead(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    monkeypatch.setenv("FEATHERFRAME_DB", str(tmp_path / "ff.db"))
    from featherframe.config import Config, save_config
    from featherframe.service import FeatherframeService
    db = Database(tmp_path / "ff.db")
    save_config(db, Config(imagegen_api_key="sk-proj-verysecretkey1234"))
    svc = FeatherframeService(db)
    svc._clock = lambda: datetime(2026, 10, 14, 12, 0)
    svc.spend_gate.store = DeadStore()
    return svc


@pytest.fixture
def client(dead):
    from featherframe.app import app
    app.state.service = dead
    return TestClient(app, raise_server_exceptions=False)


ORIGIN = {"Origin": "http://testserver"}


def test_the_page_and_the_status_stand_without_the_door(dead, client):
    ai = dead.status()["ai"]
    assert ai["summary"] == "Unavailable" and ai["state"] == "warn" and ai["notice"] == "error"
    assert ai["text"] == ("AI generation is unavailable right now, so nothing is bought. "
                          "It resumes on its own.")
    assert client.get("/").status_code == 200
    assert client.get("/api/status").json()["ai"]["summary"] == "Unavailable"


def test_a_render_has_no_footnote_about_a_door_that_is_away(dead):
    assert dead.ai_refusal() == "unreachable"
    dead.db.set("imagegen_error", {"at": "2026-10-14T11:00:00", "reason": "credits"})
    assert dead._imagegen_glass_note() is None
    assert dead._fallback_note() is None
    assert dead.genart._model is not None        # without the rule it would say "Out of OpenAI credits"


def test_a_repaint_says_the_door_is_away(dead, client, monkeypatch):
    monkeypatch.setattr(dead, "generated_listing", lambda: [{"slug": "tyto-alba"}])
    r = client.post("/api/generated/regenerate", data={"slug": "tyto-alba"}, headers=ORIGIN)
    assert r.json() == {"ok": False,
                        "error": "AI generation is unavailable right now. Try again in a minute."}
    dead.config.collage_generated = True
    r = client.post("/api/collage/now", data={"repaint": "1"}, headers=ORIGIN)
    assert r.status_code == 409
    assert r.json() == {"ok": False, "error": "AI generation is unavailable right now"}


def test_the_repaint_checks_ask_the_door_off_the_event_loop(dead, client, monkeypatch):
    """A door that hangs must not stop every other request with it."""
    on_loop = []

    def refusal():
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return "unreachable"

    monkeypatch.setattr(dead, "ai_refusal", refusal)
    monkeypatch.setattr(dead, "generated_listing", lambda: [{"slug": "tyto-alba"}])
    client.post("/api/generated/regenerate", data={"slug": "tyto-alba"}, headers=ORIGIN)
    dead.config.collage_generated = True
    client.post("/api/collage/now", data={"repaint": "1"}, headers=ORIGIN)
    assert on_loop == [False, False]


def test_resume_says_try_again_when_the_door_is_away(client):
    r = client.post("/api/ai/resume", headers=ORIGIN)
    assert r.status_code == 503
    assert r.json() == {"ok": False, "error": "try again in a minute"}
