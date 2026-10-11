# Front door switch (W-1025) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Featherframe Cloud's front door switches the screens to a picture already drawn without starting the server, and the server catches up by taking the front door's queue itself.

**Architecture:** The server reports a switch table in `hosted_state()`. The front door (`hosted/src/household.ts`) runs a port of the server's batch rule over its queued detections when they are the only reason to wake, switches the frame and viewer rows itself, and writes a marker into its `ingest` queue. The server takes that queue (pushes, markers, seeds) at start and at every run, before any report, applying each marker as a plates tick at its own time.

**Tech Stack:** Python 3.9+ (Starlette server, pytest), TypeScript Cloudflare Worker and Durable Objects (vitest), shared JSON fixture.

**Spec:** `docs/superpowers/specs/2026-10-11-front-door-switch-design.md`

## Global Constraints

- Python code runs on 3.9 (CI's floor): `from __future__ import annotations`, no `match`, no runtime `X | Y` unions.
- Every comment that sets a rule names W-1025; comments say why, in plain sentences, matching the surrounding code.
- No owner-facing copy changes. The admin page (Task 7) is the only copy, and it follows `docs/STYLE.md`.
- `server/tests/fixtures/switch-cases.json` is the one statement of the rule; Python and TypeScript both run it.
- The Worker deploys before the server image rolls out: a new front door must work with an old server (it decides nothing without `switches`, and pushes wait in the queue).
- A dev server started for any check runs with `FEATHERFRAME_NO_MDNS=1`.
- Never print the household DB's `config` kv row.
- `make test` (from the repo root) and `cd hosted && npm test` pass at the end of every task.

## Review Focus

1. **The deploy gap.** A new front door with the old server image: the report has no `switches`, so the front door decides nothing and pushes wait in the queue for the new image. Test in Task 5.
2. **A start stopped mid-take.** Rows the server took but never acknowledged are taken again on the next start and applied once. Test in Task 4.
3. **A webapp save while a marker is untaken.** The save's report is behind the front door. It must not move the frames back, and it sets `news` so the next wake catches up. Test in Task 5.
4. **A household with a viewer (TRMNL, tablet) whose view of a species is not drawn.** That species has no entry, so the front door wakes. Test in Task 3.
5. **Midnight or the start of quiet hours passing with a table in hand.** The front door wakes instead of deciding. Tests in Tasks 3 and 5.

---

### Task 1: The rule, in Python, with its fixture

**Files:**
- Create: `server/featherframe/switching.py`
- Create: `server/tests/fixtures/switch-cases.json`
- Create: `server/tests/test_switching.py`
- Modify: `server/featherframe/sources/pushed.py:64-145` (the parsers read identity through `switching.parse`)
- Modify: `server/featherframe/service.py` `_pick_showable` (uses `switching.best`)

**Interfaces:**
- Produces: `switching.parse(kind: str, payload) -> Optional[dict]` with keys `common`, `scientific`, `confidence` (float), `note` (str or None), `first_days` (int or None); `switching.best(ranked: list[tuple[int, T]]) -> Optional[T]` (input newest first); `switching.choose(table: dict, queued: list[dict]) -> str` returning `"wake"`, `"same"` or a scientific-name key. A `queued` item is `{"push": <parse result or None>}` or `{"marker": True}`.

- [ ] **Step 1: Write the fixture.** `server/tests/fixtures/switch-cases.json`:

```json
{
  "parse": [
    {"name": "BirdNET-Go's default body",
     "kind": "birdnet_go",
     "body": {"type": "detection", "metadata": {"species": "American Robin", "scientific_name": "Turdus migratorius",
              "confidence": 0.91, "note_id": 8812, "days_since_first_seen": 267}},
     "expect": {"common": "American Robin", "scientific": "Turdus migratorius", "confidence": 0.91,
                "note": "8812", "first_days": 267}},
    {"name": "a note_id as a string",
     "kind": "birdnet_go",
     "body": {"type": "detection", "metadata": {"species": "Blue Jay", "scientific_name": "Cyanocitta cristata",
              "confidence": "0.8", "note_id": " 77 "}},
     "expect": {"common": "Blue Jay", "scientific": "Cyanocitta cristata", "confidence": 0.8, "note": "77", "first_days": null}},
    {"name": "a warning on the same channel",
     "kind": "birdnet_go", "body": {"type": "warning", "title": "Stream disconnected"}, "expect": null},
    {"name": "BirdNET-Go's Test button",
     "kind": "birdnet_go",
     "body": {"type": "detection", "metadata": {"species": "Testus", "scientific_name": "Testus birdicus", "confidence": 1}},
     "expect": null},
    {"name": "Apprise with text around the JSON",
     "kind": "apprise",
     "body": {"title": "x", "message": "New: {\"comname\": \"Blue Jay\", \"sciname\": \"Cyanocitta cristata\", \"confidence\": 0.75} heard"},
     "expect": {"common": "Blue Jay", "scientific": "Cyanocitta cristata", "confidence": 0.75, "note": null, "first_days": null}},
    {"name": "a confidence out of range is 0",
     "kind": "apprise", "body": {"common": "Blue Jay", "scientific": "Cyanocitta cristata", "confidence": 7},
     "expect": {"common": "Blue Jay", "scientific": "Cyanocitta cristata", "confidence": 0.0, "note": null, "first_days": null}},
    {"name": "a name that is not a string is skipped",
     "kind": "apprise", "body": {"comname": ["x"], "common": "Blue Jay", "sciname": 5},
     "expect": {"common": "Blue Jay", "scientific": "", "confidence": 0.0, "note": null, "first_days": null}},
    {"name": "no name at all",
     "kind": "apprise", "body": {"confidence": 0.9}, "expect": null}
  ],
  "choose": [
    {"name": "an empty batch changes nothing", "table": "base", "queued": [], "expect": "same"},
    {"name": "a species with an entry is switched to",
     "table": "base", "queued": [{"push": {"scientific": "Turdus migratorius", "common": "American Robin", "confidence": 0.9}}],
     "expect": "turdus migratorius"},
    {"name": "a species with no entry wakes the server",
     "table": "base", "queued": [{"push": {"scientific": "Sitta carolinensis", "common": "White-breasted Nuthatch", "confidence": 0.9}}],
     "expect": "wake"},
    {"name": "a species new today wakes the server",
     "table": "base", "queued": [{"push": {"scientific": "Turdus migratorius", "common": "American Robin", "confidence": 0.9, "first_days": 0}}],
     "expect": "wake"},
    {"name": "a blocked species is dropped",
     "table": "base", "queued": [{"push": {"scientific": "Passer domesticus", "common": "House Sparrow", "confidence": 0.9}}],
     "expect": "same"},
    {"name": "below the floor is dropped",
     "table": "floor", "queued": [{"push": {"scientific": "Turdus migratorius", "common": "American Robin", "confidence": 0.5}}],
     "expect": "same"},
    {"name": "first today beats a newer repeat",
     "table": "base", "queued": [
       {"push": {"scientific": "Poecile atricapillus", "common": "Black-capped Chickadee", "confidence": 0.9}},
       {"push": {"scientific": "Turdus migratorius", "common": "American Robin", "confidence": 0.9}}],
     "expect": "poecile atricapillus"},
    {"name": "a species heard twice since the take is a repeat, so the newest wins",
     "table": "base", "queued": [
       {"push": {"scientific": "Poecile atricapillus", "common": "Black-capped Chickadee", "confidence": 0.9}},
       {"marker": true},
       {"push": {"scientific": "Poecile atricapillus", "common": "Black-capped Chickadee", "confidence": 0.9}},
       {"push": {"scientific": "Turdus migratorius", "common": "American Robin", "confidence": 0.9}}],
     "expect": "turdus migratorius"},
    {"name": "only the batch after the last marker is chosen from",
     "table": "base", "queued": [
       {"push": {"scientific": "Turdus migratorius", "common": "American Robin", "confidence": 0.9}},
       {"marker": true}],
     "expect": "same"},
    {"name": "a repeated note_id counts once",
     "table": "base", "queued": [
       {"push": {"scientific": "Poecile atricapillus", "common": "Black-capped Chickadee", "confidence": 0.9, "note": "5"}},
       {"push": {"scientific": "Poecile atricapillus", "common": "Black-capped Chickadee", "confidence": 0.9, "note": "5"}}],
     "expect": "poecile atricapillus"},
    {"name": "a note the server already took is dropped",
     "table": "base", "queued": [{"push": {"scientific": "Turdus migratorius", "common": "American Robin", "confidence": 0.9, "note": "8812"}}],
     "expect": "same"},
    {"name": "a push with only a common name resolves when one entry has it",
     "table": "base", "queued": [{"push": {"scientific": "", "common": "American Robin", "confidence": 0.9}}],
     "expect": "turdus migratorius"},
    {"name": "the species shown, clean, is the same",
     "table": "base", "queued": [{"push": {"scientific": "Haemorhous mexicanus", "common": "House Finch", "confidence": 0.9}}],
     "expect": "same"},
    {"name": "the species shown under a footnote is switched to its own clean sheet",
     "table": "noted", "queued": [{"push": {"scientific": "Haemorhous mexicanus", "common": "House Finch", "confidence": 0.9}}],
     "expect": "haemorhous mexicanus"}
  ],
  "tables": {
    "base": {"valid_until": 9999999999, "shown": "haemorhous mexicanus", "shown_clean": true,
             "blocked": ["house sparrow", "passer domesticus"], "floor": 0.0, "notes": ["8812"],
             "species": {
               "haemorhous mexicanus": {"common": "house finch", "first_today": false, "etag": "e1", "frames": {}, "viewers": {}},
               "turdus migratorius": {"common": "american robin", "first_today": false, "etag": "e2", "frames": {}, "viewers": {}},
               "poecile atricapillus": {"common": "black-capped chickadee", "first_today": true, "etag": "e3", "frames": {}, "viewers": {}}}},
    "floor": {"extends": "base", "floor": 0.7},
    "noted": {"extends": "base", "shown_clean": false}
  }
}
```

- [ ] **Step 2: Write the failing test.** `server/tests/test_switching.py`:

```python
"""The front door's choice (W-1025), held to the fixture the front door's
port (hosted/src/switch.ts) also runs."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from featherframe import switching

CASES = json.loads((Path(__file__).parent / "fixtures" / "switch-cases.json").read_text())


def _table(name: str) -> dict:
    t = dict(CASES["tables"][name])
    base = t.pop("extends", None)
    return {**_table(base), **t} if base else t


@pytest.mark.parametrize("case", CASES["parse"], ids=lambda c: c["name"])
def test_parse(case):
    assert switching.parse(case["kind"], case["body"]) == case["expect"]


@pytest.mark.parametrize("case", CASES["choose"], ids=lambda c: c["name"])
def test_choose(case):
    queued = [{"marker": True} if q.get("marker") else
              {"push": {"note": None, "first_days": None, **q["push"]}} for q in case["queued"]]
    assert switching.choose(_table(case["table"]), queued) == case["expect"]


def test_best_is_the_highest_rank_newest_first():
    assert switching.best([(0, "a"), (1, "b"), (1, "c"), (0, "d")]) == "b"
    assert switching.best([]) is None
```

- [ ] **Step 3: Run it to see it fail.**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_switching.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'featherframe.switching'`.

- [ ] **Step 4: Write `server/featherframe/switching.py`.**

```python
"""The front door's choice (W-1025): what a batch of pushed detections would
switch the screens to, when the server has said ahead what each species would
show (`service.switch_table`). Pure, so the front door's port
(hosted/src/switch.ts) and this are held to one fixture,
tests/fixtures/switch-cases.json. The server's own batch rule
(`_pick_showable`) ranks with `best`, so the two cannot drift apart."""
from __future__ import annotations

import json
from typing import Optional, TypeVar

T = TypeVar("T")

NAME_MAX = 200     # a species name; anything longer is not one
# What BirdNET-Go's *Test* button on a notification channel posts (W-865).
GO_TEST_SPECIES = "testus birdicus"


def name(*candidates) -> str:
    """The first candidate that is a non-blank string, trimmed and bounded.
    A list or object under a name key is not a bird: skipped, never coerced."""
    for v in candidates:
        if isinstance(v, str) and v.strip():
            return v.strip()[:NAME_MAX]
    return ""


def confidence(value) -> float:
    try:
        c = float(value)
    except (TypeError, ValueError):
        return 0.0
    return c if 0.0 <= c <= 1.0 else 0.0


def apprise_body(payload: dict) -> dict:
    """BirdNET-Pi's Apprise notification carries our JSON inside `message`,
    perhaps with text after it: the {...} span, else the top-level fields."""
    msg = payload.get("message")
    if isinstance(msg, str) and "{" in msg and "}" in msg:
        try:
            obj = json.loads(msg[msg.index("{"): msg.rindex("}") + 1])
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
    return payload


def parse(kind: str, payload) -> Optional[dict]:
    """Who a pushed body says was heard, or None for a body that is not a
    detection, the test species, or one that names neither name."""
    if not isinstance(payload, dict):
        return None
    if kind == "birdnet_go":
        md = payload.get("metadata")
        if payload.get("type") != "detection" or not isinstance(md, dict):
            return None
        note = md.get("note_id")
        days = md.get("days_since_first_seen")
        out = {"common": name(md.get("species")), "scientific": name(md.get("scientific_name")),
               "confidence": confidence(md.get("confidence")),
               "note": (str(note).strip()[:40] or None)
               if isinstance(note, (int, str)) and not isinstance(note, bool) else None,
               "first_days": days if isinstance(days, int) and not isinstance(days, bool)
               and 0 <= days < 100000 else None}
    else:
        body = apprise_body(payload)
        out = {"common": name(body.get("comname"), body.get("common"), body.get("commonName")),
               "scientific": name(body.get("sciname"), body.get("scientific"), body.get("scientificName")),
               "confidence": confidence(body.get("confidence")), "note": None, "first_days": None}
    if not (out["common"] or out["scientific"]) or out["scientific"].lower() == GO_TEST_SPECIES:
        return None
    return out


def is_test(kind: str, payload) -> bool:
    """BirdNET-Go's *Test* button (W-865): kept as `test_at`, never a bird."""
    if not isinstance(payload, dict):
        return False
    if kind == "birdnet_go":
        md = payload.get("metadata")
        sci = name(md.get("scientific_name")) if isinstance(md, dict) else ""
    else:
        body = apprise_body(payload)
        sci = name(body.get("sciname"), body.get("scientific"), body.get("scientificName"))
    return sci.lower() == GO_TEST_SPECIES


def best(ranked: list) -> Optional[T]:
    """Of (rank, item) newest first: the highest rank, the newest within it."""
    chosen, top = None, -1
    for rank, item in ranked:
        if rank > top:
            chosen, top = item, rank
    return chosen


def _entry(table: dict, push: dict) -> Optional[str]:
    """The table's key for a push: by its scientific name, as the server keys a
    species; with none, by its common name only if one entry alone has it."""
    species = table.get("species") or {}
    sci = (push.get("scientific") or "").strip().lower()
    if sci:
        return sci if sci in species else None
    common = (push.get("common") or "").strip().lower()
    hits = [k for k, e in species.items() if e.get("common") == common]
    return hits[0] if len(hits) == 1 else None


def choose(table: dict, queued: list) -> str:
    """"wake", "same", or the species key every screen is switched to. `queued`
    is what the server has not yet taken, in order: {"push": parse()} or
    {"marker": True}. Counts run over all of it, the choice over the pushes
    after the last marker (spec: The choice, in one place twice)."""
    blocked = set(table.get("blocked") or [])
    floor = float(table.get("floor") or 0.0)
    seen_notes = set(table.get("notes") or [])
    kept = []                      # (index, key) of every push that counts
    for i, q in enumerate(queued):
        p = q.get("push")
        if q.get("marker") or not p:
            continue
        if p["confidence"] < floor:
            continue
        if p.get("note"):
            if p["note"] in seen_notes:
                continue
            seen_notes.add(p["note"])
        names = {(p.get("common") or "").lower(), (p.get("scientific") or "").lower()} - {""}
        if names & blocked:
            continue
        key = _entry(table, p)
        if key is None or p.get("first_days") == 0:
            return "wake"
        kept.append((i, key))
    last_marker = max((i for i, q in enumerate(queued) if q.get("marker")), default=-1)
    counts: dict = {}
    for _, key in kept:
        counts[key] = counts.get(key, 0) + 1
    species = table.get("species") or {}
    batch = [(1 if species[key].get("first_today") and counts[key] == 1 else 0, key)
             for i, key in reversed(kept) if i > last_marker]
    pick = best(batch)
    if pick is None:
        return "same"
    if pick == table.get("shown") and table.get("shown_clean"):
        return "same"
    return pick
```

- [ ] **Step 5: Run the test to see it pass.**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_switching.py -q`
Expected: PASS.

- [ ] **Step 6: Make the push feed read identity through `switching`.** In `server/featherframe/sources/pushed.py`, delete `_NAME_MAX`, `_GO_TEST_SPECIES`, `_name` and `_confidence`, import `from .. import switching`, and replace the two parsers' identity fields:

```python
def _parse_apprise(payload) -> Optional[dict]:
    """BirdNET-Pi's Apprise notification (see switching.apprise_body)."""
    who = switching.parse("apprise", payload)
    if who is None:
        return None
    body = switching.apprise_body(payload)
    now = datetime.now()
    return {"common": who["common"], "scientific": who["scientific"],
            "confidence": who["confidence"],
            "date": _norm_date(str(body.get("date") or "").strip(), now),
            "time": _norm_time(str(body.get("time") or "").strip(), now)}
```

and in `_parse_birdnet_go`, keep the date, time, `loc` and `first_days` reading, but take `common`, `scientific`, `confidence` and `note` from `switching.parse("birdnet_go", payload)` (return None when it does). `switching.parse` returns None for the test species, so `PushedSource.ingest` records `test_at` before it parses:

```python
    def ingest(self, payload) -> Optional[Detection]:
        """Normalize one pushed body, queue its detection, and return it — or
        None if it carries no usable species (guards 'never a wrong bird')."""
        if switching.is_test(self.kind, payload):
            with self._lock:
                self.test_at = datetime.now().isoformat(timespec="seconds")
                self._persist()
            return None
        item = self._parse(payload)
        if not item or not (item["common"] or item["scientific"]):
            return None
        with self._lock:
            ...   # unchanged from here
```

`grep -n "_name(\|_confidence(\|_GO_TEST_SPECIES\|_NAME_MAX" server/featherframe` must find nothing left. Add to `test_switching.py`:

```python
def test_the_test_button_is_known_before_it_is_parsed():
    body = {"type": "detection", "metadata": {"species": "Testus", "scientific_name": "Testus birdicus"}}
    assert switching.is_test("birdnet_go", body) and switching.parse("birdnet_go", body) is None
    assert not switching.is_test("birdnet_go", {"type": "detection", "metadata": {"scientific_name": "Turdus migratorius"}})
```

- [ ] **Step 7: Make `_pick_showable` rank with `best`.** In `server/featherframe/service.py`, replace the novelty loop in `_pick_showable` (keep the blocked skip, the corroboration gate and `pending` as they are):

```python
        ranked: list = []
        for d in detections:
            if self.config.is_blocked(d.common_name, d.scientific_name):
                continue
            ok, record = self._corroborated(d, now)
            if not ok:
                if pending is None:
                    pending = record
                continue
            if not by_novelty:
                chosen = d
                break
            ranked.append((_NOVELTY_RANK[self._novelty(d, now)], d))
        if by_novelty:
            # One order for the server and the front door's port (W-1025).
            chosen = switching.best(ranked)
```

Add `from . import switching` to the imports.

- [ ] **Step 8: Run the push feed, novelty and switching tests.**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_switching.py tests/test_pushed_source.py tests/test_service.py -q`
Expected: PASS. Then `make test` from the repo root: PASS.

- [ ] **Step 9: Commit.**

```bash
git add server/featherframe/switching.py server/tests/fixtures/switch-cases.json server/tests/test_switching.py server/featherframe/sources/pushed.py server/featherframe/service.py
git commit -m "Featherframe Cloud: the batch rule a front door can run, held to one fixture (W-1025)" -m "Refs: W-1025"
```

---

### Task 2: The rule, ported to the front door

**Files:**
- Create: `hosted/src/switch.ts`
- Create: `hosted/test/switch.test.ts`

**Interfaces:**
- Consumes: `server/tests/fixtures/switch-cases.json` (Task 1).
- Produces: `parse(kind: "apprise" | "birdnet_go", body: string): Pushed | null`; `choose(table: SwitchTable, queued: Queued[]): string` (`"wake"`, `"same"` or a key); types `Pushed`, `Queued`, `SwitchTable`, `SwitchEntry`; `kindOf(path: string): "apprise" | "birdnet_go" | null`; `MARKER = "marker"`.

- [ ] **Step 1: Write the failing test.** `hosted/test/switch.test.ts`:

```ts
// The front door's port of the server's batch rule (W-1025), held to the same
// fixture as featherframe/switching.py.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { choose, kindOf, parse, type Queued, type SwitchTable } from "../src/switch";

const CASES = JSON.parse(readFileSync(new URL("../../server/tests/fixtures/switch-cases.json", import.meta.url), "utf8"));
const table = (name: string): SwitchTable => {
  const { extends: base, ...rest } = CASES.tables[name];
  return base ? { ...table(base), ...rest } : rest;
};

describe("parse", () => {
  for (const c of CASES.parse) {
    it(c.name, () => expect(parse(c.kind, JSON.stringify(c.body))).toEqual(c.expect));
  }
  it("reads nothing from a body that is not JSON", () => expect(parse("birdnet_go", "not json")).toBeNull());
});

describe("choose", () => {
  for (const c of CASES.choose) {
    const queued: Queued[] = c.queued.map((q: { marker?: boolean; push?: object }) =>
      q.marker ? { marker: true } : { push: { note: null, first_days: null, ...q.push } });
    it(c.name, () => expect(choose(table(c.table), queued)).toBe(c.expect));
  }
});

describe("kindOf", () => {
  it("names a push by its path", () => {
    expect(kindOf("/api/ingest/birdnet-go/tok")).toBe("birdnet_go");
    expect(kindOf("/api/ingest/apprise")).toBe("apprise");
    expect(kindOf("/api/hosted/seed")).toBeNull();
  });
});
```

- [ ] **Step 2: Run it to see it fail.**

Run: `cd hosted && npx vitest run test/switch.test.ts`
Expected: FAIL, cannot find `../src/switch`.

- [ ] **Step 3: Write `hosted/src/switch.ts`.**

```ts
// The front door's choice (W-1025): what a batch of pushed detections would
// switch the screens to, when the server has said ahead what each species
// would show. A port of featherframe/switching.py; both run
// server/tests/fixtures/switch-cases.json.

export const MARKER = "marker";
const NAME_MAX = 200;
const GO_TEST_SPECIES = "testus birdicus";

export type Pushed = { common: string; scientific: string; confidence: number;
                       note: string | null; first_days: number | null };
export type Queued = { push: Pushed | null } | { marker: true };
export type SwitchEntry = {
  common: string; first_today: boolean; etag: string;
  frames: Record<string, { etag: string; file: string; push: Record<string, unknown> }>;
  viewers: Record<string, { name: string; file: string }>;
};
export type SwitchTable = {
  valid_until: number; shown: string | null; shown_clean: boolean;
  blocked: string[]; floor: number; notes: string[];
  species: Record<string, SwitchEntry>;
};

const name = (...vs: unknown[]): string => {
  for (const v of vs) if (typeof v === "string" && v.trim()) return v.trim().slice(0, NAME_MAX);
  return "";
};
const confidence = (v: unknown): number => {
  const c = typeof v === "number" ? v : typeof v === "string" && v.trim() !== "" ? Number(v) : NaN;
  return Number.isFinite(c) && c >= 0 && c <= 1 ? c : 0;
};

export function kindOf(path: string): "apprise" | "birdnet_go" | null {
  const m = /^\/api\/ingest\/(apprise|birdnet-go)(\/|$)/.exec(path);
  return m ? (m[1] === "birdnet-go" ? "birdnet_go" : "apprise") : null;
}

export function parse(kind: "apprise" | "birdnet_go", body: string): Pushed | null {
  let o: any;
  try { o = JSON.parse(body); } catch { return null; }
  if (o === null || typeof o !== "object" || Array.isArray(o)) return null;
  let out: Pushed;
  if (kind === "birdnet_go") {
    const md = o.metadata;
    if (o.type !== "detection" || md === null || typeof md !== "object") return null;
    const note = md.note_id;
    const days = md.days_since_first_seen;
    out = { common: name(md.species), scientific: name(md.scientific_name), confidence: confidence(md.confidence),
            note: (typeof note === "number" || typeof note === "string") ? (String(note).trim().slice(0, 40) || null) : null,
            first_days: Number.isInteger(days) && days >= 0 && days < 100000 ? days : null };
  } else {
    let f: any = o;
    if (typeof o.message === "string" && o.message.includes("{") && o.message.includes("}")) {
      try {
        const inner = JSON.parse(o.message.slice(o.message.indexOf("{"), o.message.lastIndexOf("}") + 1));
        if (inner && typeof inner === "object" && !Array.isArray(inner)) f = inner;
      } catch { /* the fields at the top */ }
    }
    out = { common: name(f.comname, f.common, f.commonName), scientific: name(f.sciname, f.scientific, f.scientificName),
            confidence: confidence(f.confidence), note: null, first_days: null };
  }
  if (!(out.common || out.scientific) || out.scientific.toLowerCase() === GO_TEST_SPECIES) return null;
  return out;
}

function entry(t: SwitchTable, p: Pushed): string | null {
  const sci = p.scientific.trim().toLowerCase();
  if (sci) return sci in t.species ? sci : null;
  const common = p.common.trim().toLowerCase();
  const hits = Object.keys(t.species).filter((k) => t.species[k].common === common);
  return hits.length === 1 ? hits[0] : null;
}

/** "wake", "same", or the species every screen is switched to. */
export function choose(t: SwitchTable, queued: Queued[]): string {
  const blocked = new Set(t.blocked);
  const notes = new Set(t.notes);
  const kept: [number, string][] = [];
  for (let i = 0; i < queued.length; i++) {
    const q = queued[i];
    if ("marker" in q || !q.push) continue;
    const p = q.push;
    if (p.confidence < t.floor) continue;
    if (p.note) {
      if (notes.has(p.note)) continue;
      notes.add(p.note);
    }
    if ([p.common, p.scientific].some((n) => n && blocked.has(n.toLowerCase()))) continue;
    const key = entry(t, p);
    if (key === null || p.first_days === 0) return "wake";
    kept.push([i, key]);
  }
  let lastMarker = -1;
  queued.forEach((q, i) => { if ("marker" in q) lastMarker = i; });
  const counts = new Map<string, number>();
  for (const [, k] of kept) counts.set(k, (counts.get(k) || 0) + 1);
  let pick: string | null = null;
  let top = -1;
  for (let j = kept.length - 1; j >= 0; j--) {            // newest first
    const [i, k] = kept[j];
    if (i <= lastMarker) continue;
    const rank = t.species[k].first_today && counts.get(k) === 1 ? 1 : 0;
    if (rank > top) { pick = k; top = rank; }
  }
  if (pick === null || (pick === t.shown && t.shown_clean)) return "same";
  return pick;
}
```

- [ ] **Step 4: Run the test to see it pass.**

Run: `cd hosted && npx vitest run test/switch.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add hosted/src/switch.ts hosted/test/switch.test.ts
git commit -m "Featherframe Cloud: the front door's port of the batch rule, on the same fixture (W-1025)" -m "Refs: W-1025"
```

---

### Task 3: The server reports the switch table

**Files:**
- Modify: `server/featherframe/service.py` (`_render_single` builds its spec through a new `_fresh_spec`; new `_kept_output_for`, `_kept_view_for`, `switch_table`; `hosted_state` adds `"switches"`; `_draw_frame` uses `_kept_output_for` for its lookup)
- Modify: `server/featherframe/sources/pushed.py` (new `PushedSource.heard()` and `notes_since()`)
- Create: `server/tests/test_switch_table.py`

**Interfaces:**
- Consumes: `switching` (Task 1).
- Produces: `FeatherframeService.switch_table(now: Optional[datetime] = None) -> dict` in the spec's shape; `hosted_state()["switches"]`; `PushedSource.heard() -> list[tuple[str, str]]` (common, scientific; newest first, distinct by scientific); `PushedSource.notes_since(seconds: int) -> list[str]`.

- [ ] **Step 1: Write the failing tests.** `server/tests/test_switch_table.py`, reusing the drawn store's stub art (import the helpers only, never the tests):

```python
"""What the server tells its front door it may switch to alone (W-1025)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from featherframe.render import pipeline
from featherframe.sources.pushed import PushedSource
from tests._frames import EE02_PANEL, EE03_PANEL, add_kit
from tests.test_drawn import COLOUR, GRAY, NOW, SPECIES, _RefArt

GO = lambda common, sci, note=None: {"type": "detection", "metadata": {
    "species": common, "scientific_name": sci, "confidence": 0.9, "note_id": note,
    "days_since_first_seen": 30,
    "bg_detection_date": NOW.strftime("%Y-%m-%d"), "bg_detection_time": NOW.strftime("%H:%M:%S")}}


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
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
    add_kit(s, COLOUR, EE02_PANEL)
    return s


def _hear(s, common, note=None):
    s.source.ingest(GO(common, SPECIES[common][0], note))
    s.tick()


def test_a_species_drawn_for_every_frame_has_an_entry(svc):
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    t = svc.switch_table()
    card = t["species"]["cardinalis cardinalis"]
    assert card["common"] == "northern cardinal" and card["first_today"] is False
    assert set(card["frames"]) == {GRAY, COLOUR}
    # Exactly what the frames were served when the cardinal was shown.
    _hear(svc, "Northern Cardinal")
    assert {fid: svc._output_etag(fid) for fid in (GRAY, COLOUR)} == \
        {fid: f["etag"] for fid, f in card["frames"].items()}
    assert t["shown"] == "cyanocitta cristata" and t["shown_clean"] is True


def test_a_species_never_drawn_has_no_entry(svc):
    _hear(svc, "Northern Cardinal")
    assert "cyanocitta cristata" not in svc.switch_table()["species"]


def test_a_frame_missing_its_output_takes_the_entry_away(svc):
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    add_kit(svc, "CC:CC:CC:00:00:04", EE03_PANEL, rotation=270)  # a new frame, nothing drawn for it
    assert "cardinalis cardinalis" not in svc.switch_table()["species"]


def test_a_viewer_without_its_view_takes_the_entry_away(svc):
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    # The report a TRMNL makes, as tests/test_hosted.py's viewer tests build it.
    svc.apply_viewer_checkin("TRMNL1", {"transport": "trmnl", "width": "800", "height": "480", "model": "og"},
                             at=NOW.isoformat())
    svc.answer_frame("TRMNL1", "add")
    assert "cardinalis cardinalis" not in svc.switch_table()["species"]


def test_first_today_is_what_novelty_says_of_a_fresh_detection(svc):
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    # Make the cardinal's detections yesterday's: today it has none.
    svc.source._tally = {}
    assert svc.switch_table()["species"]["cardinalis cardinalis"]["first_today"] is True


def test_nothing_in_quiet_hours_or_without_a_push_feed(svc):
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    svc.in_quiet_hours = lambda now=None: True
    assert svc.switch_table() == {}


def test_valid_until_is_the_sooner_of_midnight_and_quiet_hours(svc):
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    midnight = datetime.combine(NOW.date() + timedelta(days=1), datetime.min.time())
    assert svc.switch_table()["valid_until"] == int(midnight.timestamp())
    svc.config.quiet_hours_mode = "custom"
    svc.config.quiet_hours_start, svc.config.quiet_hours_end = "20:00", "06:00"
    assert svc.switch_table()["valid_until"] == int(NOW.replace(hour=20, minute=0).timestamp())


def test_the_notes_taken_in_the_last_hour_ride_along(svc):
    _hear(svc, "Northern Cardinal", note="41")
    assert "41" in svc.switch_table()["notes"]


def test_the_report_carries_it(svc):
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    assert "cardinalis cardinalis" in svc.hosted_state()["switches"]["species"]
```

Copy the viewer's report from `test_a_viewer_its_owner_paired_is_added_and_its_image_is_drawn_ahead` in `tests/test_hosted.py` if its keys differ from the ones above. Adjust `add_kit`'s keyword for rotation to whatever `tests/_frames.py` takes (read it first); the frame must differ from the others in a setting that changes `okey`.

- [ ] **Step 2: Run them to see them fail.**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_switch_table.py -q`
Expected: FAIL, `AttributeError: ... has no attribute 'switch_table'`.

- [ ] **Step 3: Give the push feed what the table reads.** In `server/featherframe/sources/pushed.py` (`PushedSource`):

```python
    def heard(self) -> list:
        """(common, scientific) of every species in the retained window,
        newest first, once each: what the front door may hear next (W-1025)."""
        with self._lock:
            items = list(self._items)
        seen, out = set(), []
        for i in reversed(items):
            key = (i.get("scientific") or "").strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append((i.get("common") or "", i.get("scientific") or ""))
        return out

    def notes_since(self, seconds: int) -> list:
        """BirdNET-Go note ids taken in the last `seconds`: a push that repeats
        one is the same detection, sent again for another rule (W-1025)."""
        cutoff = datetime.now() - timedelta(seconds=seconds)
        with self._lock:
            items = list(self._items)
        out = []
        for i in items:
            try:
                at = datetime.fromisoformat(f"{i['date']}T{i['time']}")
            except (KeyError, ValueError):
                continue
            if i.get("note") and at >= cutoff:
                out.append(str(i["note"]))
        return out
```

- [ ] **Step 4: One spec for a fresh detection.** In `server/featherframe/service.py`, pull `_render_single`'s `SingleSpec(...)` construction into:

```python
    def _fresh_spec(self, common: str, scientific: str, when: datetime,
                    novelty: str, note: Optional[str], note_kind: Optional[str],
                    fallback_note: Optional[str]) -> SingleSpec:
        """The sheet a detection of this species draws. `_render_single` and
        the switch table (W-1025) both build it here, so the table names the
        sheet a real tick would show. The fallback note is the caller's: it
        asks the spend gate, so the table reads it once, not per species."""
        return SingleSpec(common_name=common, scientific_name=scientific, when=when,
                          first_seen=self._first_seen(scientific), note=note,
                          note_kind=note_kind, first_ever=novelty == "first-ever",
                          fallback_note=fallback_note)
```

and in `_render_single`:

```python
        novelty = self._novelty(det, now)
        note = self._note_text()
        spec = self._fresh_spec(det.common_name, det.scientific_name,
                                det.timestamp if det.timestamp != datetime.min else now,
                                novelty, note, self._note_kind() if note else None,
                                self._fallback_note())
```

- [ ] **Step 5: Name an output and a view without drawing them.** Pull `_draw_frame`'s lookup into `_kept_output_for` and make `_draw_frame` call it:

```python
    def _kept_output_for(self, row: dict, kind: str, etag: str,
                         now: datetime) -> Optional[dict]:
        """{"etag", "file", "src"} of this frame's output of picture `etag`
        when it is already drawn under the current Refresh, else None. Names,
        never draws: `_draw_frame`'s lookup, for the switch table (W-1025)."""
        cfg = self.frame_config(row)
        source = next(iter(pictures_mod.sheets_for(etag, cfg.panel_spec.color)), None)
        if source is None:
            return None
        src = self._src_of(kind, etag, source, cfg)
        key = drawn.okey(src)
        fff, png = drawn.out_paths(etag, key)
        rec = (self._drawn.get("outs") or {}).get(key) or {}
        if rec.get("f") != self._drawn_token(etag):
            return None
        out = self._kept_output(fff, png, rec)
        return None if out is None else {"etag": out, "file": drawn.rel(fff), "src": src}
```

`_draw_frame` stays as it is: its early return must stay cheap, and both name an output through the same helpers (`_src_of`, `drawn.okey`, `drawn.out_paths`, `_drawn_token`, `_kept_output`), so they cannot disagree. The table's test that the entry's ETags equal what the frames were served holds them together.

```python
    def _kept_view_for(self, row: dict, etag: str) -> Optional[dict]:
        """{"name", "file"} of this viewer's view of picture `etag`, already
        drawn, else None (W-1025)."""
        view = viewers_mod.view_of(row)
        source = next(iter(pictures_mod.sheets_for(etag, view.fmt == "color")), None)
        if source is None:
            return None
        name = drawn.view_name(source, view.key)
        if not self._view_kept(etag, name):
            return None
        return {"name": name, "file": drawn.rel(drawn.view_path(name))}
```

- [ ] **Step 6: Build the table.** Add to `FeatherframeService` (constants at the top with the others: `SWITCH_NOTES_S = 3600`):

```python
    def switch_table(self, now: Optional[datetime] = None) -> dict:
        """What the front door may switch every screen to by itself when a
        species is heard next (W-1025), or {} when it must wake this server.
        Only a sheet already kept, drawn for every screen that would show it,
        for a species not new today: anything else composes, buys or gates."""
        now = now or self._clock()
        src = self.source
        if (getattr(src, "kind", None) not in ("apprise", "birdnet_go")
                or self.in_quiet_hours(now) or PLATES not in self._kinds_shown(now)):
            return {}
        # Read as a tick reads, and never beside one: today's counts fresh, the
        # pictures still. Callers (a report) hold no tick lock.
        with self._tick_lock:
            self._tick_memo = {"now": now}
            return self._switch_table(now, src)

    def _switch_table(self, now: datetime, src) -> dict:
        fallback = self._fallback_note()
        kits = [r for r in self._kits_on()
                if self._kind_for(frames_mod.shows_of(r), now) == PLATES]
        viewers = [r for r in self.frames.by_transport(*viewers_mod.KINDS)
                   if r.get("status") == frames_mod.ON
                   and self._kind_for(viewers_mod.shows_of(r), now) == PLATES]
        color = any(self.frame_config(r).panel_spec.color for r in kits) or \
            any(viewers_mod.view_of(r).fmt == "color" for r in viewers)
        species = {}
        for common, scientific in src.heard():
            key = scientific.strip().lower()
            if not key or self.config.is_blocked(common, scientific) \
                    or self._is_new_species(scientific, now.date()):
                continue
            det = Detection(rowid=0, date=now.strftime("%Y-%m-%d"), time=now.strftime("%H:%M:%S"),
                            common_name=common, scientific_name=scientific, confidence=1.0)
            first_today = self._novelty(det, now) == "first-today" \
                and (self._today_counts(now) or {}).get(det.key, 0) == 0
            spec = self._fresh_spec(common, scientific, now,
                                    "first-today" if first_today else "repeat", None, None, fallback)
            ref = self._art_ref(det)
            if ref is None or (ref == "" and self._art_could_arrive()):
                continue            # it would compose, or buy an illustration
            kept = self._kept_sheet(drawn.sheet_key(_drawn_from(spec), ref))
            if kept is None:
                continue
            etag = kept[0]
            if color and not drawn.whole(drawn.twin_path(etag)):
                continue            # a real switch would compose its colour twin
            outs = {str(r["id"]): self._kept_output_for(r, PLATES, etag, now) for r in kits}
            views = {str(r["id"]): self._kept_view_for(r, etag) for r in viewers}
            if None in outs.values() or None in views.values():
                continue            # some screen would have it drawn first
            frames = {}
            for fid, o in outs.items():
                push = dict(self.push_message(fid) or {})
                push["etag"] = o["etag"]
                frames[fid] = {"etag": o["etag"], "file": o["file"], "push": push}
            species[key] = {"common": common.strip().lower(), "first_today": first_today,
                            "etag": etag, "frames": frames, "viewers": views}
        pic = self.pictures[PLATES]
        shown = str(pic.meta.get("species_key") or "") or None
        clean = shown is not None and self._is_shown(_drawn_from(self._fresh_spec(
            pic.meta.get("label") or "", shown, now, "repeat", None, None, fallback)))
        return {"valid_until": self._switch_valid_until(now), "shown": shown,
                "shown_clean": bool(clean),
                "blocked": sorted({b.strip().lower() for b in self.config.species_blocklist if b.strip()}),
                "floor": 0.0 if getattr(src, "_defer_confidence", False) else CONFIDENCE_FLOOR,
                "notes": src.notes_since(SWITCH_NOTES_S) if hasattr(src, "notes_since") else [],
                "species": species}

    def _switch_valid_until(self, now: datetime) -> int:
        """The sooner of the next midnight (novelty and a dated sheet's key
        turn over) and the next start of quiet hours (W-1025)."""
        ends = [datetime.combine(now.date() + timedelta(days=1), dtime.min)]
        if self.config.quiet_hours_mode != "off":
            ends.append(self._next_time(now, self.quiet_window(now.date())[0]))
        return int(min(ends).timestamp())
```

`switch_table` takes `_tick_lock`, so never call it while holding it (`hosted_state` runs from `settle`, outside every tick). Check before relying on them: `Detection` is imported in `service.py` (add `from .sources import Detection` if not); `pic.meta["species_key"]` is set by `_commit`'s `species_key` (it is `det.key`, the lowercased scientific name: confirm in `_picture_meta`); `_is_shown` takes a `_drawn_from` string; `Detection`'s constructor fields match `tests/test_drawn.py`'s `_show`. If `species_key` is not the scientific name, use `pic.meta["names"][1]`.

- [ ] **Step 7: Report it.** In `hosted_state()`, add `"switches": self.switch_table(),` beside `"unchanged"`, with the comment: `# What the front door may switch every screen to without waking this server (W-1025).`

- [ ] **Step 8: Run the tests.**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_switch_table.py tests/test_drawn.py tests/test_hosted.py -q`
Expected: PASS. Then `make test`: PASS.

- [ ] **Step 9: Commit.**

```bash
git add server/featherframe/service.py server/featherframe/sources/pushed.py server/tests/test_switch_table.py
git commit -m "Featherframe Cloud: the server tells its front door what each screen would switch to (W-1025)" -m "Refs: W-1025"
```

---

### Task 4: The server takes the front door's queue

**Files:**
- Modify: `server/featherframe/hosted.py` (`take_queue`, `ack_queue`, `take` takes both, `settle` waits for the start's take and acknowledges after the upload, module docstring's protocol list)
- Modify: `server/featherframe/service.py` (`apply_queue`, `_replay_marker`, `switch_mismatches`; `hosted_state` reports `switch_mismatches`)
- Modify: `server/featherframe/app.py` (lifespan starts the take beside the server; `_apply_seed` shared by the route and the queue)
- Modify: `server/tests/test_hosted.py` (fake door gains `ingest/take` and `ingest/ack`; new tests)

**Interfaces:**
- Consumes: `switch_table` (Task 3).
- Produces: front door API `POST {base}/ingest/take` → `{"rows": [{"seq": int, "path": str, "body": str}]}` and `POST {base}/ingest/ack` with `{"upto": int}` → `{"ok": true}`; queue row paths `/api/ingest/<kind>/<token>`, `/api/hosted/seed`, `marker` (body `{"at": ISO local, "choice": str, "etag": str|null, "upto": int}`); `hosted_state()["switch_mismatches"]: int`; kv keys `queue_seq` (int) and `switch_mismatches` (int).

- [ ] **Step 1: Teach the fake front door the queue.** In `server/tests/test_hosted.py` `front_door()`, add `door.state.ingest = []` (rows `{"seq", "path", "body"}`) and `door.state.acked = []`, and:

```python
    @door.post("/h/ingest/take")
    async def ingest_take(request: Request):
        assert ok(request)
        return {"rows": list(door.state.ingest)}

    @door.post("/h/ingest/ack")
    async def ingest_ack(request: Request):
        upto = (await request.json())["upto"]
        door.state.acked.append(upto)
        door.state.ingest = [r for r in door.state.ingest if r["seq"] > upto]
        return {"ok": True}
```

- [ ] **Step 2: Write the failing tests** (append to `server/tests/test_hosted.py`):

```python
# -- the queue, taken by the server (W-1025) --------------------------------------
def _go(common, sci, note=None):
    return json.dumps({"type": "detection", "metadata": {
        "species": common, "scientific_name": sci, "confidence": 0.9, "note_id": note,
        "bg_detection_date": NOW.strftime("%Y-%m-%d"), "bg_detection_time": NOW.strftime("%H:%M:%S")}})


def _queue_svc(env):
    from featherframe.sources.pushed import PushedSource
    door, link, data = env
    svc = _service()
    svc.config.detection_backend = "birdnet_go"
    svc.source = PushedSource("birdnet_go", svc.db)
    tok = svc.config.ingest_token
    return door, link, svc, tok


def test_the_server_takes_pushes_and_acknowledges_them_after_they_reach_the_front_door(env):
    door, link, svc, tok = _queue_svc(env)
    door.state.ingest = [{"seq": 1, "path": f"/api/ingest/birdnet-go/{tok}", "body": _go("Blue Jay", "Cyanocitta cristata")}]
    link.take(svc)
    assert [d.common_name for d in svc.source.new_since(0)] == ["Blue Jay"]
    assert door.state.acked == []               # not until the database is in R2
    link.settle(svc, False)
    assert door.state.acked == [1] and door.state.ingest == []


def test_a_queue_taken_twice_is_applied_once(env):
    door, link, svc, tok = _queue_svc(env)
    door.state.ingest = [{"seq": 4, "path": f"/api/ingest/birdnet-go/{tok}", "body": _go("Blue Jay", "Cyanocitta cristata")}]
    link.take(svc)
    link.take(svc)                              # a start stopped before its ack, then started again
    assert len(svc.source.new_since(0)) == 1
    assert svc.db.get("queue_seq") == 4


def test_a_marker_is_a_plates_tick_at_its_own_time(env):
    door, link, svc, tok = _queue_svc(env)
    svc.tick()
    at = NOW - timedelta(minutes=30)
    door.state.ingest = [
        {"seq": 1, "path": f"/api/ingest/birdnet-go/{tok}", "body": _go("Blue Jay", "Cyanocitta cristata")},
        {"seq": 2, "path": "marker", "body": json.dumps({"at": at.isoformat(), "choice": "cyanocitta cristata",
                                                         "etag": None, "upto": 1})}]
    link.take(svc)
    assert svc.pictures["plates"].meta.get("label") == "Blue Jay"
    assert svc.render_history()[0]["at"].startswith(at.isoformat()[:16])


def test_a_marker_the_server_disagrees_with_is_counted_and_its_own_choice_stands(env):
    door, link, svc, tok = _queue_svc(env)
    svc.tick()
    door.state.ingest = [
        {"seq": 1, "path": f"/api/ingest/birdnet-go/{tok}", "body": _go("Blue Jay", "Cyanocitta cristata")},
        {"seq": 2, "path": "marker", "body": json.dumps({"at": NOW.isoformat(), "choice": "cardinalis cardinalis",
                                                         "etag": None, "upto": 1})}]
    link.take(svc)
    assert svc.pictures["plates"].meta.get("label") == "Blue Jay"
    assert svc.hosted_state()["switch_mismatches"] == 1


def test_a_seed_in_the_queue_is_applied_like_the_route(env):
    door, link, svc, tok = _queue_svc(env)
    door.state.ingest = [{"seq": 1, "path": "/api/hosted/seed", "body": json.dumps({"region": "europe"})}]
    link.take(svc)
    assert svc.config.region == "europe"


def test_a_report_waits_for_the_starts_take(env, monkeypatch):
    import threading
    door, link, svc, tok = _queue_svc(env)
    gate = threading.Event()
    link.queue_ready.clear()
    reported = []
    monkeypatch.setattr(link, "report", lambda state: reported.append(1))
    t = threading.Thread(target=link.settle, args=(svc, False))
    t.start()
    t.join(0.2)
    assert reported == []                       # still waiting on the take
    link.queue_ready.set()
    t.join(5)
    assert reported == [1]
```

Add `import json` at the top of the file if missing. If `render_history()` rows name the time under another key, read `server/featherframe/service.py` `render_history` and assert on that key.

- [ ] **Step 3: Run them to see them fail.**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_hosted.py -q -k "queue or marker or seed_in or waits_for"`
Expected: FAIL (no `queue_ready`, nothing takes `ingest/take`).

- [ ] **Step 4: The link takes and acknowledges.** In `server/featherframe/hosted.py`, add both calls to the protocol list in the module docstring:

```
    POST   {base}/ingest/take           {"rows": [{seq, path, body}]}: the
                                        detections, seeds and the front door's
                                        own switches since this last took them
    POST   {base}/ingest/ack            {"upto": seq}: they are in R2 now
```

In `HostedLink.__init__`, add `self.queue_ready = threading.Event()` and `self._ack_upto = 0`. Then:

```python
    def take_queue(self) -> list:
        r = self.http.post(self._url("ingest/take"), timeout=TIMEOUT_S)
        r.raise_for_status()
        return list(r.json().get("rows") or [])

    def ack_queue(self, upto: int) -> None:
        r = self.http.post(self._url("ingest/ack"), json={"upto": upto}, timeout=TIMEOUT_S)
        r.raise_for_status()
```

`take()` becomes:

```python
    def take(self, service) -> None:
        """What the frames said and what the front door queued (W-1025): a
        wake takes both before its tick. Whatever happens, the start's own
        take is over and reports may go."""
        try:
            self._apply_checkins(service)
            rows = self.take_queue()
            if rows:
                upto = service.apply_queue(rows)
                self._ack_upto = max(self._ack_upto, upto)
        except (requests.RequestException, OSError, ValueError, sqlite3.Error):
            log.warning("hosted: taking what the front door kept failed", exc_info=True)
        finally:
            self.queue_ready.set()
```

In `settle()`, wait first and acknowledge after the upload, before the removals:

```python
        # A report must never be older than what the front door already shows
        # (W-1025): it waits for the start's take of the queue.
        self.queue_ready.wait(QUEUE_WAIT_S)
```

and inside the `with self._lock:` block, after `self.report(state)`:

```python
                if self._ack_upto:
                    self.ack_queue(self._ack_upto)   # the database holding them is in R2
                    self._ack_upto = 0
```

Add `QUEUE_WAIT_S = 120` beside `TIMEOUT_S`. A `HostedLink` built for a test starts with `queue_ready` set (call `self.queue_ready.set()` in `__init__`), and the lifespan clears it before the start's take (Step 6), so existing tests that never take are not held up.

- [ ] **Step 5: The service applies the queue.** In `server/featherframe/service.py`:

```python
    def apply_queue(self, rows: list) -> int:
        """The front door's queue, in order (W-1025): each detection to the push
        feed, a seed as the settings save it is, and each of the front door's
        own switches as a plates tick at its time. A row at or below the last
        seq applied was applied before a stop that lost its acknowledgement.
        Returns the highest seq seen."""
        last = int(self.db.get("queue_seq", 0) or 0)
        top = last
        with self._working(), self._tick_lock:
            for row in sorted(rows, key=lambda r: int(r.get("seq") or 0)):
                seq = int(row.get("seq") or 0)
                top = max(top, seq)
                if seq <= last:
                    continue
                path, body = str(row.get("path") or ""), str(row.get("body") or "")
                try:
                    payload = json.loads(body) if body else {}
                except ValueError:
                    payload = {}
                if path == "marker":
                    self._replay_marker(payload)
                elif path == "/api/hosted/seed":
                    self.apply_seed(payload)
                elif path.startswith("/api/ingest/"):
                    ingest = getattr(self.source, "ingest", None)
                    if callable(ingest):
                        ingest(payload)
                self.db.set("queue_seq", seq)
        return top

    def _replay_marker(self, marker: dict) -> None:
        """One of the front door's switches, as the tick it stands for, at its
        own time: the source's alarms as `_tick_pictures` reads them, then the
        plates picture. Its choice is checked; ours stands (W-1025)."""
        try:
            at = datetime.fromisoformat(str(marker.get("at")))
        except (TypeError, ValueError):
            at = self._clock()
        available = self.source.available()
        self._track_source(at, available)
        self._quiet = self.quiet_state(at, available=available)
        self._outage = self.outage_state(at)
        before = self.pictures[PLATES].etag
        self._single_tick(at)
        pic = self.pictures[PLATES]
        want = str(marker.get("choice") or "")
        shown = str(pic.meta.get("species_key") or "")
        agrees = (pic.etag == before) if want == "same" else (shown == want)
        if not agrees:
            log.warning("front door chose %s at %s; this server shows %s", want, at, shown or "nothing")
            self.db.set("switch_mismatches", int(self.db.get("switch_mismatches", 0) or 0) + 1)

    def apply_seed(self, body: dict) -> None:
        """A household set up from the phone (W-888): its source and Region,
        as a settings save of those fields."""
        if not isinstance(body, dict):
            return
        cur = self.config.to_dict()
        cur.update({k: str(body[k]) for k in SEED_FIELDS if isinstance(body.get(k), str)})
        self.update_config(Config.from_dict(cur).sanitize())
```

Move `_SEED_FIELDS` from `app.py` to `service.py` as `SEED_FIELDS`, and make `/api/hosted/seed` call `svc.apply_seed(body)` in a threadpool (it stays for a front door from before this change). `apply_queue` takes `_tick_lock` itself: `tick()` already holds it, so call `link.take` only where it is called today (outside `tick()`), never from inside one. `hosted_state()` adds `"switch_mismatches": int(self.db.get("switch_mismatches", 0) or 0),`. Confirm `_track_source`, `quiet_state` and `outage_state` take a `now` argument as `_tick_pictures` calls them (they do at `service.py:1157-1163`).

- [ ] **Step 6: Take the queue beside the start.** In `server/featherframe/app.py` `lifespan`, right after `link = hosted.HostedLink(*conf)`:

```python
        link.queue_ready.clear()   # no report until the start's take is done (W-1025)
```

and after `service.start()`, only when `link is not None`:

```python
        # The front door's queue (W-1025) is taken beside the start, not in
        # front of it: the Container library gives a start 20 s to answer on
        # its port. Every report waits for it (`HostedLink.settle`).
        threading.Thread(target=link.take, args=(service,), name="hosted-take", daemon=True).start()
```

Add `import threading` if missing. `/api/hosted/run` already calls `link.take` before its tick, which now takes the queue too.

- [ ] **Step 7: Run the tests.**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_hosted.py tests/test_switch_table.py -q`
Expected: PASS. Then `make test`: PASS.

- [ ] **Step 8: Commit.**

```bash
git add server/featherframe/hosted.py server/featherframe/service.py server/featherframe/app.py server/tests/test_hosted.py
git commit -m "Featherframe Cloud: the server takes its front door's queue before it reports anything (W-1025)" -m "Refs: W-1025"
```

---

### Task 5: The front door switches alone

**Files:**
- Modify: `hosted/src/wakes.ts` (`WakeState` gains `pushNews`, `lastSwitch`; `due`, `nextAlarm`; new `onlyDetections`)
- Modify: `hosted/src/pagecache.ts` (new `age()`)
- Modify: `hosted/src/household.ts` (schema: `ingest.taken`, `usage.switches`; `ingest()`; `wake()`; `alarm()`; new `decideAlone()`; `internal()` routes `ingest/take`, `ingest/ack`; `takeState()`; `summary()`)
- Modify: `hosted/test/wakes.test.ts`
- Create: `hosted/test/switching.test.ts` (the front door with a node SQLite, as `test/spend.test.ts` builds one)

**Interfaces:**
- Consumes: `switch.ts` (Task 2); the server's `switches`, `switch_mismatches`, `ingest/take`, `ingest/ack` (Tasks 3–4).
- Produces: `Household.decideAlone(): Promise<boolean>`; meta keys `push_news`, `switch_ms`, `switches` (JSON), `switch_mismatches`; `summary()` gains `switches: number` (this month) and `mismatches: number`; `UsageDay` gains `switches: number`.

- [ ] **Step 1: The wake rule's tests.** Add to `hosted/test/wakes.test.ts` (and extend the `s()` helper with `pushNews: false, lastSwitch: 0`):

```ts
import { onlyDetections } from "../src/wakes";

describe("detections alone (W-1025)", () => {
  it("wait out the gap from the later of the last wake and the last switch", () => {
    expect(due(s({ pushNews: true, lastWake: T - MIN_GAP_MS, lastSwitch: T - 60_000 }))).toBe(false);
    expect(due(s({ pushNews: true, lastWake: T - MIN_GAP_MS, lastSwitch: T - MIN_GAP_MS }))).toBe(true);
    expect(nextAlarm(s({ pushNews: true, lastWake: T - MIN_GAP_MS, lastSwitch: T - 60_000 })))
      .toBe(T - 60_000 + MIN_GAP_MS);
  });
  it("are the only reason only when nothing else is due", () => {
    const ripe = { pushNews: true, lastWake: T - MIN_GAP_MS };
    expect(onlyDetections(s(ripe))).toBe(true);
    expect(onlyDetections(s({ ...ripe, news: true }))).toBe(false);       // a pairing
    expect(onlyDetections(s({ ...ripe, look: true }))).toBe(false);
    expect(onlyDetections(s({ ...ripe, named: T - 1 }))).toBe(false);
    expect(onlyDetections(s({ ...ripe, lastWake: T - SAFETY_MS }))).toBe(false);
  });
});
```

- [ ] **Step 2: Run them to see them fail.**

Run: `cd hosted && npx vitest run test/wakes.test.ts`
Expected: FAIL, `onlyDetections` is not exported.

- [ ] **Step 3: The wake rule.** `hosted/src/wakes.ts`:

```ts
export interface WakeState {
  now: number;
  lastWake: number;     // ms; 0 when never
  lastSwitch: number;   // ms; the front door's last switch on its own (W-1025); 0 when never
  named: number;        // ms; 0 when none
  news: boolean;        // a pairing, a BirdWeather look's news
  pushNews: boolean;    // a pushed detection the server did not say changes nothing
  look: boolean;
  birdweather: boolean; // a BirdWeather station is polled (and not in quiet hours)
}

const pushGap = (s: WakeState) => Math.max(s.lastWake, s.lastSwitch) + MIN_GAP_MS;

export function due(s: WakeState): boolean {
  return (s.named > 0 && s.named <= s.now)
    || ((s.news || s.look) && s.now - s.lastWake >= MIN_GAP_MS)
    || (s.pushNews && s.now >= pushGap(s))
    || s.now - s.lastWake >= SAFETY_MS;
}

/** Due only because detections arrived (W-1025): the front door may switch
 * the screens itself instead of waking the server. */
export function onlyDetections(s: WakeState): boolean {
  return s.pushNews && s.now >= pushGap(s) && !s.news && !s.look
    && !(s.named > 0 && s.named <= s.now) && s.now - s.lastWake < SAFETY_MS;
}

export function nextAlarm(s: WakeState): number {
  const times = [s.lastWake + SAFETY_MS];
  if (s.named > s.now) times.push(s.named);
  if (s.news || s.look) times.push(Math.max(s.now, s.lastWake + MIN_GAP_MS));
  if (s.pushNews) times.push(Math.max(s.now, pushGap(s)));
  if (s.birdweather) times.push(s.now + POLL_MS);
  return Math.max(s.now + 1000, Math.min(...times));
}
```

Run: `cd hosted && npx vitest run test/wakes.test.ts` → PASS.

- [ ] **Step 4: The front door's tests.** `hosted/test/switching.test.ts`:

```ts
// @ts-nocheck: a front door on node's SQLite, as test/spend.test.ts builds one.
// The front door switches the screens itself when only detections arrived and
// the server said what each would show (W-1025).
import { describe, expect, it, vi } from "vitest";
import { nodeSql } from "./sql";

vi.mock("cloudflare:workers", () => ({
  DurableObject: class { ctx: unknown; env: unknown; constructor(ctx: unknown, env: unknown) { this.ctx = ctx; this.env = env; } },
}));
vi.mock("../src/viewers", () => ({ display: vi.fn(), lobbyPng: vi.fn(), shortOf: vi.fn(), trmnlHeaders: vi.fn() }));

const GO = (common: string, sci: string) =>
  JSON.stringify({ type: "detection", metadata: { species: common, scientific_name: sci, confidence: 0.9 } });
const TABLE = {
  valid_until: 9_999_999_999, shown: "haemorhous mexicanus", shown_clean: true, blocked: [], floor: 0, notes: [],
  species: { "turdus migratorius": { common: "american robin", first_today: false, etag: "pic-robin",
    frames: { K1: { etag: "out-robin", file: "frames/drawn/out/robin.fff", push: { etag: "out-robin", rotation: 90, power: "awake", ota: false } } },
    viewers: {} } },
};

async function door({ running = false } = {}) {
  const { Household } = await import("../src/household");
  const sql = nodeSql();
  const sent: string[] = [];
  const woken: string[] = [];
  const sockets = [{ send: (m: string) => sent.push(m), close: () => {} }];
  const h = new Household({ storage: { sql, setAlarm: async () => {}, deleteAlarm: async () => {} },
                            getWebSockets: () => sockets, waitUntil: () => {} } as never,
    { APP_HOST: "cloud.featherframe.app", DATA: { delete: async () => {} },
      SERVER: { getByName: () => ({ running: async () => running, configure: async () => {},
                                     fetch: async () => new Response("{}") }) } } as never);
  h.setMeta("hid", "h1"); h.setMeta("key", "k"); h.setMeta("tz", "UTC");
  h.setMeta("source_kind", "birdnet_go"); h.setMeta("apprise_token", "tok");
  h.wake = async () => { woken.push("wake"); };
  await h.takeState({ frames: { K1: { status: "on", etag: "out-finch", file: "frames/drawn/out/finch.fff",
    headers: {}, push: { etag: "out-finch", rotation: 90, power: "awake", ota: false } } },
    viewers: {}, poll: true, unchanged: ["house finch", "haemorhous mexicanus"], switches: TABLE, page_build: "b",
    source: { kind: "birdnet_go", token: "tok" } });
  return { h, sql, sent, woken };
}
const push = (h, body: string) => h.ingest(new Request("https://x/api/ingest/birdnet-go/tok", { method: "POST", body }),
  new URL("https://x/api/ingest/birdnet-go/tok"));
const rows = (sql) => sql.exec("SELECT seq, path, body, taken FROM ingest ORDER BY seq").toArray();

describe("the front door's own switch", () => {
  it("switches the frames, tells their sockets and leaves a marker, without the server", async () => {
    const { h, sql, sent, woken } = await door();
    await push(h, GO("American Robin", "Turdus migratorius"));
    expect(await h.decideAlone()).toBe(true);
    expect(h.frameRow("K1").etag).toBe("out-robin");
    expect(h.frameRow("K1").file).toBe("frames/drawn/out/robin.fff");
    expect(JSON.parse(sent.at(-1)).etag).toBe("out-robin");
    const q = rows(sql);
    expect(q.map((r) => r.path)).toEqual(["/api/ingest/birdnet-go/tok", "marker"]);
    expect(JSON.parse(q[1].body)).toMatchObject({ choice: "turdus migratorius", etag: "pic-robin", upto: q[0].seq });
    expect(JSON.parse(h.meta("unchanged"))).toEqual(["american robin", "turdus migratorius"]);
    expect(woken).toEqual([]);
  });

  it("wakes the server for a species it has no answer for", async () => {
    const { h } = await door();
    await push(h, GO("Blue Jay", "Cyanocitta cristata"));
    expect(await h.decideAlone()).toBe(false);
  });

  it("leaves a running server to decide", async () => {
    const { h } = await door({ running: true });
    await push(h, GO("American Robin", "Turdus migratorius"));
    expect(await h.decideAlone()).toBe(false);
  });

  it("decides nothing once the table has expired", async () => {
    const { h } = await door();
    h.setMeta("switches", JSON.stringify({ ...TABLE, valid_until: 1 }));
    await push(h, GO("American Robin", "Turdus migratorius"));
    expect(await h.decideAlone()).toBe(false);
  });

  it("decides nothing for a server that reports no table (the deploy gap)", async () => {
    const { h } = await door();
    await h.takeState({ frames: {}, viewers: {}, poll: true, page_build: "b", source: { kind: "birdnet_go", token: "tok" } });
    await push(h, GO("American Robin", "Turdus migratorius"));
    expect(await h.decideAlone()).toBe(false);
  });

  it("keeps the switched frames when a report comes from before its marker, and asks for a wake", async () => {
    const { h } = await door();
    await push(h, GO("American Robin", "Turdus migratorius"));
    await h.decideAlone();
    h.setMeta("news", null);
    // A save's report: the server took the queue before the marker was written.
    await h.takeState({ frames: { K1: { status: "on", etag: "out-finch", file: "frames/drawn/out/finch.fff",
      headers: {}, push: { etag: "out-finch" } } }, viewers: {}, poll: true, switches: TABLE, page_build: "b",
      source: { kind: "birdnet_go", token: "tok" } });
    expect(h.frameRow("K1").etag).toBe("out-robin");
    expect(h.meta("news")).toBe("1");
  });

  it("hands the server everything queued and forgets it only once acknowledged", async () => {
    const { h, sql } = await door();
    await push(h, GO("American Robin", "Turdus migratorius"));
    await h.decideAlone();
    const call = (path: string, body?: unknown) => h.internal(new Request(`https://x/_internal/h1/${path}`,
      { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }), path);
    const taken = await (await call("ingest/take")).json();
    expect(taken.rows.map((r) => r.path)).toEqual(["/api/ingest/birdnet-go/tok", "marker"]);
    expect(rows(sql).every((r) => r.taken === 1)).toBe(true);
    await call("ingest/ack", { upto: taken.rows.at(-1).seq });
    expect(rows(sql)).toEqual([]);
  });

  it("ages the page's copies instead of deleting them", async () => {
    const { h, sql } = await door();
    sql.exec("INSERT INTO page_cache (key, kind, object, headers, filled_at, read_at) VALUES ('k', 'changing', 'o', '{}', 5, 5)");
    await push(h, GO("American Robin", "Turdus migratorius"));
    await h.decideAlone();
    expect(sql.exec("SELECT object, filled_at FROM page_cache").toArray()).toEqual([{ object: "o", filled_at: 0 }]);
  });
});
```

Read `hosted/src/pagecache.ts`'s `CREATE TABLE page_cache` before running, and match the `INSERT`'s columns to it.

- [ ] **Step 5: Run them to see them fail.**

Run: `cd hosted && npx vitest run test/switching.test.ts`
Expected: FAIL, `decideAlone` is not a function.

- [ ] **Step 6: `PageCache.age()`.** In `hosted/src/pagecache.ts` beside `clear()`:

```ts
  /** Every changing copy is old, but kept (W-1025): the next read serves it
   * while a look asks for a wake, as after any change. `clear()` would leave
   * nothing to serve, and the next poll would start the server itself. */
  age(): void {
    this.sql.exec("UPDATE page_cache SET filled_at = 0 WHERE kind = 'changing' AND filled_at IS NOT NULL");
  }
```

- [ ] **Step 7: The front door.** In `hosted/src/household.ts`:

Schema, after the existing `usage` column loop in the constructor:

```ts
    // What the server has taken but not yet acknowledged (W-1025).
    if (!new Set(this.sql.exec("SELECT * FROM ingest LIMIT 0").columnNames).has("taken")) {
      this.sql.exec("ALTER TABLE ingest ADD COLUMN taken INTEGER NOT NULL DEFAULT 0");
    }
```

and add `"switches"` to the `usage` column loop (`for (const c of ["wake_ms", "page_ms", "switches"])`).

Imports: `import { choose, kindOf, MARKER, parse, type Queued, type SwitchTable } from "./switch";` and `onlyDetections` from `./wakes`.

`wakeState()` reads the two new fields:

```ts
      lastSwitch: Number(this.meta("switch_ms") || 0),
      pushNews: this.meta("push_news") === "1",
```

`ingest()` sets `push_news` instead of `news`:

```ts
    if (this.meta("poll") !== "0" && !changesNothing(this.meta("unchanged"), pushedNames(kind, body))) {
      this.setMeta("push_news", "1");
    }
```

`alarm()`:

```ts
  async alarm(): Promise<void> {
    if (this.meta("suspended")) return;
    await this.lookForNews();
    const s = this.wakeState();
    if (!due(s)) return this.schedule();
    // Detections alone, and the server said what each screen would show:
    // switch them here and let the server catch up when it next starts (W-1025).
    if (onlyDetections(s) && await this.decideAlone()) return this.schedule();
    await this.wake();
  }
```

`wake()` no longer sends the queue (the server takes it) and clears both kinds of news:

```ts
    this.setMeta("news", "0");
    this.setMeta("push_news", null);
    ...
    try {
      const stub = await this.server();
      // The server takes the queue itself, before its tick and before any
      // report it makes (W-1025): nothing is sent from here.
      await stub.fetch("http://server/api/hosted/run", { method: "POST" });
```

(delete the `queued` loop).

`decideAlone()`:

```ts
  /** The server's switch table, as its last report gave it, or null. */
  switchTable(): SwitchTable | null {
    const raw = this.meta("switches");
    if (!raw) return null;
    try { const t = JSON.parse(raw); return t && t.species ? t : null; } catch { return null; }
  }

  /** Detections were the only reason to wake the server (W-1025). When the
   * server said what each screen would show for every species in the batch,
   * switch them here: the frames' rows, their sockets, the viewers, and a
   * marker in the queue right after the detections it decided over, which the
   * server replays as the tick it stands for. True when it decided. */
  async decideAlone(): Promise<boolean> {
    const table = this.switchTable();
    if (!table || Date.now() / 1000 >= table.valid_until) return false;
    if (await this.env.SERVER.getByName(this.meta("hid")!).running()) return false;
    // From here to the marker: no await, so no push lands in between.
    const rows = this.sql.exec<{ seq: number; path: string; body: string }>(
      "SELECT seq, path, body FROM ingest WHERE taken = 0 ORDER BY seq").toArray();
    const queued: Queued[] = [];
    for (const r of rows) {
      if (r.path === MARKER) { queued.push({ marker: true }); continue; }
      const kind = kindOf(r.path);
      if (!kind) return false;                       // a seed: the server's to apply
      queued.push({ push: parse(kind, r.body) });
    }
    const choice = choose(table, queued);
    if (choice === "wake") return false;
    const entry = choice === "same" ? null : table.species[choice];
    if (entry) {
      for (const [id, f] of Object.entries(entry.frames)) {
        const push = JSON.stringify(f.push);
        this.sql.exec("UPDATE frames SET etag = ?, file = ?, push = ? WHERE id = ?", f.etag, f.file, push, id);
        for (const ws of this.ctx.getWebSockets(id)) ws.send(push);
      }
      for (const [id, v] of Object.entries(entry.viewers)) {
        this.sql.exec("UPDATE viewers SET name = ?, file = ? WHERE id = ?", v.name, v.file, id);
      }
      this.setMeta("unchanged", JSON.stringify([...table.blocked, entry.common, choice]));
    }
    this.sql.exec("INSERT INTO ingest (path, body) VALUES (?, ?)", MARKER, JSON.stringify({
      at: localIso(this.meta("tz") || "UTC"), choice, etag: entry ? entry.etag : null,
      upto: rows.length ? rows[rows.length - 1].seq : 0 }));
    this.setMeta("switch_ms", String(Date.now()));
    this.setMeta("push_news", null);
    this.addSwitch();
    if (entry) this.cache.age();
    return true;
  }

  /** A switch made here, counted by UTC day beside the wakes (W-1025). */
  addSwitch(): void {
    const day = new Date().toISOString().slice(0, 10);
    this.sql.exec(`INSERT INTO usage (day, switches) VALUES (?, 1)
      ON CONFLICT (day) DO UPDATE SET switches = switches + 1`, day);
  }
```

`internal()` routes, beside `checkins/take`:

```ts
    // The server takes the queue itself (W-1025): everything in it, marked
    // taken, and forgets it only once the server says it is in R2.
    if (path === "ingest/take" && request.method === "POST") {
      const rows = this.sql.exec<{ seq: number; path: string; body: string }>(
        "SELECT seq, path, body FROM ingest ORDER BY seq").toArray();
      this.sql.exec("UPDATE ingest SET taken = 1");
      return Response.json({ rows });
    }
    if (path === "ingest/ack" && request.method === "POST") {
      const upto = Number((await request.json<{ upto?: number }>().catch(() => ({}))).upto || 0);
      this.sql.exec("DELETE FROM ingest WHERE taken = 1 AND seq <= ?", upto);
      return Response.json({ ok: true });
    }
```

`takeState()`: extend the `state` type with `switches?: SwitchTable | Record<string, never>` and `switch_mismatches?: number`. At the top of the method:

```ts
    // A report from a run that took the queue before the front door's last
    // switch (W-1025) is behind it: the frames, viewers and table stay as the
    // front door left them, and a wake soon brings the server up to date.
    const behind = this.sql.exec<{ n: number }>(
      "SELECT count(*) AS n FROM ingest WHERE path = ? AND taken = 0", MARKER).one().n > 0;
```

Wrap the existing frames loop, the viewers loop and the `unchanged` line in `if (!behind) { … }`, and add:

```ts
    if (behind) this.setMeta("news", "1");
    else this.setMeta("switches", state.switches && "species" in state.switches ? JSON.stringify(state.switches) : null);
    this.setMeta("switch_mismatches", state.switch_mismatches ? String(state.switch_mismatches) : null);
```

`summary()`: add to the return value

```ts
             switches: this.sql.exec<{ n: number }>("SELECT coalesce(sum(switches), 0) AS n FROM usage WHERE day >= ?",
               new Date().toISOString().slice(0, 8) + "01").one().n,
             mismatches: Number(this.meta("switch_mismatches") || 0),
```

and add `switches: number; mismatches: number` to its return type. In `hosted/src/usage.ts`, add `switches: number` to `UsageDay` and `switches` to `summary()`'s `SELECT day, wakes, …` column list.

- [ ] **Step 8: Run the front door's tests.**

Run: `cd hosted && npm test`
Expected: PASS (fix any test that builds a `WakeState` or a `UsageDay` without the new fields by adding them).

- [ ] **Step 9: Commit.**

```bash
git add hosted/src/wakes.ts hosted/src/pagecache.ts hosted/src/household.ts hosted/src/usage.ts hosted/test/wakes.test.ts hosted/test/switching.test.ts
git commit -m "Featherframe Cloud: the front door switches to a picture already drawn without starting the server (W-1025)" -m "Refs: W-1025"
```

---

### Task 6: The admin page counts them

**Files:**
- Modify: `hosted/src/pages.ts` (the households table's server note), `hosted/src/admin.ts` type of a household row if it names `summary()`'s fields
- Modify: `hosted/test/admin.test.ts`

**Interfaces:**
- Consumes: `summary().switches`, `summary().mismatches` (Task 5).

- [ ] **Step 1: The failing test.** In `hosted/test/admin.test.ts`, find the test that renders the households table (search for `s a wake`) and add a household with `switches: 40, mismatches: 1` to its fixture, then:

```ts
    expect(html).toContain("40 switches without a wake");
    expect(html).toContain("1 mismatch");
```

and for a household with `switches: 0, mismatches: 0`, neither phrase appears.

- [ ] **Step 2: Run it to see it fail.** `cd hosted && npx vitest run test/admin.test.ts` → FAIL.

- [ ] **Step 3: Render it.** In `hosted/src/pages.ts`, the `note` list for each household gains two entries after the wake time:

```ts
        h.switches ? `${h.switches} switch${h.switches === 1 ? "" : "es"} without a wake` : "",
        h.mismatches ? `${h.mismatches} mismatch${h.mismatches === 1 ? "" : "es"}` : "",
```

Add `switches: number; mismatches: number` to the household type `pages.ts` renders (line ~208, beside `ai`).

- [ ] **Step 4: Run it.** `cd hosted && npm test` → PASS.

- [ ] **Step 5: Commit.**

```bash
git add hosted/src/pages.ts hosted/src/admin.ts hosted/test/admin.test.ts
git commit -m "Admin: each household's switches without a wake, and any the server disagreed with (W-1025)" -m "Refs: W-1025"
```

---

### Task 7: Ship and measure

**Files:** none changed unless a check fails.

- [ ] **Step 1: Everything passes.** `make test` from the repo root, `cd hosted && npm test`, and `cd server && ./.venv/bin/python scripts/check_copy.py` (no owner-facing copy changed, so this must stay clean).

- [ ] **Step 2: PR.** Push the branch and open a ready PR titled "Featherframe Cloud: the front door switches to a picture already drawn without starting the server (W-1025)", body: what changed and why (link the spec), how it was verified, what is left (BirdWeather, the preview), `Refs W-1025`.

- [ ] **Step 3: After merge, deploy.** From an up-to-date `main`: `cd hosted && npx wrangler deploy` (Docker running; retry on a registry push drop). The Worker is live at once; the server image only after its rollout, which the containers rollouts API reports (`GET /accounts/<id>/containers/applications/<app>/rollouts`, `status: completed`).

- [ ] **Step 4: Check the first hour on the image.** In the containers dataset (cloudflare-api MCP `execute`, telemetry query, service `a0382371-…`): wakes log the take, markers replay with no `front door chose … this server shows …` warning, and the readiness check answers `GET / … 204` (W-1024). In the zone's HTTP analytics, `/api/frame` keeps both kits' heartbeats.

- [ ] **Step 5: Measure a whole day.** The first full day (04:00Z → 04:00Z) on the image: scratchpad `cost.py` (rewrite from `memory/hosted-cost-diagnostics.md` if gone) against 10 Oct ($0.0058, 68 starts). Record starts, CPU-s, $/day, the admin page's switches and mismatches, in the memory file and as a comment on W-1025.
