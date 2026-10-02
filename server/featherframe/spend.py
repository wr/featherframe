"""Every paid AI call goes through one gate (W-938).

A purchase is checked, recorded durably, and only then sent to the vendor;
its record is settled when the vendor answers. A record left open (the
process died, the connection dropped, the answer was lost) counts as billed
at its estimate, so no restart can buy the same thing again unseen. From 26
to 28 Sep 2026 one household was billed for about 89 collages and the old
ledger recorded 5: each server start bought the day's sheet again and was
stopped (W-917) before it wrote anything down.

The rules live here, in `decide()`. A store only keeps the records and runs
`decide()` atomically with the insert. The Worker's front door (part 2) runs
a port of it, held to the same cases (tests/fixtures/spend-cases.json).
"""
from __future__ import annotations

import functools
import json
import logging
import re
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterator, Optional

import requests

log = logging.getLogger("featherframe.spend")

#: More automatic collages and re-bought illustrations than this in a rolling
#: hour pauses AI generation until the owner resumes it (`_counts_toward_runaway`).
RUNAWAY_PER_HOUR = 6
#: The image kinds the cost projection counts (`Gate.summary`).
RUNAWAY_KINDS = ("plate", "collage")
#: An open record (maybe billed, never settled) holds its subject this long.
OPEN_HOLD_S = 86400.0
DEFAULT_LIMIT_USD = 10
#: Measured per-image prices for gpt-image (W-859, on gpt-image-2.5).
IMAGE_USD = {"low": 0.034, "medium": 0.039, "high": 0.070, "xhigh": 0.103, "max": 0.194}
COLLAGE_FACTOR = 1.1
#: Any paid model not measured: the highest image price, so a limit holds.
UNMEASURED_USD = 0.21
DESCRIBE_USD = 0.002
WEATHER_USD = 0.012
#: Answers that mean the vendor refused before doing any work.
_REFUSED_CODES = (400, 401, 402, 403, 404, 429)


class Unguarded(RuntimeError):
    """A paid model method was called outside `Gate.purchase`."""


class Refused(Exception):
    """The gate said no and nothing was bought. `reason`: off, paused,
    limit, subject, runaway or unreachable."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class Record:
    id: str
    at: float
    month: str
    day: str
    kind: str
    subject: str
    auto: bool
    model: str
    quality: Optional[str]
    est_usd: float
    cost_usd: Optional[float] = None
    state: str = "open"          # open | settled | released


@dataclass
class Rule:
    limit_usd: float
    runaway_per_hour: Optional[int]
    window_s: Optional[float]    # None: no window for this subject


@dataclass
class Snapshot:
    rows: list = field(default_factory=list)
    pause: Optional[dict] = None
    resumed_at: float = 0.0


def estimate_usd(kind: str, model: Optional[str], quality: Optional[str]) -> float:
    """The price of one call before it is made."""
    m = (model or "").lower()
    if m.startswith("local:") or m == "a1111":
        return 0.0
    if kind == "describe":
        return DESCRIBE_USD
    if kind == "weather":
        return WEATHER_USD
    base = IMAGE_USD.get(quality or "", UNMEASURED_USD) if m.startswith("gpt-image-2.5") \
        else UNMEASURED_USD
    return round(base * (COLLAGE_FACTOR if kind == "collage" else 1.0), 4)


def vendor_refused(exc: BaseException) -> bool:
    """True when the vendor answered no before doing the work, so nothing was
    billed. Anything else may have been."""
    if getattr(exc, "billed", False):
        return False
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return True
    code = getattr(getattr(exc, "response", None), "status_code", None)
    if code is None:
        m = re.match(r"HTTP (\d{3})", str(exc))
        code = int(m.group(1)) if m else None
    return code in _REFUSED_CODES


def _spent(r: Record) -> float:
    if r.state == "released":
        return 0.0
    if r.state == "settled" and r.cost_usd is not None:
        return float(r.cost_usd)
    return float(r.est_usd)


def _counts_toward_runaway(rows: list, r: Record) -> bool:
    """Collages always; an illustration only when it buys a species again
    within a day of buying it (W-938). A first illustration of a new species
    is bounded by the monthly limit, not the pause."""
    if r.kind == "collage":
        return True
    if r.kind != "plate":
        return False
    return any(o.kind == "plate" and o.subject == r.subject and o.state != "released"
               and o.id != r.id and r.at - OPEN_HOLD_S <= o.at < r.at for o in rows)


def decide(rows: list, paused: bool, resumed_at: float, rec: Record, rule: Rule,
           now: float) -> Optional[str]:
    """Whether `rec` may be bought: None, or why not. `rows` holds at least
    this month's records and the last 36 hours'."""
    if paused:
        return "paused"
    month = sum(_spent(r) for r in rows if r.month == rec.month)
    if rec.est_usd > 0 and month + rec.est_usd > rule.limit_usd + 1e-9:
        return "limit"
    if not rec.auto:
        return None
    same = [r for r in rows if r.kind == rec.kind and r.subject == rec.subject
            and r.state != "released"]
    if any(r.state == "open" and now - r.at < OPEN_HOLD_S for r in same):
        return "subject"
    if rule.window_s and any(now - r.at < rule.window_s for r in same):
        return "subject"
    if rule.runaway_per_hour and _counts_toward_runaway(rows, rec):
        since = max(now - 3600.0, resumed_at)
        recent = [r for r in rows if r.auto and r.state != "released" and r.at > since
                  and _counts_toward_runaway(rows, r)]
        if len(recent) >= rule.runaway_per_hour:
            return "runaway"
    return None


# -- the fence ---------------------------------------------------------------
_local = threading.local()


def active() -> Optional[Purchase]:
    stack = getattr(_local, "stack", None)
    return stack[-1] if stack else None


def fenced(fn: Callable) -> Callable:
    """A paid model method: it runs only inside `Gate.purchase`."""
    if getattr(fn, "__fenced__", False):
        return fn

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if active() is None:
            raise Unguarded(f"{fn.__qualname__} called outside spend.Gate.purchase")
        return fn(*args, **kwargs)

    wrapper.__fenced__ = True
    return wrapper


# -- a purchase --------------------------------------------------------------
class Purchase:
    def __init__(self, store, record: Record) -> None:
        self._store = store
        self.record = record
        self.done = False

    def settle(self, usage: Optional[dict] = None, cost_usd: Optional[float] = None) -> None:
        """The vendor answered: record what it cost (its estimate if unknown).
        A failure to write it leaves the record open, which counts it anyway."""
        if self.done:
            return
        self.done = True
        cost = self.record.est_usd if cost_usd is None else float(cost_usd)
        try:
            self._store.settle(self.record.id, "settled", cost, usage)
        except Exception:
            log.warning("could not settle %s %s; it stays counted at its estimate",
                        self.record.kind, self.record.subject, exc_info=True)

    def release(self) -> None:
        """The vendor refused before doing the work: nothing was billed."""
        if self.done:
            return
        self.done = True
        try:
            self._store.settle(self.record.id, "released", 0.0, None)
        except Exception:
            log.warning("could not release %s %s", self.record.kind, self.record.subject,
                        exc_info=True)


class Gate:
    def __init__(self, store, *, enabled: Callable[[], bool] = lambda: True,
                 limit_usd: Callable[[], float] = lambda: DEFAULT_LIMIT_USD,
                 runaway_per_hour: Optional[int] = RUNAWAY_PER_HOUR,
                 now: Callable[[], datetime] = datetime.now) -> None:
        self.store = store
        self._enabled = enabled
        self._limit = limit_usd
        self._runaway = runaway_per_hour
        self._now = now
        self._last_summary: Optional[dict] = None

    @classmethod
    def unlimited(cls) -> Gate:
        """For tests and tools with no owner's money behind them: no limit,
        no runaway pause, records in memory."""
        return cls(MemoryStore(), limit_usd=lambda: float("inf"), runaway_per_hour=None)

    @contextmanager
    def purchase(self, kind: str, subject: str, *, model: str, quality: Optional[str] = None,
                 auto: bool = True, window_s: Optional[float] = None) -> Iterator[Purchase]:
        """Check, record, then let the caller make the one paid call inside.
        Raises Refused before recording anything when the answer is no."""
        if not self._enabled():
            raise Refused("off")
        now = self._now()
        rec = Record(id=uuid.uuid4().hex, at=now.timestamp(), month=now.strftime("%Y-%m"),
                     day=now.strftime("%Y-%m-%d"), kind=kind, subject=subject, auto=auto,
                     model=model or "unknown", quality=quality,
                     est_usd=estimate_usd(kind, model, quality))
        rule = Rule(limit_usd=float(self._limit()), runaway_per_hour=self._runaway,
                    window_s=window_s if auto else None)
        try:
            reason = self.store.reserve(rec, rule)
        except Exception as exc:
            log.warning("spend record for %s %s could not be written: %s", kind, subject, exc)
            raise Refused("unreachable") from exc
        if reason:
            raise Refused(reason)
        p = Purchase(self.store, rec)
        stack = getattr(_local, "stack", None)
        if stack is None:
            stack = _local.stack = []
        stack.append(p)
        try:
            yield p
        except BaseException as exc:
            if not p.done:
                if isinstance(exc, Exception) and vendor_refused(exc):
                    p.release()
                else:
                    log.warning("%s %s may have been billed; held as open", kind, subject)
            raise
        else:
            p.settle()
        finally:
            stack.remove(p)

    def summary(self) -> dict:
        """This month's spend against the limit, the pause, and the last 30
        days' automatic image purchases by kind (for the cost projection).
        Reading never stops the page: a store that cannot answer (the front
        door, on Cloud) gives the last good summary, or an empty one, marked
        `unreachable`. Buying stays strict: `purchase` refuses."""
        now = self._now()
        month = now.strftime("%Y-%m")
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        since = min(month_start, now - timedelta(days=30)).timestamp()
        try:
            snap = self.store.snapshot(since)
        except Exception:
            log.warning("spend records could not be read; showing the last known summary",
                        exc_info=True)
            last = self._last_summary
            if last is not None:
                return {**last, "unreachable": True}
            return {"month": month, "usd": 0.0, "limit": float(self._limit()), "count": 0,
                    "paused": None, "by_kind_30d": {}, "span_days": 0, "unreachable": True}
        cut = (now - timedelta(days=30)).timestamp()
        recent = [r for r in snap.rows if r.auto and r.kind in RUNAWAY_KINDS
                  and r.state != "released" and r.at >= cut]
        first = min((r.at for r in recent), default=None)
        span = 0 if first is None else max(1, min(30, int((now.timestamp() - first) // 86400) + 1))
        out = {
            "month": month,
            "usd": round(sum(_spent(r) for r in snap.rows if r.month == month), 4),
            "limit": float(self._limit()),
            "count": sum(1 for r in snap.rows if r.month == month and r.state != "released"),
            "paused": snap.pause,
            "by_kind_30d": {k: sum(1 for r in recent if r.kind == k) for k in RUNAWAY_KINDS
                            if any(r.kind == k for r in recent)},
            "span_days": span,
            "unreachable": False,
        }
        self._last_summary = out
        return dict(out)

    def resume(self) -> None:
        self.store.resume(self._now().timestamp())


# -- stores ------------------------------------------------------------------
class MemoryStore:
    """Records in memory: tests, and tools with no owner's money behind them."""

    def __init__(self) -> None:
        self._rows: dict = {}
        self._pause: Optional[dict] = None
        self._resumed_at = 0.0
        self._lock = threading.Lock()

    def reserve(self, rec: Record, rule: Rule) -> Optional[str]:
        with self._lock:
            reason = decide(list(self._rows.values()), self._pause is not None,
                            self._resumed_at, rec, rule, rec.at)
            if reason == "runaway":
                self._pause = {"at": rec.at, "count": rule.runaway_per_hour}
            if reason is None:
                self._rows[rec.id] = rec
            return reason

    def settle(self, rec_id: str, state: str, cost_usd: Optional[float],
               usage: Optional[dict]) -> None:
        with self._lock:
            r = self._rows.get(rec_id)
            if r is not None:
                r.state, r.cost_usd = state, cost_usd

    def snapshot(self, since: float) -> Snapshot:
        with self._lock:
            rows = [Record(**vars(r)) for r in self._rows.values() if r.at >= since]
            return Snapshot(rows=rows, pause=self._pause, resumed_at=self._resumed_at)

    def resume(self, now: float) -> None:
        with self._lock:
            self._pause = None
            self._resumed_at = now


_PAUSE_KEY = "ai_pause"
_RESUMED_KEY = "ai_resumed_at"
_IMPORTED_KEY = "spend_ledger_imported"
#: The longest window `decide()` reads back (the nightly collage's).
_LOOKBACK_S = 36 * 3600.0


class LocalStore:
    """The records in our own SQLite (`db.Database`). On Cloud
    `FrontDoorStore` keeps the count, and this store's rows are imported
    there once."""

    def __init__(self, db, ledger_path: Optional[Path] = None) -> None:
        self._db = db
        if not db.get(_IMPORTED_KEY):
            if ledger_path is None:
                from . import paths
                ledger_path = paths.spend_ledger_path()
            self._import_ledger(Path(ledger_path))

    def reserve(self, rec: Record, rule: Rule) -> Optional[str]:
        def check(rows: list) -> Optional[str]:
            reason = decide([Record(**r) for r in rows], self._db.get(_PAUSE_KEY) is not None,
                            float(self._db.get(_RESUMED_KEY) or 0.0), rec, rule, rec.at)
            if reason == "runaway":
                self._db.set(_PAUSE_KEY, {"at": rec.at, "count": rule.runaway_per_hour})
            return reason
        return self._db.spend_reserve(asdict(rec), rec.at - _LOOKBACK_S, check)

    def settle(self, rec_id: str, state: str, cost_usd: Optional[float],
               usage: Optional[dict]) -> None:
        self._db.spend_settle(rec_id, state, cost_usd, usage, time.time())

    def snapshot(self, since: float) -> Snapshot:
        rows = [Record(**r) for r in self._db.spend_rows(since)]
        return Snapshot(rows=rows, pause=self._db.get(_PAUSE_KEY),
                        resumed_at=float(self._db.get(_RESUMED_KEY) or 0.0))

    def resume(self, now: float) -> None:
        # The resume first: a reserve between the two writes must not count
        # the hour that tripped the pause.
        self._db.set(_RESUMED_KEY, now)
        self._db.set(_PAUSE_KEY, None)

    def _import_ledger(self, path: Path) -> None:
        """The W-859 ledger (`spend.jsonl`), carried over once as settled
        records so this month's spend counts toward the limit."""
        try:
            lines = path.read_text().splitlines()
        except (OSError, ValueError):
            lines = []
        rows = []
        for n, raw in enumerate(lines):
            try:
                e = json.loads(raw)
                at = datetime.fromisoformat(str(e["at"])).astimezone()
                kind = str(e.get("kind") or "plate")
                est = estimate_usd(kind, e.get("model"), e.get("quality"))
                cost = e.get("cost_usd")
                row = asdict(Record(id=f"ledger-{n}", at=at.timestamp(),
                                    month=at.strftime("%Y-%m"), day=at.strftime("%Y-%m-%d"),
                                    kind=kind, subject=str(e.get("subject") or ""), auto=True,
                                    model=str(e.get("model") or "unknown"),
                                    quality=e.get("quality"), est_usd=est,
                                    cost_usd=float(cost) if isinstance(cost, (int, float)) else est,
                                    state="settled"))
            except Exception:  # noqa: BLE001 — one odd line never stops the import
                continue
            rows.append(row)
        if rows:
            self._db.spend_import(rows, _IMPORTED_KEY)


#: A reservation waits this long for the front door, then the gate refuses.
FRONT_DOOR_TIMEOUT_S = 10
#: A snapshot is kept this long: the page polls `Gate.summary` every 30 s and
#: a tab left open must not ask the front door twice a poll.
SNAPSHOT_TTL_S = 15.0
_AT_DOOR_KEY = "spend_rows_at_door"


class FrontDoorStore:
    """The records at the household's front door (W-938, part 2): they
    outlive a Container stopped mid-call. The front door runs `decide()`'s
    port with the insert and adds a backstop of its own. Anything it cannot
    answer raises, and the gate turns that into `Refused("unreachable")`:
    nothing is bought on a front door that cannot be reached, a start that
    finds it away included, so building the store never touches the network."""

    def __init__(self, link, local_db=None, clock: Callable[[], float] = time.monotonic) -> None:
        self._link = link
        self._clock = clock
        self._local_db = local_db
        self._imported = local_db is None    # the server's own rows are at the door
        self._import_lock = threading.Lock()
        self._kept: Optional[tuple] = None   # (when, since, Snapshot)
        self._writes = 0                     # bumped by every write
        self._kept_lock = threading.Lock()

    def _import_local(self) -> None:
        """The server's own records, from before the front door kept them,
        go over once, ahead of the first reservation: its count must include
        them. A failure raises, and the next reservation tries again."""
        if self._imported:
            return
        with self._import_lock:
            if self._imported:
                return
            db = self._local_db
            if not db.get(_AT_DOOR_KEY):
                rows = db.spend_rows(0.0)
                if rows:
                    r = self._link.http.post(self._link._url("spend/import"), json={"rows": rows},
                                             timeout=FRONT_DOOR_TIMEOUT_S)
                    r.raise_for_status()
                db.set(_AT_DOOR_KEY, True)
            self._imported = True

    def _post(self, op: str, body: dict) -> dict:
        try:
            r = self._link.http.post(self._link._url(f"spend/{op}"), json=body,
                                     timeout=FRONT_DOOR_TIMEOUT_S)
            r.raise_for_status()
            return r.json()
        finally:
            with self._kept_lock:            # even a failed write may have landed
                self._kept, self._writes = None, self._writes + 1

    def reserve(self, rec: Record, rule: Rule) -> Optional[str]:
        self._import_local()
        body = self._post("reserve", {"record": asdict(rec), "rule": asdict(rule)})
        return None if body.get("ok") is True else str(body.get("reason") or "unreachable")

    def settle(self, rec_id: str, state: str, cost_usd: Optional[float],
               usage: Optional[dict]) -> None:
        self._post("settle", {"id": rec_id, "state": state, "cost_usd": cost_usd})

    def snapshot(self, since: float) -> Snapshot:
        now = self._clock()
        with self._kept_lock:
            kept, writes = self._kept, self._writes
        # `Gate.summary` asks since = now - 30 days, which moves with every
        # call: a kept snapshot serves any `since` at or after its own.
        if kept is not None and now - kept[0] < SNAPSHOT_TTL_S and since >= kept[1]:
            snap = kept[2]
            if since == kept[1]:
                return snap
            return Snapshot(rows=[r for r in snap.rows if r.at >= since],
                            pause=snap.pause, resumed_at=snap.resumed_at)
        r = self._link.http.get(self._link._url("spend/snapshot"), params={"since": since},
                                timeout=FRONT_DOOR_TIMEOUT_S)
        r.raise_for_status()
        body = r.json()
        snap = Snapshot(rows=[Record(**x) for x in body.get("rows") or []],
                        pause=body.get("pause"), resumed_at=float(body.get("resumed_at") or 0.0))
        with self._kept_lock:
            if self._writes == writes:       # not if a write landed while it was read
                self._kept = (now, since, snap)
        return snap

    def resume(self, now: float) -> None:
        self._post("resume", {"now": now})
