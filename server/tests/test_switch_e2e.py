"""The front door's switch and the server's replay of it, run together (W-1025).

Every other test holds one half: `test_switching.py` the rule, `test_switch_table.py`
what the server reports, `test_hosted.py` the queue and the marker, and
`hosted/test/switching.test.ts` what the front door does with a table. None of
them holds the two against each other, which is where a batch can be chosen
one way by the front door and another by the server's replay.

`FrontDoor` below is the front door's half in Python, over a REAL service:
`decideAlone` (hosted/src/household.ts: the untaken rows parsed by their path,
`switching.choose` over the server's own table, the frames' rows and the table
rewritten, a marker appended), the queue routes (`ingest/take`, `ingest/ack`)
and `takeState`'s rule for a report that is behind a marker. It has to be kept
as faithful as that: when `decideAlone` changes, so does this.

After every decision the server takes the queue (pushes and markers, in
order) and the test holds that it agrees: no mismatch counted, the plates
picture is the entry's, and what the server then reports for every frame and
viewer is what the front door switched them to.
"""
from __future__ import annotations

import json
import random
import re
from datetime import datetime, timedelta

import pytest

from featherframe import switching
from featherframe.render import pipeline
from featherframe.sources.pushed import PushedSource
from tests._frames import EE02_PANEL, EE03_PANEL, add_kit
from tests.test_drawn import COLOUR, GRAY, NOW, SPECIES, _RefArt

VIEWER = "AA:BB:CC:00:00:01"
TOKEN = "tok"
DAY2 = NOW + timedelta(days=1)
JAY, CARDINAL, DOVE = "Blue Jay", "Northern Cardinal", "Mourning Dove"
CARDINAL_KEY, JAY_KEY = "cardinalis cardinalis", "cyanocitta cristata"


def _kind_of(path: str):
    """hosted/src/switch.ts `kindOf`."""
    m = re.match(r"^/api/ingest/(apprise|birdnet-go)(/|$)", path)
    return None if not m else ("birdnet_go" if m.group(1) == "birdnet-go" else "apprise")


class FrontDoor:
    def __init__(self, svc, kind="birdnet_go"):
        self.svc = svc
        self.kind = kind                    # the household's `source_kind` meta
        self.now = svc._clock()
        self.rows = []                      # the ingest table: seq, path, body, taken
        self.seq = 0
        self.upto = 0                       # what the server has taken, not yet acknowledged
        self.table = None                   # meta `switches`
        self.frames, self.viewers = {}, {}
        self.expect = {}
        self.last_marker = 0
        self.log = []                       # every decision, for a failure to read
        self.report()
        self.reset_expectation()

    # -- time ----------------------------------------------------------------
    def at(self, when: datetime) -> None:
        self.now = when
        self.svc._clock = lambda: when
        self.svc._tick_memo = {}

    # -- what the detectors push ----------------------------------------------
    def push(self, common: str, when: datetime = None, kind: str = None, note=None, days=30) -> int:
        """A push queued as `ingest()` queues it. `kind` is the path's: a
        different one from the household's is a push queued before its source
        changed, which the route would now refuse (409)."""
        when = when or self.now
        kind = kind or self.kind
        sci = SPECIES[common][0]
        if kind == "birdnet_go":
            path = f"/api/ingest/birdnet-go/{TOKEN}"
            body = {"type": "detection", "metadata": {
                "species": common, "scientific_name": sci, "confidence": 0.9, "note_id": note,
                "days_since_first_seen": days,
                "bg_detection_date": when.strftime("%Y-%m-%d"), "bg_detection_time": when.strftime("%H:%M:%S")}}
        else:
            path = f"/api/ingest/apprise/{TOKEN}"
            body = {"title": "x", "message": json.dumps({
                "comname": common, "sciname": sci, "confidence": 0.9,
                "date": when.strftime("%Y-%m-%d"), "time": when.strftime("%H:%M:%S")})}
        return self._append(path, json.dumps(body))

    def _append(self, path: str, body: str) -> int:
        self.seq += 1
        self.rows.append({"seq": self.seq, "path": path, "body": body, "taken": 0})
        return self.seq

    # -- the server takes the queue (ingest/take) --------------------------------
    def take(self) -> None:
        rows = [{k: r[k] for k in ("seq", "path", "body")} for r in self.rows]
        for r in self.rows:
            r["taken"] = 1
        self.upto = max(self.upto, self.svc.apply_queue(rows) if rows else 0)

    # -- the server reports (settle: state, then ack) ------------------------------
    def report(self) -> dict:
        state = self.svc.hosted_state()
        self.take_state(state)
        self.rows = [r for r in self.rows if not (r["taken"] and r["seq"] <= self.upto)]
        self.upto = 0
        return state

    def take_state(self, state: dict) -> None:
        """household.ts `takeState`: a report that has not applied a marker
        yet is behind the front door's last switch, and changes none of it."""
        behind = "applied_seq" in state and any(
            r["path"] == "marker" and r["seq"] > state["applied_seq"] for r in self.rows)
        if behind:
            return
        self.frames = {fid: {k: f[k] for k in ("etag", "file", "push")}
                       for fid, f in state["frames"].items() if f.get("status") == "on"}
        self.viewers = {vid: {"name": v["name"], "file": v["file"]}
                        for vid, v in state["viewers"].items() if v.get("name")}
        sw = state.get("switches")
        self.table = sw if isinstance(sw, dict) and "species" in sw else None

    # -- the three things that happen to a server ------------------------------------
    def wake(self) -> None:
        """What `wake()` runs on the server: take, one tick, report."""
        self.take()
        self.svc.tick()
        self.report()
        self.reset_expectation()

    def page_start(self) -> None:
        """A page opens: the start takes the queue and the server stops again
        with no tick (the lifespan's take, then the stop's final report)."""
        self.take()
        self.report()

    def crash_after_take(self) -> None:
        """A start that took the queue and was lost before it reported: the
        rows stay taken, unacknowledged."""
        self.take()
        self.upto = 0

    def reset_expectation(self) -> None:
        self.expect = {"etag": self.svc.pictures["plates"].etag, "frames": dict(self.frames),
                       "viewers": dict(self.viewers)}

    # -- the alarm: decideAlone ---------------------------------------------------
    def decide(self) -> str:
        t = self.table
        if not t or not (self.now.timestamp() < t["valid_until"]):
            return "wake"
        # Any row the server has taken and not acknowledged: the two sides
        # count differently over it (a crash or a failed report between).
        if any(r["taken"] for r in self.rows):
            return "wake"
        rows = sorted((r for r in self.rows if not r["taken"]), key=lambda r: r["seq"])
        if not rows:
            return "wake"
        queued = []
        for r in rows:
            if r["path"] == "marker":
                queued.append({"marker": True})
                continue
            kind = _kind_of(r["path"])
            if kind is None:
                return "wake"                         # a seed: the server's to apply
            if kind != self.kind:
                queued.append({"push": None})         # the other detector's: dropped
                continue
            try:
                body = json.loads(r["body"])
            except ValueError:
                body = None
            queued.append({"push": switching.parse(kind, body)})
        choice = switching.choose(t, queued)
        if choice == "wake":
            return "wake"
        entry = None if choice == "same" else t["species"][choice]
        marker = {"at": self.now.isoformat(), "choice": choice,
                  "etag": entry["etag"] if entry else None, "upto": rows[-1]["seq"]}
        if entry:
            for fid, f in entry["frames"].items():
                self.frames[fid] = {k: f[k] for k in ("etag", "file", "push")}
            for vid, v in entry["viewers"].items():
                self.viewers[vid] = {"name": v["name"], "file": v["file"]}
            self.table = {**t, "shown": choice, "shown_clean": True}
            self.expect = {"etag": entry["etag"], "frames": dict(self.frames), "viewers": dict(self.viewers)}
        self.last_marker = self._append("marker", json.dumps(marker))
        self.log.append(choice)
        return choice

    # -- the whole of it ------------------------------------------------------------
    def settle(self) -> str:
        """The alarm, then whatever follows it: the front door decides and the
        server catches up at its next wake, or the front door wakes it at once."""
        choice = self.decide()
        if choice == "wake":
            self.wake()
        else:
            self.catch_up()
        return choice

    def catch_up(self) -> None:
        """The server's next start: it takes pushes and markers in order. Held
        to agree before any tick, as a report at the stop would show it."""
        self.take()
        self.agrees()
        self.svc.tick()
        self.report()
        self.agrees()

    def agrees(self) -> None:
        svc = self.svc
        assert not svc.db.get("switch_mismatches", 0), f"the server disagreed after {self.log}"
        assert svc.pictures["plates"].etag == self.expect["etag"], f"another picture after {self.log}"
        state = svc.hosted_state()
        for fid, f in self.expect["frames"].items():
            got = state["frames"][fid]
            assert (got["etag"], got["file"], got["push"]) == (f["etag"], f["file"], f["push"]), fid
        for vid, v in self.expect["viewers"].items():
            assert (state["viewers"][vid]["name"], state["viewers"][vid]["file"]) == (v["name"], v["file"]), vid
        assert state["applied_seq"] >= self.last_marker


# -- a real service, as test_switch_table builds one ---------------------------------
@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    monkeypatch.setitem(SPECIES, DOVE, ("Zenaida macroura", (110, 110, 110), 17))
    from featherframe.service import FeatherframeService
    pipeline.DITHER_OVERRIDE = "none"
    s = FeatherframeService()
    s._clock = lambda: NOW
    s.config.quiet_hours_mode = "off"
    s.config.detection_backend = "birdnet_go"
    s.reload_config = lambda: None
    s.provider = _RefArt()
    s.source = PushedSource("birdnet_go", s.db)
    add_kit(s, GRAY, EE03_PANEL)
    add_kit(s, COLOUR, EE02_PANEL, shows="plates")
    return s


def _minutes(door, n: int) -> datetime:
    when = door.now + timedelta(minutes=n)
    door.at(when)
    return when


def _day_one(svc, viewer=False) -> FrontDoor:
    """Both species drawn on day 1 (the cardinal shown), then day 2 begins with
    a real wake on the cardinal: shown, heard today, and the jay not."""
    if viewer:
        svc.apply_viewer_checkin(VIEWER, {"transport": "trmnl",
                                          "headers": {"Model": "og", "Width": "800", "Height": "480"}},
                                 at=NOW.isoformat())
        svc.answer_frame(VIEWER, "add")
    door = FrontDoor(svc)
    door.push(JAY)
    door.wake()
    door.at(NOW + timedelta(minutes=10))
    door.push(CARDINAL)
    door.wake()
    door.at(DAY2)
    door.push(CARDINAL)
    door.wake()
    assert svc.pictures["plates"].meta["species_key"] == CARDINAL_KEY
    entries = door.table["species"]
    assert set(entries) == {JAY_KEY, CARDINAL_KEY} and door.table["shown_clean"]
    assert entries[JAY_KEY]["first_today"] and not entries[CARDINAL_KEY]["first_today"]
    return door


# -- the cases the final review reproduced ---------------------------------------------
def test_a_push_after_a_page_start_that_took_what_no_tick_has_decided_over(svc):
    """A page start takes the jay and stops with no tick; the next push, the
    cardinal, would be "the shown species again" to a front door that counts
    only what it has not been taken, while the server's replay chooses over the
    jay too."""
    door = _day_one(svc)
    door.push(JAY, _minutes(door, 1))
    door.page_start()
    assert door.table is None                        # the stop reports no table: it holds the jay undecided
    door.push(CARDINAL, _minutes(door, 2))
    assert door.settle() == "wake"
    assert svc.pictures["plates"].meta["species_key"] == JAY_KEY     # first today, over a newer repeat
    assert not svc.db.get("switch_mismatches", 0)


def test_a_push_of_the_other_detector_kind_is_dropped_by_both(svc):
    """Queued before the household's source changed (the route now refuses it
    with a 409): neither side may choose over it."""
    door = _day_one(svc)
    seen = len(svc.source.new_since(0))
    door.push(JAY, _minutes(door, 1), kind="apprise")
    assert door.settle() == "same"
    assert len(svc.source.new_since(0)) == seen      # the server took nothing
    assert svc.pictures["plates"].meta["species_key"] == CARDINAL_KEY
    assert not svc.db.get("switch_mismatches", 0)


# -- the rule's branches, over a real table ------------------------------------------------
def test_a_first_today_species_beats_a_newer_repeat(svc):
    door = _day_one(svc)
    door.push(JAY, _minutes(door, 1))
    door.push(CARDINAL, _minutes(door, 2))
    assert door.settle() == JAY_KEY
    assert svc.pictures["plates"].meta["species_key"] == JAY_KEY


def test_the_shown_species_heard_again_is_the_same(svc):
    door = _day_one(svc)
    shown = svc.pictures["plates"].etag
    door.push(CARDINAL, _minutes(door, 1))
    assert door.settle() == "same"
    assert svc.pictures["plates"].etag == shown


def test_a_species_with_no_entry_wakes_the_server(svc):
    door = _day_one(svc)
    door.push(DOVE, _minutes(door, 1))
    assert door.settle() == "wake"
    assert svc.pictures["plates"].meta["species_key"] == "zenaida macroura"
    # Drawn now, so its next push is the front door's: the shown species again.
    door.push(DOVE, _minutes(door, 6))
    assert door.settle() == "same"


def test_a_viewer_is_switched_with_the_frames(svc):
    door = _day_one(svc, viewer=True)
    assert VIEWER in door.table["species"][JAY_KEY]["viewers"]
    door.push(JAY, _minutes(door, 1))
    assert door.settle() == JAY_KEY
    assert door.viewers[VIEWER]["name"] == svc.hosted_state()["viewers"][VIEWER]["name"]


def test_several_decisions_before_the_server_next_starts(svc):
    """Each marker is replayed over the pushes up to it and no further, in the
    order they were queued: the second batch is chosen after the first's marker."""
    door = _day_one(svc)
    door.push(JAY, _minutes(door, 1))
    assert door.decide() == JAY_KEY
    door.push(CARDINAL, _minutes(door, 6))
    assert door.decide() == CARDINAL_KEY
    door.push(CARDINAL, _minutes(door, 6))
    assert door.decide() == "same"
    door.catch_up()
    assert svc.pictures["plates"].meta["species_key"] == CARDINAL_KEY


def test_rows_taken_and_never_acknowledged_wake_the_server(svc):
    door = _day_one(svc)
    door.push(JAY, _minutes(door, 1))
    door.crash_after_take()
    door.push(CARDINAL, _minutes(door, 2))
    assert door.settle() == "wake"
    assert svc.pictures["plates"].meta["species_key"] == JAY_KEY


def test_a_switch_then_a_page_start_then_another_switch(svc):
    door = _day_one(svc)
    door.push(JAY, _minutes(door, 1))
    assert door.settle() == JAY_KEY
    door.push(CARDINAL, _minutes(door, 6))
    assert door.decide() == CARDINAL_KEY           # decided, and not yet taken by the server
    door.page_start()                              # a page: takes the push and the marker, no tick
    assert svc.pictures["plates"].meta["species_key"] == CARDINAL_KEY
    door.push(JAY, _minutes(door, 6))
    assert door.settle() == JAY_KEY
    assert svc.pictures["plates"].meta["species_key"] == JAY_KEY


# -- scripted batches, a page start between ----------------------------------------------------
STEPS = ("push",) * 5 + ("settle",) * 5 + ("page", "crash", "other", "dove")


@pytest.mark.parametrize("seed", range(10))
def test_scripted_batches_with_page_starts_between_never_disagree(svc, seed):
    rng = random.Random(seed)
    door = _day_one(svc, viewer=bool(seed % 2))
    for _ in range(16):
        step = rng.choice(STEPS)
        if step == "push":
            door.push(rng.choice((JAY, CARDINAL)), _minutes(door, rng.randint(1, 4)))
        elif step == "other":
            door.push(rng.choice((JAY, CARDINAL)), _minutes(door, 1), kind="apprise")
        elif step == "dove":
            door.push(DOVE, _minutes(door, 1))
        elif step == "page":
            door.page_start()
        elif step == "crash":
            door.crash_after_take()
        else:
            _minutes(door, 6)
            door.settle()
        assert not svc.db.get("switch_mismatches", 0), f"seed {seed}: {door.log}"
    door.settle()
    door.wake()                                      # whatever is left, the server draws in a real tick
    assert not svc.db.get("switch_mismatches", 0)
