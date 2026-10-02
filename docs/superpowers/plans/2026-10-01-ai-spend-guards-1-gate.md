# AI spend guards, part 1: the gate (W-938) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every paid AI call (illustration, collage sheet, species brief, weather) passes one gate that checks a monthly limit, a runaway pause and per-subject rules, records the purchase durably before the call, and settles it after; owners get a master switch, a limit field and clear states on the webapp and the frame.

**Architecture:** A new `featherframe/spend.py` holds the gate (`Gate.purchase`, a context manager), the pure `decide()` rule, the price estimates, and two stores (`MemoryStore`, `LocalStore` on our SQLite). Paid model methods are fenced: they raise outside a purchase. `GeneratedArtProvider` routes its four purchases through the gate; the service builds the gate, passes owner/nightly/interval context into collages, and reports one `status()["ai"]` view the page renders. Part 2 (separate plan) moves the Cloud count to the front door.

**Tech Stack:** Python 3.9+ (CI floor), FastAPI + Jinja2 page with ES5 script, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-10-01-ai-spend-guards-design.md`

## Global Constraints

- Python 3.9 is the CI floor: `from __future__ import annotations` in every new module; no `match`, no runtime `X | Y` types.
- Every user-facing string is written verbatim in this plan. Do not invent copy. `docs/STYLE.md` governs; `make test` runs `scripts/check_copy.py` on the webapp.
- Strings, exactly:
  - Master switch label: `AI image generation`
  - Locked hints: `Needs an API key`, `AI image generation is off`
  - Limit row label: `Monthly limit`
  - Summaries: `Off`, `No API key`, `Key rejected`, `Out of credits`, `Paused`, `Limit reached`, `Last attempt failed`, and `{Provider} · ${usd:.2f} of ${limit:.2f}` (e.g. `OpenAI · $1.20 of $10.00`)
  - Paused notice: `AI generation is paused: {n} purchases in the last hour, more than usual. Nothing more is bought until you resume.` Button: `Resume`
  - Limit notice: `This month's AI spend reached your ${limit:.2f} limit. New AI illustrations and collages resume on 1 {Mon}, or raise the limit.` (`{Mon}` = next month's three-letter name, e.g. `1 Nov`)
  - Projection line: `About ${amount} a month at this station's pace.` (`amount` with two decimals under $10, whole dollars from $10)
  - Frame footnotes: `AI limit reached`, `AI paused`
  - Regenerate refusals: `AI image generation is off.`, `AI generation is paused.`, `This month's AI limit is reached.`
  - Collage repaint refusals (the page prefixes "Could not start — " and adds the period): `AI image generation is off`, `AI generation is paused`, `this month's AI limit is reached`
  - Generated illustrations line: `This month: ${usd:.2f} of ${limit:.2f}.`
- Numbers: `RUNAWAY_PER_HOUR = 6` (counts automatic `plate` and `collage` purchases only; briefs and weather cost cents and a day's collage can need a dozen briefs), `OPEN_HOLD_S = 86400`, default limit $10, limit sanitized to 1–1000 whole dollars, nightly collage window 36 h, interval collage window = interval − 600 s.
- Prices (`spend.IMAGE_USD`): low 0.034, medium 0.039, high 0.070, xhigh 0.103, max 0.194; collage × 1.1; brief 0.002; weather 0.012; any unmeasured paid model 0.21; `local:*` and `a1111` 0.
- Vendor refusals (record released, $0): HTTP 400, 401, 402, 403, 404, 429, and `requests.exceptions.ConnectTimeout`. Everything else leaves the record open (counted at its estimate).
- Run tests from `server/`: `./.venv/bin/python -m pytest tests/<file> -q`. The whole suite: `make test` from the repo root.
- Commit titles follow docs/STYLE.md: an outcome sentence with `(W-938)`, body with `Refs: W-938`.

## File structure

- Create `server/featherframe/spend.py` — the gate, `decide()`, estimates, the fence, `MemoryStore`, `LocalStore`.
- Create `server/tests/fixtures/spend-cases.json` — `decide()` cases, shared with the Worker in part 2.
- Create tests: `server/tests/test_spend.py`, `test_spend_store.py`, `test_spend_fence.py`, `test_spend_genart.py`, `test_spend_service.py`, `test_spend_replay.py`, `test_spend_page.py`.
- Modify `server/featherframe/db.py` — the `spend` table and three methods.
- Modify `server/featherframe/render/genart.py` — fence the model classes, route purchases through the gate, drop `record_spend`/`spend_for_month`.
- Modify `server/featherframe/config.py` — `illustrations_generated`, `ai_monthly_limit_usd`, migration.
- Modify `server/featherframe/service.py` — build the gate, collage context, `ai_view`, `ai_refusal`, `resume_ai`, footnotes.
- Modify `server/featherframe/app.py` — settings fields, `/api/ai/resume`, refusal messages, index spend.
- Modify `server/templates/index.html` — the switch, limit row, states, notices, projection, spend line.
- Modify `firmware/tools/screens/boot_art.py` — its calls go through a gate.
- Modify tests that call paid model methods directly or assert on `spend.jsonl`.
- Modify `AGENTS.md`; the wiki's AI illustrations page (separate repo `featherframe.wiki`).

---

### Task 1: The gate and its rule

**Files:**
- Create: `server/featherframe/spend.py`
- Create: `server/tests/fixtures/spend-cases.json`
- Test: `server/tests/test_spend.py`

**Interfaces:**
- Produces:
  - `spend.Record(id: str, at: float, month: str, day: str, kind: str, subject: str, auto: bool, model: str, quality: Optional[str], est_usd: float, cost_usd: Optional[float] = None, state: str = "open")`
  - `spend.Rule(limit_usd: float, runaway_per_hour: Optional[int], window_s: Optional[float])`
  - `spend.Snapshot(rows: list[Record], pause: Optional[dict], resumed_at: float)`; `pause` is `{"at": float, "count": int}`
  - `spend.decide(rows, paused: bool, resumed_at: float, rec: Record, rule: Rule, now: float) -> Optional[str]` — `None` or one of `"paused" | "limit" | "subject" | "runaway"`
  - `spend.estimate_usd(kind: str, model: str, quality: Optional[str]) -> float`
  - `spend.vendor_refused(exc: BaseException) -> bool`
  - `spend.Refused(reason)` with `.reason`; `spend.Unguarded`
  - `spend.MemoryStore()` with `reserve(rec, rule) -> Optional[str]`, `settle(rec_id, state, cost_usd, usage) -> None`, `snapshot(since: float) -> Snapshot`, `resume(now: float) -> None`
  - `spend.Gate(store, *, enabled=lambda: True, limit_usd=lambda: DEFAULT_LIMIT_USD, runaway_per_hour=RUNAWAY_PER_HOUR, now=datetime.now)` with `purchase(kind, subject, *, model, quality=None, auto=True, window_s=None)` (context manager yielding a `Purchase`), `summary() -> dict`, `resume() -> None`, `Gate.unlimited()` classmethod, `.store`
  - `Purchase.settle(usage: Optional[dict] = None, cost_usd: Optional[float] = None)`, `Purchase.release()`, `.record`
  - `spend.fenced(fn)` decorator; `spend.active() -> Optional[Purchase]`

- [ ] **Step 1: Write the shared rule cases**

Create `server/tests/fixtures/spend-cases.json`. Times are epoch seconds; `now` is 1790000000 (a moment in Sep 2026). Each case is checked by Python here and by the Worker in part 2.

```json
[
  {"name": "an empty month buys",
   "rows": [], "paused": false, "resumed_at": 0,
   "rec": {"id": "n", "at": 1790000000, "month": "2026-09", "day": "2026-09-21", "kind": "plate", "subject": "tyto-alba", "auto": true, "model": "gpt-image-2.5-sunburst", "quality": "medium", "est_usd": 0.039, "cost_usd": null, "state": "open"},
   "rule": {"limit_usd": 10, "runaway_per_hour": 6, "window_s": null}, "now": 1790000000, "expect": null},
  {"name": "paused refuses everything, an owner's repaint too",
   "rows": [], "paused": true, "resumed_at": 0,
   "rec": {"id": "n", "at": 1790000000, "month": "2026-09", "day": "2026-09-21", "kind": "plate", "subject": "tyto-alba", "auto": false, "model": "gpt-image-2.5-sunburst", "quality": "medium", "est_usd": 0.039, "cost_usd": null, "state": "open"},
   "rule": {"limit_usd": 10, "runaway_per_hour": 6, "window_s": null}, "now": 1790000000, "expect": "paused"},
  {"name": "settled at cost and open at estimate both count toward the limit",
   "rows": [
     {"id": "a", "at": 1789900000, "month": "2026-09", "day": "2026-09-20", "kind": "collage", "subject": "2026-09-20", "auto": true, "model": "gpt-image-2.5-sunburst", "quality": "max", "est_usd": 0.2134, "cost_usd": 9.80, "state": "settled"},
     {"id": "b", "at": 1789950000, "month": "2026-09", "day": "2026-09-20", "kind": "collage", "subject": "2026-09-20/nightly", "auto": true, "model": "gpt-image-2.5-sunburst", "quality": "max", "est_usd": 0.2134, "cost_usd": null, "state": "open"}],
   "paused": false, "resumed_at": 0,
   "rec": {"id": "n", "at": 1790000000, "month": "2026-09", "day": "2026-09-21", "kind": "plate", "subject": "tyto-alba", "auto": false, "model": "gpt-image-2.5-sunburst", "quality": "medium", "est_usd": 0.039, "cost_usd": null, "state": "open"},
   "rule": {"limit_usd": 10, "runaway_per_hour": 6, "window_s": null}, "now": 1790000000, "expect": "limit"},
  {"name": "a released record costs nothing",
   "rows": [
     {"id": "a", "at": 1789900000, "month": "2026-09", "day": "2026-09-20", "kind": "plate", "subject": "x", "auto": true, "model": "gpt-image-2.5-sunburst", "quality": "max", "est_usd": 9.99, "cost_usd": 0, "state": "released"}],
   "paused": false, "resumed_at": 0,
   "rec": {"id": "n", "at": 1790000000, "month": "2026-09", "day": "2026-09-21", "kind": "plate", "subject": "tyto-alba", "auto": true, "model": "gpt-image-2.5-sunburst", "quality": "medium", "est_usd": 0.039, "cost_usd": null, "state": "open"},
   "rule": {"limit_usd": 10, "runaway_per_hour": 6, "window_s": null}, "now": 1790000000, "expect": null},
  {"name": "last month does not count",
   "rows": [
     {"id": "a", "at": 1789000000, "month": "2026-08", "day": "2026-08-31", "kind": "plate", "subject": "x", "auto": true, "model": "gpt-image-2.5-sunburst", "quality": "max", "est_usd": 0.194, "cost_usd": 50, "state": "settled"}],
   "paused": false, "resumed_at": 0,
   "rec": {"id": "n", "at": 1790000000, "month": "2026-09", "day": "2026-09-21", "kind": "plate", "subject": "tyto-alba", "auto": true, "model": "gpt-image-2.5-sunburst", "quality": "medium", "est_usd": 0.039, "cost_usd": null, "state": "open"},
   "rule": {"limit_usd": 10, "runaway_per_hour": 6, "window_s": null}, "now": 1790000000, "expect": null},
  {"name": "an open record holds its subject for a day",
   "rows": [
     {"id": "a", "at": 1789930000, "month": "2026-09", "day": "2026-09-21", "kind": "plate", "subject": "tyto-alba", "auto": true, "model": "gpt-image-2.5-sunburst", "quality": "medium", "est_usd": 0.039, "cost_usd": null, "state": "open"}],
   "paused": false, "resumed_at": 0,
   "rec": {"id": "n", "at": 1790000000, "month": "2026-09", "day": "2026-09-21", "kind": "plate", "subject": "tyto-alba", "auto": true, "model": "gpt-image-2.5-sunburst", "quality": "medium", "est_usd": 0.039, "cost_usd": null, "state": "open"},
   "rule": {"limit_usd": 10, "runaway_per_hour": 6, "window_s": null}, "now": 1790000000, "expect": "subject"},
  {"name": "the hold ends after a day",
   "rows": [
     {"id": "a", "at": 1789900000, "month": "2026-09", "day": "2026-09-20", "kind": "plate", "subject": "tyto-alba", "auto": true, "model": "gpt-image-2.5-sunburst", "quality": "medium", "est_usd": 0.039, "cost_usd": null, "state": "open"}],
   "paused": false, "resumed_at": 0,
   "rec": {"id": "n", "at": 1790000000, "month": "2026-09", "day": "2026-09-21", "kind": "plate", "subject": "tyto-alba", "auto": true, "model": "gpt-image-2.5-sunburst", "quality": "medium", "est_usd": 0.039, "cost_usd": null, "state": "open"},
   "rule": {"limit_usd": 10, "runaway_per_hour": 6, "window_s": null}, "now": 1790000000, "expect": null},
  {"name": "a settled illustration does not hold its species (the cache does)",
   "rows": [
     {"id": "a", "at": 1789990000, "month": "2026-09", "day": "2026-09-21", "kind": "plate", "subject": "tyto-alba", "auto": true, "model": "gpt-image-2.5-sunburst", "quality": "medium", "est_usd": 0.039, "cost_usd": 0.04, "state": "settled"}],
   "paused": false, "resumed_at": 0,
   "rec": {"id": "n", "at": 1790000000, "month": "2026-09", "day": "2026-09-21", "kind": "plate", "subject": "tyto-alba", "auto": true, "model": "gpt-image-2.5-sunburst", "quality": "medium", "est_usd": 0.039, "cost_usd": null, "state": "open"},
   "rule": {"limit_usd": 10, "runaway_per_hour": 6, "window_s": null}, "now": 1790000000, "expect": null},
  {"name": "one collage per interval for a date",
   "rows": [
     {"id": "a", "at": 1789990000, "month": "2026-09", "day": "2026-09-21", "kind": "collage", "subject": "2026-09-21", "auto": true, "model": "gpt-image-2.5-sunburst", "quality": "medium", "est_usd": 0.0429, "cost_usd": 0.045, "state": "settled"}],
   "paused": false, "resumed_at": 0,
   "rec": {"id": "n", "at": 1790000000, "month": "2026-09", "day": "2026-09-21", "kind": "collage", "subject": "2026-09-21", "auto": true, "model": "gpt-image-2.5-sunburst", "quality": "medium", "est_usd": 0.0429, "cost_usd": null, "state": "open"},
   "rule": {"limit_usd": 10, "runaway_per_hour": 6, "window_s": 21000}, "now": 1790000000, "expect": "subject"},
  {"name": "an owner's repaint skips the subject rule",
   "rows": [
     {"id": "a", "at": 1789990000, "month": "2026-09", "day": "2026-09-21", "kind": "collage", "subject": "2026-09-21", "auto": true, "model": "gpt-image-2.5-sunburst", "quality": "medium", "est_usd": 0.0429, "cost_usd": null, "state": "open"}],
   "paused": false, "resumed_at": 0,
   "rec": {"id": "n", "at": 1790000000, "month": "2026-09", "day": "2026-09-21", "kind": "collage", "subject": "2026-09-21", "auto": false, "model": "gpt-image-2.5-sunburst", "quality": "medium", "est_usd": 0.0429, "cost_usd": null, "state": "open"},
   "rule": {"limit_usd": 10, "runaway_per_hour": 6, "window_s": null}, "now": 1790000000, "expect": null},
  {"name": "the seventh automatic image in an hour trips the pause",
   "rows": [
     {"id": "1", "at": 1789996500, "month": "2026-09", "day": "2026-09-21", "kind": "plate", "subject": "s1", "auto": true, "model": "m", "quality": null, "est_usd": 0.21, "cost_usd": 0.21, "state": "settled"},
     {"id": "2", "at": 1789997000, "month": "2026-09", "day": "2026-09-21", "kind": "plate", "subject": "s2", "auto": true, "model": "m", "quality": null, "est_usd": 0.21, "cost_usd": 0.21, "state": "settled"},
     {"id": "3", "at": 1789997500, "month": "2026-09", "day": "2026-09-21", "kind": "plate", "subject": "s3", "auto": true, "model": "m", "quality": null, "est_usd": 0.21, "cost_usd": 0.21, "state": "settled"},
     {"id": "4", "at": 1789998000, "month": "2026-09", "day": "2026-09-21", "kind": "collage", "subject": "d4", "auto": true, "model": "m", "quality": null, "est_usd": 0.21, "cost_usd": 0.21, "state": "settled"},
     {"id": "5", "at": 1789998500, "month": "2026-09", "day": "2026-09-21", "kind": "collage", "subject": "d5", "auto": true, "model": "m", "quality": null, "est_usd": 0.21, "cost_usd": null, "state": "open"},
     {"id": "6", "at": 1789999000, "month": "2026-09", "day": "2026-09-21", "kind": "plate", "subject": "s6", "auto": true, "model": "m", "quality": null, "est_usd": 0.21, "cost_usd": 0.21, "state": "settled"}],
   "paused": false, "resumed_at": 0,
   "rec": {"id": "n", "at": 1790000000, "month": "2026-09", "day": "2026-09-21", "kind": "plate", "subject": "s7", "auto": true, "model": "m", "quality": null, "est_usd": 0.21, "cost_usd": null, "state": "open"},
   "rule": {"limit_usd": 100, "runaway_per_hour": 6, "window_s": null}, "now": 1790000000, "expect": "runaway"},
  {"name": "briefs and owner actions do not count toward the pause",
   "rows": [
     {"id": "1", "at": 1789996500, "month": "2026-09", "day": "2026-09-21", "kind": "describe", "subject": "s1", "auto": true, "model": "m", "quality": null, "est_usd": 0.002, "cost_usd": 0.002, "state": "settled"},
     {"id": "2", "at": 1789997000, "month": "2026-09", "day": "2026-09-21", "kind": "describe", "subject": "s2", "auto": true, "model": "m", "quality": null, "est_usd": 0.002, "cost_usd": 0.002, "state": "settled"},
     {"id": "3", "at": 1789997500, "month": "2026-09", "day": "2026-09-21", "kind": "weather", "subject": "d", "auto": true, "model": "m", "quality": null, "est_usd": 0.012, "cost_usd": 0.012, "state": "settled"},
     {"id": "4", "at": 1789998000, "month": "2026-09", "day": "2026-09-21", "kind": "collage", "subject": "d4", "auto": false, "model": "m", "quality": null, "est_usd": 0.21, "cost_usd": 0.21, "state": "settled"},
     {"id": "5", "at": 1789998500, "month": "2026-09", "day": "2026-09-21", "kind": "plate", "subject": "s5", "auto": false, "model": "m", "quality": null, "est_usd": 0.21, "cost_usd": 0.21, "state": "settled"},
     {"id": "6", "at": 1789999000, "month": "2026-09", "day": "2026-09-21", "kind": "plate", "subject": "s6", "auto": true, "model": "m", "quality": null, "est_usd": 0.21, "cost_usd": 0.0, "state": "released"}],
   "paused": false, "resumed_at": 0,
   "rec": {"id": "n", "at": 1790000000, "month": "2026-09", "day": "2026-09-21", "kind": "plate", "subject": "s7", "auto": true, "model": "m", "quality": null, "est_usd": 0.21, "cost_usd": null, "state": "open"},
   "rule": {"limit_usd": 100, "runaway_per_hour": 6, "window_s": null}, "now": 1790000000, "expect": null},
  {"name": "after a resume only later purchases count",
   "rows": [
     {"id": "1", "at": 1789996500, "month": "2026-09", "day": "2026-09-21", "kind": "plate", "subject": "s1", "auto": true, "model": "m", "quality": null, "est_usd": 0.21, "cost_usd": 0.21, "state": "settled"},
     {"id": "2", "at": 1789997000, "month": "2026-09", "day": "2026-09-21", "kind": "plate", "subject": "s2", "auto": true, "model": "m", "quality": null, "est_usd": 0.21, "cost_usd": 0.21, "state": "settled"},
     {"id": "3", "at": 1789997500, "month": "2026-09", "day": "2026-09-21", "kind": "plate", "subject": "s3", "auto": true, "model": "m", "quality": null, "est_usd": 0.21, "cost_usd": 0.21, "state": "settled"},
     {"id": "4", "at": 1789998000, "month": "2026-09", "day": "2026-09-21", "kind": "plate", "subject": "s4", "auto": true, "model": "m", "quality": null, "est_usd": 0.21, "cost_usd": 0.21, "state": "settled"},
     {"id": "5", "at": 1789998500, "month": "2026-09", "day": "2026-09-21", "kind": "plate", "subject": "s5", "auto": true, "model": "m", "quality": null, "est_usd": 0.21, "cost_usd": 0.21, "state": "settled"},
     {"id": "6", "at": 1789999000, "month": "2026-09", "day": "2026-09-21", "kind": "plate", "subject": "s6", "auto": true, "model": "m", "quality": null, "est_usd": 0.21, "cost_usd": 0.21, "state": "settled"}],
   "paused": false, "resumed_at": 1789999500,
   "rec": {"id": "n", "at": 1790000000, "month": "2026-09", "day": "2026-09-21", "kind": "plate", "subject": "s7", "auto": true, "model": "m", "quality": null, "est_usd": 0.21, "cost_usd": null, "state": "open"},
   "rule": {"limit_usd": 100, "runaway_per_hour": 6, "window_s": null}, "now": 1790000000, "expect": null},
  {"name": "a free local model is never over the limit",
   "rows": [
     {"id": "a", "at": 1789900000, "month": "2026-09", "day": "2026-09-20", "kind": "plate", "subject": "x", "auto": true, "model": "m", "quality": null, "est_usd": 10, "cost_usd": 10, "state": "settled"}],
   "paused": false, "resumed_at": 0,
   "rec": {"id": "n", "at": 1790000000, "month": "2026-09", "day": "2026-09-21", "kind": "plate", "subject": "y", "auto": true, "model": "a1111", "quality": null, "est_usd": 0, "cost_usd": null, "state": "open"},
   "rule": {"limit_usd": 10, "runaway_per_hour": 6, "window_s": null}, "now": 1790000000, "expect": null}
]
```

- [ ] **Step 2: Write the failing tests**

Create `server/tests/test_spend.py`:

```python
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
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_spend.py -q`
Expected: FAIL at import, `ModuleNotFoundError: No module named 'featherframe.spend'`.

- [ ] **Step 4: Write `spend.py`**

Create `server/featherframe/spend.py`:

```python
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
import logging
import re
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Iterator, Optional

import requests

log = logging.getLogger("featherframe.spend")

#: More automatic image purchases than this in a rolling hour pauses AI
#: generation until the owner resumes it.
RUNAWAY_PER_HOUR = 6
#: The kinds the runaway counts: a day's collage can need a dozen briefs.
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
    base = IMAGE_USD.get(quality or "", UNMEASURED_USD) if m.startswith("gpt-image") \
        else UNMEASURED_USD
    return round(base * (COLLAGE_FACTOR if kind == "collage" else 1.0), 4)


def vendor_refused(exc: BaseException) -> bool:
    """True when the vendor answered no before doing the work, so nothing was
    billed. Anything else may have been."""
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
    if rule.runaway_per_hour and rec.kind in RUNAWAY_KINDS:
        since = max(now - 3600.0, resumed_at)
        recent = [r for r in rows if r.auto and r.kind in RUNAWAY_KINDS
                  and r.state != "released" and r.at > since]
        if len(recent) >= rule.runaway_per_hour:
            return "runaway"
    return None


# -- the fence ---------------------------------------------------------------
_local = threading.local()


def active() -> Optional["Purchase"]:
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

    @classmethod
    def unlimited(cls) -> "Gate":
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
        days' automatic image purchases by kind (for the cost projection)."""
        now = self._now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        since = min(month_start, now - timedelta(days=30)).timestamp()
        snap = self.store.snapshot(since)
        month = now.strftime("%Y-%m")
        cut = (now - timedelta(days=30)).timestamp()
        recent = [r for r in snap.rows if r.auto and r.kind in RUNAWAY_KINDS
                  and r.state != "released" and r.at >= cut]
        first = min((r.at for r in recent), default=None)
        span = 0 if first is None else max(1, min(30, int((now.timestamp() - first) // 86400) + 1))
        return {
            "month": month,
            "usd": round(sum(_spent(r) for r in snap.rows if r.month == month), 4),
            "limit": float(self._limit()),
            "count": sum(1 for r in snap.rows if r.month == month and r.state != "released"),
            "paused": snap.pause,
            "by_kind_30d": {k: sum(1 for r in recent if r.kind == k) for k in RUNAWAY_KINDS
                            if any(r.kind == k for r in recent)},
            "span_days": span,
        }

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
```

Note on `MemoryStore.reserve`: it passes every row to `decide()`; `LocalStore` (Task 2) passes only this month's and the last 36 hours', which is all `decide()` reads.

- [ ] **Step 5: Run the tests to see them pass**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_spend.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add server/featherframe/spend.py server/tests/test_spend.py server/tests/fixtures/spend-cases.json
git commit -m "A gate that checks, records and settles every paid AI call (W-938)" -m "Refs: W-938"
```

---

### Task 2: Records that survive a restart (`LocalStore`)

**Files:**
- Modify: `server/featherframe/db.py` (`_init_schema`, three new methods)
- Modify: `server/featherframe/spend.py` (add `LocalStore`)
- Test: `server/tests/test_spend_store.py`

**Interfaces:**
- Consumes: `spend.Record`, `spend.Rule`, `spend.Snapshot`, `spend.decide` (Task 1).
- Produces:
  - `Database.spend_reserve(row: dict, since: float, decide: Callable[[list[dict]], Optional[str]]) -> Optional[str]` — under the DB lock: reads rows with `at >= since` or `month = row["month"]`, calls `decide`, inserts when it returns None, commits.
  - `Database.spend_settle(rec_id: str, state: str, cost_usd: Optional[float], usage: Optional[dict], at: float) -> None`
  - `Database.spend_rows(since: float) -> list[dict]`
  - `spend.LocalStore(db, ledger_path: Optional[Path] = None)` with the same four methods as `MemoryStore`; on first use it imports the old `spend.jsonl` once.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_spend_store.py`:

```python
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
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_spend_store.py -q`
Expected: FAIL, `AttributeError: module 'featherframe.spend' has no attribute 'LocalStore'`.

- [ ] **Step 3: Add the table and methods to `db.py`**

In `Database._init_schema`, add to the `executescript` string, after `battery_log`:

```sql
                CREATE TABLE IF NOT EXISTS spend (
                    id         TEXT PRIMARY KEY,
                    at         REAL NOT NULL,
                    month      TEXT NOT NULL,
                    day        TEXT NOT NULL,
                    kind       TEXT NOT NULL,
                    subject    TEXT NOT NULL,
                    auto       INTEGER NOT NULL,
                    model      TEXT,
                    quality    TEXT,
                    est_usd    REAL NOT NULL,
                    cost_usd   REAL,
                    usage      TEXT,
                    state      TEXT NOT NULL,
                    settled_at REAL
                );
                CREATE INDEX IF NOT EXISTS spend_at ON spend(at);
                CREATE INDEX IF NOT EXISTS spend_month ON spend(month);
```

Add after the battery log section (add `Callable, Optional` to the `typing` import):

```python
    # -- AI spend (W-938) --------------------------------------------------
    _SPEND_COLS = ("id", "at", "month", "day", "kind", "subject", "auto", "model",
                   "quality", "est_usd", "cost_usd", "state")

    def spend_rows(self, since: float, month: Optional[str] = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, at, month, day, kind, subject, auto, model, quality, est_usd, "
                "cost_usd, state FROM spend WHERE at >= ? OR month = ? ORDER BY at",
                (float(since), month or "")).fetchall()
        return [{**dict(r), "auto": bool(r["auto"])} for r in rows]

    def spend_reserve(self, row: dict[str, Any], since: float,
                      decide: Callable[[list[dict[str, Any]]], Optional[str]]) -> Optional[str]:
        """Run `decide` on the records it needs and insert `row` if it says
        yes, as one step: two threads can't both pass the same check."""
        with self._lock:
            reason = decide(self.spend_rows(since, row["month"]))
            if reason is None:
                self._conn.execute(
                    "INSERT INTO spend(id, at, month, day, kind, subject, auto, model, quality, "
                    "est_usd, cost_usd, state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    tuple(int(row[c]) if c == "auto" else row[c] for c in self._SPEND_COLS))
                self._conn.commit()
            return reason

    def spend_settle(self, rec_id: str, state: str, cost_usd: Optional[float],
                     usage: Optional[dict], at: float) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE spend SET state=?, cost_usd=?, usage=?, settled_at=? WHERE id=?",
                (state, cost_usd, json.dumps(usage) if usage is not None else None, at, rec_id))
            self._conn.commit()
```

- [ ] **Step 4: Add `LocalStore` to `spend.py`**

Add `import json`, `import time`, `from dataclasses import asdict` and `from pathlib import Path` to the imports, then append:

```python
_PAUSE_KEY = "ai_pause"
_RESUMED_KEY = "ai_resumed_at"
_IMPORTED_KEY = "spend_ledger_imported"
#: The longest window `decide()` reads back (the nightly collage's).
_LOOKBACK_S = 36 * 3600.0


class LocalStore:
    """The records in our own SQLite (`db.Database`). On Cloud this DB
    reaches the front door after every tick; part 2 moves the count there."""

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
        self._db.set(_PAUSE_KEY, None)
        self._db.set(_RESUMED_KEY, now)

    def _import_ledger(self, path: Path) -> None:
        """The W-859 ledger (`spend.jsonl`), carried over once as settled
        records so this month's spend counts toward the limit."""
        try:
            lines = path.read_text().splitlines()
        except OSError:
            lines = []
        for n, raw in enumerate(lines):
            try:
                e = json.loads(raw)
                at = datetime.fromisoformat(str(e["at"])).astimezone()
            except (ValueError, KeyError, TypeError):
                continue
            kind = str(e.get("kind") or "plate")
            est = estimate_usd(kind, e.get("model"), e.get("quality"))
            cost = e.get("cost_usd")
            row = asdict(Record(id=f"ledger-{n}", at=at.timestamp(), month=at.strftime("%Y-%m"),
                                day=at.strftime("%Y-%m-%d"), kind=kind,
                                subject=str(e.get("subject") or ""), auto=True,
                                model=str(e.get("model") or "unknown"),
                                quality=e.get("quality"), est_usd=est,
                                cost_usd=float(cost) if isinstance(cost, (int, float)) else est,
                                state="settled"))
            self._db.spend_reserve(row, 0.0, lambda rows: None)
        self._db.set(_IMPORTED_KEY, True)
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_spend_store.py tests/test_spend.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add server/featherframe/db.py server/featherframe/spend.py server/tests/test_spend_store.py
git commit -m "AI purchases are recorded in the server's own database before they are made (W-938)" -m "Refs: W-938"
```

---

### Task 3: Fence every paid model method

**Files:**
- Modify: `server/featherframe/render/genart.py` (`TextModel`, `ImageModel`)
- Modify: `server/tests/conftest.py` (a `metered` fixture)
- Modify: `server/tests/test_image_models.py`, `server/tests/test_usage_cost.py`, `server/tests/test_season.py` (direct calls)
- Test: `server/tests/test_spend_fence.py`

**Interfaces:**
- Consumes: `spend.fenced`, `spend.Gate.unlimited` (Task 1).
- Produces: every subclass of `genart.ImageModel` has a fenced `generate`; every subclass of `genart.TextModel` has fenced `complete_json` and `search_json` (when it defines them). The pytest fixture `metered` opens a purchase for tests that call a model directly.

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_spend_fence.py`:

```python
"""A paid model method runs only inside a purchase (W-938): a call site that
forgets the gate fails here, never in an owner's account."""
from __future__ import annotations

import pytest

from featherframe import spend
from featherframe.render import genart

PAID = [
    (genart.OpenAIImageModel, ("k",), "generate", ("p", "1024x1024", [])),
    (genart.GeminiImageModel, ("k",), "generate", ("p", "1024x1024", [])),
    (genart.ReplicateImageModel, ("k",), "generate", ("p", "1024x1024", [])),
    (genart.A1111ImageModel, ("http://gpu:7860",), "generate", ("p", "1024x1024", [])),
    (genart.OpenAITextModel, ("k",), "complete_json", ("brief",)),
    (genart.OpenAITextModel, ("k",), "search_json", ("weather",)),
    (genart.GeminiTextModel, ("k",), "complete_json", ("brief",)),
    (genart.AnthropicTextModel, ("k",), "complete_json", ("brief",)),
    (genart.LocalTextModel, ("http://gpu:11434",), "complete_json", ("brief",)),
]


@pytest.mark.parametrize("cls, args, method, call", PAID,
                         ids=[f"{c.__name__}.{m}" for c, _, m, _ in PAID])
def test_every_paid_method_is_fenced(cls, args, method, call, monkeypatch):
    def no_network(*a, **kw):
        raise AssertionError("reached the network outside a purchase")
    monkeypatch.setattr(genart.requests, "post", no_network)
    with pytest.raises(spend.Unguarded):
        getattr(cls(*args), method)(*call)


def test_a_new_model_class_is_fenced_too():
    class Later(genart.ImageModel):
        def generate(self, prompt, size, refs):
            return b"png"
    with pytest.raises(spend.Unguarded):
        Later().generate("p", "1x1", [])
    with spend.Gate.unlimited().purchase("plate", "x", model="later"):
        assert Later().generate("p", "1x1", []) == b"png"
```

- [ ] **Step 2: Run it to see it fail**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_spend_fence.py -q`
Expected: FAIL (`AssertionError: reached the network outside a purchase` and `DID NOT RAISE`).

- [ ] **Step 3: Fence the base classes**

In `server/featherframe/render/genart.py`, add `from .. import spend` beside the other package imports. In `class TextModel(ABC)`, after the `last_usage` line:

```python
    def __init_subclass__(cls, **kwargs):
        # Every paid call goes through the spend gate (W-938): a subclass's
        # own methods are fenced the moment it is defined.
        super().__init_subclass__(**kwargs)
        for name in ("complete_json", "search_json"):
            if name in cls.__dict__:
                setattr(cls, name, spend.fenced(cls.__dict__[name]))
```

In `class ImageModel(ABC)`, after its `last_usage` line:

```python
    def __init_subclass__(cls, **kwargs):
        # Every paid call goes through the spend gate (W-938).
        super().__init_subclass__(**kwargs)
        if "generate" in cls.__dict__:
            cls.generate = spend.fenced(cls.__dict__["generate"])
```

- [ ] **Step 4: Run the fence test to see it pass**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_spend_fence.py -q`
Expected: PASS.

- [ ] **Step 5: Give direct-call tests a purchase**

In `server/tests/conftest.py`, append:

```python
@pytest.fixture
def metered():
    """One open purchase, for a test that calls a paid model method directly
    (W-938: outside a purchase it raises spend.Unguarded)."""
    from featherframe import spend
    with spend.Gate.unlimited().purchase("plate", "test", model="test") as p:
        yield p
```

Add near the top of each of these files (after the imports):

```python
pytestmark = pytest.mark.usefixtures("metered")
```

- `server/tests/test_image_models.py`
- `server/tests/test_usage_cost.py`

In `server/tests/test_season.py` add the fixture only to the test that calls `search_json` on a real model directly (the one with `assert m.search_json("?") == …`, about line 245): give that test function a `metered` parameter.

- [ ] **Step 6: Run the touched files**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_image_models.py tests/test_usage_cost.py tests/test_season.py tests/test_spend_fence.py -q`
Expected: PASS, except the two ledger tests in `test_usage_cost.py` and `test_daily_weather_asks_the_text_model_once` in `test_season.py`, which Task 4 rewrites. If any other test fails with `spend.Unguarded`, it calls a model directly: give it the `metered` fixture.

- [ ] **Step 7: Commit**

```bash
git add server/featherframe/render/genart.py server/tests/conftest.py server/tests/test_spend_fence.py server/tests/test_image_models.py server/tests/test_usage_cost.py server/tests/test_season.py
git commit -m "A paid model call outside the spend gate fails instead of spending (W-938)" -m "Refs: W-938"
```

---

### Task 4: Illustrations, collages, briefs and weather go through the gate

**Files:**
- Modify: `server/featherframe/render/genart.py` (`GeneratedArtProvider.__init__`, `artwork`, `_describe_locked`, `day_composite`, `_day_weather`, `_generate_to_cache`; remove `record_spend`, `spend_for_month`, `_LEDGER_LOCK`)
- Modify: `server/tests/test_usage_cost.py` (ledger tests), `server/tests/test_season.py` (weather ledger asserts), and any test the open-record hold breaks (Step 6)
- Test: `server/tests/test_spend_genart.py`

**Interfaces:**
- Consumes: `spend.Gate`, `spend.Refused` (Task 1); fenced models (Task 3).
- Produces:
  - `GeneratedArtProvider(model, cache_dir=None, refs=None, cooldown_s=900.0, text_model=None, failures=None, gate=None)`; `gate=None` means `spend.Gate.unlimited()`. Attributes `.gate` and `.buy_new: bool = True` (False: never buy a new illustration; cached ones still serve).
  - `GeneratedArtProvider.day_composite(cells, when, force=False, southern=False, branch="season", location=None, auto=True, nightly=False, interval_s=None)`
  - `genart.NIGHTLY_WINDOW_S = 36 * 3600.0`

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_spend_genart.py`:

```python
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
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_spend_genart.py -q`
Expected: FAIL, `TypeError: __init__() got an unexpected keyword argument 'gate'`.

- [ ] **Step 3: Wire the gate into the provider**

In `GeneratedArtProvider.__init__`, add the parameter `gate: Optional["spend.Gate"] = None` after `failures`, and in the body:

```python
        # Every paid call goes through this gate (W-938). The service passes
        # its own; tests and tools get one with no limit.
        self.gate = gate if gate is not None else spend.Gate.unlimited()
        # Off: a species with no illustration is never bought one (the
        # Illustrations switch); what is kept still shows.
        self.buy_new = True
```

In `artwork()`, change `if self._model is None:` to:

```python
            if self._model is None or not self.buy_new:
```

Add a module-level constant beside `_KEEP_SHEETS` (or near `day_composite`):

```python
#: The nightly collage is bought at most once a date (W-938).
NIGHTLY_WINDOW_S = 36 * 3600.0
```

Add a helper method on `GeneratedArtProvider` (near `_in_cooldown`):

```python
    def _image_quality(self) -> Optional[str]:
        return (getattr(self._model, "effective_quality", None)
                or getattr(self._model, "quality", None))
```

- [ ] **Step 4: Route the four purchases**

1. **The brief.** In `_describe_locked`, replace the block from `subject = (...)` through the `record_spend("describe", …)` call with:

```python
        subject = (f"{common_name} ({scientific_name})"
                   if scientific_name else common_name)
        text_name = getattr(self._text_model, "name", "unknown")
        try:
            with self.gate.purchase("describe", key, model=text_name) as buy:
                out = self._text_model.complete_json(
                    DESCRIBE_PROMPT.format(subject=subject))
                usage = getattr(self._text_model, "last_usage", None)
                buy.settle(usage, estimate_text_cost_usd(text_name, usage))
            description = str(out.get("description", "")).strip()
            is_bird = bool(out.get("is_bird", True))
            plants = [p for p in (out.get("plants") or [])
                      if isinstance(p, dict) and p.get("name") and p.get("look")]
            if not plants and isinstance(hit, dict) and hit.get("plants"):
                # A re-buy that came back without a usable pool must not
                # destroy the pool the cache already had.
                plants = list(hit["plants"])
        except spend.Refused as r:
            log.info("brief for %s not bought: %s", subject, r.reason)
            return "", True, [], None
        except Exception as exc:
            log.warning("describe failed for %s (%s): %s", subject, text_name, exc)
            return "", True, [], None
```

Keep the lines after it (the cache write) as they are, except delete the now-duplicate `text_name = …` and `record_spend(…)` lines; `usage` is still in scope.

2. **The illustration.** In `_generate_to_cache`, replace from `started = time.time()` through the `record_spend("plate", …)` call with:

```python
            started = time.time()
            model_name = getattr(self._model, "name", "unknown")
            try:
                with self.gate.purchase("plate", slug, model=model_name,
                                        quality=self._image_quality(), auto=not force) as buy:
                    png_bytes = self._model.generate(prompt, GEN_SIZE, refs)
                    image_usage = getattr(self._model, "last_usage", None)
                    buy.settle(image_usage, estimate_cost_usd(model_name, image_usage))
                # Validate before caching: a corrupt cache would wedge forever.
                Image.open(io.BytesIO(png_bytes)).verify()
            except spend.Refused as r:
                log.info("illustration for %s not bought: %s", scientific_name, r.reason)
                return False
            except Exception as exc:
                self._failed_at[slug] = time.time()
                self._report(exc)
                log.warning("generation failed for %s (%s): %s",
                            scientific_name, model_name, exc)
                return False
            self._report(None)
```

Delete the old `model_name = …` / `image_usage = …` lines that followed; both names are now bound above.

3. **The collage.** Change the signature of `day_composite` to:

```python
    def day_composite(self, cells, when, force: bool = False, southern: bool = False,
                      branch: str = "season", location=None, auto: bool = True,
                      nightly: bool = False, interval_s: Optional[float] = None):
```

Add to its docstring: "`auto` is False for an owner's own action. `nightly` and `interval_s` name the purchase's subject and its window at the spend gate (W-938): one automatic sheet per collage interval for a date, plus one nightly sheet." Inside `with _GEN_LOCK:`, replace from `started = time.time()` through the `record_spend("collage", …)` call with:

```python
                started = time.time()
                size = "%dx%d" % sheet_art_size(cells)  # the sheet's own art box
                model_name = getattr(self._model, "name", "unknown")
                subject = f"{day}/nightly" if nightly else day
                window = (NIGHTLY_WINDOW_S if nightly
                          else max(0.0, interval_s - 600.0) if interval_s else None)
                try:
                    with self.gate.purchase("collage", subject, model=model_name,
                                            quality=self._image_quality(),
                                            auto=auto and not force, window_s=window) as buy:
                        png_bytes = self._model.generate(prompt, size, refs)
                        image_usage = getattr(self._model, "last_usage", None)
                        buy.settle(image_usage, estimate_cost_usd(model_name, image_usage))
                    Image.open(io.BytesIO(png_bytes)).verify()
                except spend.Refused as r:
                    log.info("day composite for %s not bought: %s", day, r.reason)
                    return self._read_sheet(png, sidecar, cells, locked=True) if png.exists() else None
                except Exception as exc:
                    self._failed_at[key] = time.time()
                    self._report(exc)
                    log.warning("day composite failed for %s (%s): %s", day, model_name, exc)
                    # A failed repaint keeps showing the good sheet it meant
                    # to replace, rather than falling to the grid.
                    if png.exists():
                        return self._read_sheet(png, sidecar, cells, locked=True)
                    return None
                self._report(None)
```

Delete the old `model_name = …`, `image_usage = …` and `record_spend(…)` lines that followed. The sidecar payload below keeps using `model_name` and `image_usage`.

4. **The weather.** In `_day_weather`, replace from `lat, lon = location` through the `record_spend("weather", …)` block with:

```python
        lat, lon = location
        model_name = getattr(self._text_model, "name", "unknown")
        answer = None
        try:
            with self.gate.purchase("weather", day, model=model_name,
                                    window_s=weather_mod.REASK_S) as buy:
                answer = search(weather_mod.prompt(lat, lon, when))
                usage = getattr(self._text_model, "last_usage", None)
                buy.settle(usage, estimate_text_cost_usd(model_name, usage))
        except spend.Refused as r:
            log.info("weather for %s not asked: %s", day, r.reason)
            return None
        except Exception as exc:  # the season's own look stands
            log.info("weather for %s failed: %s", day, exc)
```

Keep the rest (`kind = weather_mod.kind_from_answer(answer)` onward).

5. **Remove the old ledger.** Delete `_LEDGER_LOCK`, `record_spend` and `spend_for_month` from `genart.py`. Leave `paths.spend_ledger_path()` (Task 2 imports from it).

- [ ] **Step 5: Rewrite the ledger tests**

In `server/tests/test_usage_cost.py`, replace `test_every_paid_call_lands_in_the_ledger_and_the_month_adds_up` and `test_an_unreadable_ledger_is_an_empty_month` with:

```python
# -- the spend records (W-859, W-938) -----------------------------------------
def test_every_paid_call_is_recorded_and_the_month_adds_up(data_dir):
    from datetime import date as _date, datetime as _dt
    from featherframe import spend as _spend
    from featherframe.render import genart as _g
    from featherframe.render.collage import CollageCell as _Cell

    class _Model(_g.ImageModel):
        name = "gpt-image-2.5-sunburst"
        quality = "medium"

        def generate(self, prompt, size, refs):
            from PIL import Image as _I
            import io as _io
            buf = _io.BytesIO()
            _I.new("RGB", (64, 96), "white").save(buf, "PNG")
            self.last_usage = {"input_tokens": 4000, "output_tokens": 400,
                               "input_text_tokens": 1600, "input_image_tokens": 2400}
            return buf.getvalue()

    gate = _spend.Gate(_spend.MemoryStore(), now=lambda: _dt(2026, 9, 24, 12))
    provider = _g.GeneratedArtProvider(_Model(), refs=[], gate=gate)
    cells = [_Cell("Blue Jay", "Cyanocitta cristata", 3),
             _Cell("Carolina Wren", "Thryothorus ludovicianus", 2)]
    assert provider.day_composite(cells, _date(2026, 9, 24)) is not None
    assert provider._generate_to_cache("tyto-alba", "Barn Owl", "Tyto alba")
    import json as _json
    from featherframe import paths as _paths
    sidecar = _paths.collages_dir() / "2026-09-24.json"
    meta = _json.loads(sidecar.read_text())
    meta["created_ts"] = 0  # past the repaint debounce
    sidecar.write_text(_json.dumps(meta))
    provider.day_composite(cells, _date(2026, 9, 24), force=True)

    rows = gate.store.snapshot(0).rows
    assert [r.kind for r in rows] == ["collage", "plate", "collage"]
    one = (1600 * 5 + 2400 * 8 + 400 * 30) / 1e6
    assert abs(gate.summary()["usd"] - 3 * one) < 1e-6
```

In `server/tests/test_season.py`, in `test_daily_weather_asks_the_text_model_once`, replace the last two lines (the `spend = …` list and the `spend_for_month` assert) with:

```python
    kinds = [r.kind for r in provider.gate.store.snapshot(0).rows]
    assert kinds.count("weather") == 1
```

- [ ] **Step 6: Run the whole suite and fix what the open-record hold changes**

Run: `cd server && ./.venv/bin/python -m pytest -q`

Expected: `test_spend_genart.py` passes. Some older tests may now fail because a fake that raises a plain exception (`FakeModel(fail=True)`, a `RuntimeError`) leaves an open record that holds the subject for a day, so a later retry in the same test is refused. For each such failure:
- If the test is about the 15-minute cooldown or a retry after an error, change its fake to raise `GenerationError("HTTP 400: refused")` (a vendor refusal: released, no hold), and keep its assertions.
- If the test is about something else and a retry is incidental, give its provider `gate=spend.Gate.unlimited()` and clear the hold by constructing a new `Gate.unlimited()` before the retry.
Do not loosen `decide()` to make an old test pass.

Then run again: `cd server && ./.venv/bin/python -m pytest -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add server/featherframe/render/genart.py server/tests/
git commit -m "Illustrations, collages, briefs and weather are bought only through the spend gate (W-938)" -m "Refs: W-938"
```

---

### Task 5: The master switch and the monthly limit in `Config`

**Files:**
- Modify: `server/featherframe/config.py`
- Test: `server/tests/test_spend_service.py` (created here; Task 6 adds to it)

**Interfaces:**
- Produces: `Config.illustrations_generated: bool = True`, `Config.ai_monthly_limit_usd: int = 10`; `imagegen_enabled` keeps its name and becomes the master switch. `Config.from_dict` copies a stored `imagegen_enabled` into `illustrations_generated` when the latter is absent.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_spend_service.py`:

```python
"""The spend guards as the server and the webapp see them (W-938)."""
from __future__ import annotations

from featherframe.config import Config


def test_the_missing_species_switch_takes_the_old_value():
    off = Config.from_dict({"imagegen_enabled": False})
    assert off.illustrations_generated is False and off.imagegen_enabled is False
    on = Config.from_dict({"imagegen_enabled": True})
    assert on.illustrations_generated is True and on.imagegen_enabled is True
    kept = Config.from_dict({"imagegen_enabled": True, "illustrations_generated": False})
    assert kept.illustrations_generated is False


def test_the_limit_is_whole_dollars_from_1_to_1000():
    assert Config().ai_monthly_limit_usd == 10
    assert Config(ai_monthly_limit_usd=0).sanitize().ai_monthly_limit_usd == 1
    assert Config(ai_monthly_limit_usd=5000).sanitize().ai_monthly_limit_usd == 1000
    assert Config(ai_monthly_limit_usd="12.7").sanitize().ai_monthly_limit_usd == 12
    assert Config(ai_monthly_limit_usd="x").sanitize().ai_monthly_limit_usd == 10
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_spend_service.py -q`
Expected: FAIL, `AttributeError: 'Config' object has no attribute 'illustrations_generated'`.

- [ ] **Step 3: Add the fields, sanitize and migrate**

In `config.py`, change the comment above `imagegen_enabled` and add two fields after it:

```python
    # AI image generation, one switch (W-938): off, nothing paid is called;
    # the provider, key, model, quality and limit stay stored, and what was
    # generated keeps showing.
    imagegen_enabled: bool = True
    # The Illustrations section's "Generate images for missing species".
    illustrations_generated: bool = True
    # Nothing is bought past this in a calendar month (server's time zone).
    ai_monthly_limit_usd: int = 10
```

In `sanitize()`, after the `imagegen_text_base_url` line:

```python
        self.illustrations_generated = bool(self.illustrations_generated)
        self.ai_monthly_limit_usd = int(_clamp(_finite(self.ai_monthly_limit_usd, 10), 1, 1000))
```

In `from_dict()`, after the `ingest_token` migration:

```python
        # The missing-species switch was imagegen_enabled itself, which also
        # turned off every model; imagegen_enabled is now the master (W-938).
        # Both keep the stored value, so no migration starts a purchase.
        if "illustrations_generated" not in data and "imagegen_enabled" in data:
            data = {**data, "illustrations_generated": data["imagegen_enabled"]}
```

- [ ] **Step 4: Run them to see them pass**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_spend_service.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/featherframe/config.py server/tests/test_spend_service.py
git commit -m "AI image generation is one switch, with a monthly limit beside it (W-938)" -m "Refs: W-938"
```

---

### Task 6: The service builds the gate and says where it stands

**Files:**
- Modify: `server/featherframe/service.py`
- Test: `server/tests/test_spend_service.py` (add)

**Interfaces:**
- Consumes: `spend.Gate`, `spend.LocalStore`, `spend.estimate_usd` (Tasks 1–2); `GeneratedArtProvider(gate=…)`, `.buy_new`, `day_composite(auto=, nightly=, interval_s=)` (Task 4); `Config.illustrations_generated`, `Config.ai_monthly_limit_usd` (Task 5).
- Produces:
  - `FeatherframeService.spend_gate: spend.Gate`
  - `FeatherframeService.ai_view(now: datetime) -> dict` with keys `state` (`good|warn|bad|off`), `summary`, `notice` (`None|"error"|"paused"|"limit"`), `text`, `detail`, `usd`, `limit`, `by_kind_30d`, `span_days`
  - `FeatherframeService.ai_refusal() -> Optional[str]` (`"off" | "paused" | "limit" | None`)
  - `FeatherframeService.resume_ai() -> None`
  - `status()["ai"]` = `ai_view(now)`

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_spend_service.py`:

```python
from datetime import datetime

import pytest

from featherframe import spend
from featherframe.config import save_config
from featherframe.db import Database
from tests.test_genart import FakeModel


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    monkeypatch.setenv("FEATHERFRAME_DB", str(tmp_path / "ff.db"))
    from featherframe.service import FeatherframeService
    db = Database(tmp_path / "ff.db")
    save_config(db, Config(imagegen_api_key="sk-proj-verysecretkey1234",
                           imagegen_quality="medium"))
    s = FeatherframeService(db)
    s._clock = lambda: datetime(2026, 10, 14, 12, 0)
    return s


def test_the_service_buys_through_its_own_durable_gate(svc):
    assert svc.genart.gate is svc.spend_gate
    assert isinstance(svc.spend_gate.store, spend.LocalStore)


def test_the_summary_says_provider_and_spend(svc):
    v = svc.ai_view(svc._clock())
    assert v["state"] == "good" and v["summary"] == "OpenAI · $0.00 of $10.00"


def test_off(svc):
    svc.config.imagegen_enabled = False
    assert svc.ai_view(svc._clock())["summary"] == "Off"
    assert svc.ai_refusal() == "off"


def test_limit_reached(svc):
    svc.config.ai_monthly_limit_usd = 1
    with svc.spend_gate.purchase("collage", "2026-10-14", model="m", auto=False) as p:
        p.settle(None, 0.99)
    v = svc.ai_view(svc._clock())
    assert v["summary"] == "Limit reached" and v["notice"] == "limit"
    assert v["text"] == ("This month's AI spend reached your $1.00 limit. New AI "
                         "illustrations and collages resume on 1 Nov, or raise the limit.")
    assert svc.ai_refusal() == "limit"
    assert svc._imagegen_glass_note() == "AI limit reached"


def test_paused_and_resumed(svc):
    svc.spend_gate.store.resume(0)
    svc.db.set("ai_pause", {"at": 1.0, "count": 6})
    v = svc.ai_view(svc._clock())
    assert v["summary"] == "Paused" and v["notice"] == "paused"
    assert v["text"] == ("AI generation is paused: 6 purchases in the last hour, more "
                         "than usual. Nothing more is bought until you resume.")
    assert svc._imagegen_glass_note() == "AI paused"
    svc.resume_ai()
    assert svc.ai_view(svc._clock())["notice"] is None


def test_the_missing_species_switch_reaches_the_provider(svc):
    svc.config.illustrations_generated = False
    save_config(svc.db, svc.config)
    svc.reload_config()
    assert svc.genart.buy_new is False


def test_status_carries_the_view(svc):
    assert svc.status()["ai"]["summary"] == "OpenAI · $0.00 of $10.00"
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_spend_service.py -q`
Expected: FAIL, `AttributeError: … 'spend_gate'`.

- [ ] **Step 3: Build the gate**

In `service.py`, add `from . import spend` beside the other package imports. In `FeatherframeService.__init__`, immediately before the first call to `self._build_provider(…)`, add:

```python
        # Every paid AI call goes through this gate (W-938).
        self.spend_gate = spend.Gate(
            spend.LocalStore(self.db),
            enabled=lambda: self.config.imagegen_enabled,
            limit_usd=lambda: self.config.ai_monthly_limit_usd,
            now=lambda: self._clock())
        self._collage_by_owner = False
```

In `_build_provider`, pass the gate and the switch:

```python
        self.genart = GeneratedArtProvider(make_image_model(config),
                                           text_model=make_text_model(config),
                                           failures=_SavedCooldowns(self.db),
                                           gate=self.spend_gate)
        self.genart.buy_new = config.illustrations_generated
```

In `reload_config`, after the `imagegen_changed` block, add:

```python
            self.genart.buy_new = new.illustrations_generated
```

And where it sets `self._collage_redraw = True`, add on the next line:

```python
                # The owner's save asked for it: not held to the interval.
                self._collage_by_owner = True
```

- [ ] **Step 4: Pass the collage's context**

Change `_build_collage` and `_collage_composer`:

```python
    def _build_collage(self, now: datetime, on_date: ddate,
                       force_generated: bool = False, owner: bool = False,
                       nightly: bool = False) -> bool:
        """Draw the collage picture for `on_date`. `owner`: the owner asked
        (a button, a settings save); `nightly`: the quiet-hours sheet."""
        owner = owner or self._collage_by_owner
        self._collage_by_owner = False
        composed = self._collage_composer(now, on_date, owner=owner, nightly=nightly)
```

(keep the rest of `_build_collage` as it is), and:

```python
    def _collage_composer(self, now: datetime, on_date: ddate, owner: bool = False,
                          nightly: bool = False):
```

with the `day_composite` call inside `compose` becoming:

```python
                sheet = self.genart.day_composite(top, on_date, force=force,
                                                  southern=southern,
                                                  branch=self.config.collage_branch,
                                                  location=loc,
                                                  auto=not (owner or force),
                                                  nightly=nightly,
                                                  interval_s=self.config.collage_interval_hours * 3600)
```

In `_maybe_quiet_collage`: `if self._build_collage(now, on_date, nightly=True):`. In `force_collage`: `return self._build_collage(now, on_date, force_generated=repaint, owner=True)`.

- [ ] **Step 5: The view, the refusal, resume, the footnote**

Add to `FeatherframeService`, beside `imagegen_error_view`:

```python
    def ai_refusal(self) -> Optional[str]:
        """Why an owner's repaint would be refused now, or None."""
        if not self.config.imagegen_enabled:
            return "off"
        s = self.spend_gate.summary()
        if s["paused"]:
            return "paused"
        est = spend.estimate_usd("plate", self.config.imagegen_model, self.config.imagegen_quality)
        if est > 0 and s["usd"] + est > s["limit"]:
            return "limit"
        return None

    def resume_ai(self) -> None:
        self.spend_gate.resume()

    def ai_view(self, now: datetime) -> dict:
        """The AI image generation row as the page shows it: one state, the
        summary, and the notice under it (W-938)."""
        name = _IMAGEGEN_NAMES.get(self.config.imagegen_provider, self.config.imagegen_provider)
        s = self.spend_gate.summary()
        out = {"usd": s["usd"], "limit": s["limit"], "by_kind_30d": s["by_kind_30d"],
               "span_days": s["span_days"], "notice": None, "text": "", "detail": ""}
        ready = bool(self.config.imagegen_api_key) or self.config.imagegen_provider == "a1111"
        if not self.config.imagegen_enabled:
            return {**out, "state": "off", "summary": "Off"}
        if not ready:
            return {**out, "state": "off", "summary": "No API key"}
        err = self.imagegen_error_view(now)
        if err and err["reason"] in ("key", "credits"):
            return {**out, "state": "bad", "summary": err["summary"], "notice": "error",
                    "text": err["text"], "detail": err["detail"]}
        if s["paused"]:
            n = int(s["paused"].get("count") or spend.RUNAWAY_PER_HOUR)
            return {**out, "state": "bad", "summary": "Paused", "notice": "paused",
                    "text": (f"AI generation is paused: {n} purchases in the last hour, "
                             "more than usual. Nothing more is bought until you resume.")}
        if self.ai_refusal() == "limit":
            first = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
            return {**out, "state": "bad", "summary": "Limit reached", "notice": "limit",
                    "text": (f"This month's AI spend reached your ${s['limit']:.2f} limit. "
                             f"New AI illustrations and collages resume on 1 {first:%b}, "
                             "or raise the limit.")}
        if err:
            return {**out, "state": err["state"], "summary": err["summary"], "notice": "error",
                    "text": err["text"], "detail": err["detail"]}
        return {**out, "state": "good",
                "summary": f"{name} · ${s['usd']:.2f} of ${s['limit']:.2f}"}
```

Ensure `timedelta` is imported from `datetime` at the top of `service.py`.

In `_imagegen_glass_note`, after the `if getattr(self.genart, "_model", None) is None: return None` check, add:

```python
        refusal = self.ai_refusal()
        if refusal == "paused":
            return "AI paused"
        if refusal == "limit":
            return "AI limit reached"
```

In `status()`, beside `"imagegen_error": self.imagegen_error_view(now),` add:

```python
            "ai": self.ai_view(now),
```

- [ ] **Step 6: Run the tests**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_spend_service.py -q`
Expected: PASS. Then the whole suite: `./.venv/bin/python -m pytest -q`, expected PASS.

- [ ] **Step 7: Commit**

```bash
git add server/featherframe/service.py server/tests/test_spend_service.py
git commit -m "The server buys through a durable gate and says when AI is off, paused or at its limit (W-938)" -m "Refs: W-938"
```

---

### Task 7: The 27 Sep replay

**Files:**
- Test: `server/tests/test_spend_replay.py`

**Interfaces:**
- Consumes: `spend.Gate`, `spend.MemoryStore`, `GeneratedArtProvider(gate=…)`, `day_composite(interval_s=…)`.

- [ ] **Step 1: Write the test**

Create `server/tests/test_spend_replay.py`:

```python
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
```

- [ ] **Step 2: Run it**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_spend_replay.py -q`
Expected: PASS (Tasks 1–4 already make it true). If it fails, fix the code, not the test.

- [ ] **Step 3: Commit**

```bash
git add server/tests/test_spend_replay.py
git commit -m "A test replays 27 Sep: a hundred stopped starts buy one collage (W-938)" -m "Refs: W-938"
```

---

### Task 8: The webapp's endpoints

**Files:**
- Modify: `server/featherframe/app.py` (settings handler, `/api/generated/regenerate`, `/api/collage/now`, index, new `/api/ai/resume`)
- Test: `server/tests/test_spend_page.py`

**Interfaces:**
- Consumes: `svc.ai_refusal()`, `svc.resume_ai()`, `svc.spend_gate.summary()` (Task 6); config fields (Task 5).
- Produces: `POST /api/ai/resume` → `{"ok": true}`; the index template gets `spend` = `svc.spend_gate.summary()`.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_spend_page.py`:

```python
"""The webapp's side of the spend guards (W-938)."""
from __future__ import annotations

from datetime import datetime

import pytest
from starlette.testclient import TestClient

from featherframe.config import Config, save_config
from featherframe.db import Database


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    monkeypatch.setenv("FEATHERFRAME_DB", str(tmp_path / "ff.db"))
    from featherframe.service import FeatherframeService
    db = Database(tmp_path / "ff.db")
    save_config(db, Config(imagegen_api_key="sk-proj-verysecretkey1234"))
    s = FeatherframeService(db)
    s._clock = lambda: datetime(2026, 10, 14, 12, 0)
    return s


@pytest.fixture
def client(svc):
    from featherframe.app import app
    app.state.service = svc
    return TestClient(app, raise_server_exceptions=False)


ORIGIN = {"Origin": "http://testserver"}


def test_settings_save_the_limit_and_the_switches(client, svc):
    r = client.post("/settings", data={"section": "imagegen", "ai_monthly_limit_usd": "25",
                                       "imagegen_enabled": ["0"]}, headers=ORIGIN)
    assert r.status_code in (200, 303)
    assert svc.config.ai_monthly_limit_usd == 25 and svc.config.imagegen_enabled is False
    client.post("/settings", data={"illustrations_generated": ["0"]}, headers=ORIGIN)
    assert svc.config.illustrations_generated is False
    assert svc.config.ai_monthly_limit_usd == 25          # a switch keeps the rest


def test_resume(client, svc):
    svc.db.set("ai_pause", {"at": 1.0, "count": 6})
    r = client.post("/api/ai/resume", headers=ORIGIN)
    assert r.json() == {"ok": True}
    assert svc.spend_gate.summary()["paused"] is None


@pytest.mark.parametrize("state, message", [
    ("off", "AI image generation is off."),
    ("paused", "AI generation is paused."),
    ("limit", "This month's AI limit is reached."),
])
def test_regenerate_says_why_it_is_refused(client, svc, monkeypatch, state, message):
    monkeypatch.setattr(svc, "ai_refusal", lambda: state)
    monkeypatch.setattr(svc, "generated_listing", lambda: [{"slug": "tyto-alba"}])
    r = client.post("/api/generated/regenerate", data={"slug": "tyto-alba"}, headers=ORIGIN)
    assert r.json() == {"ok": False, "error": message}


def test_a_collage_repaint_says_why_it_is_refused(client, svc, monkeypatch):
    monkeypatch.setattr(svc, "ai_refusal", lambda: "limit")
    r = client.post("/api/collage/now", data={"repaint": "1"}, headers=ORIGIN)
    assert r.status_code == 409
    assert r.json() == {"ok": False, "error": "this month's AI limit is reached"}
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_spend_page.py -q`
Expected: FAIL (the limit is not saved; `/api/ai/resume` is 404/405).

- [ ] **Step 3: The settings fields**

In the `/settings` handler's `Config(...)` call, after `imagegen_enabled=…`:

```python
        illustrations_generated=b("illustrations_generated", cur["illustrations_generated"]),
        ai_monthly_limit_usd=i("ai_monthly_limit_usd", cur["ai_monthly_limit_usd"]),
```

(Every field must be in this call: a field left out resets to its default on every save.)

- [ ] **Step 4: Refusals and resume**

Near the top of `app.py`'s route definitions (beside the other module constants), add:

```python
# What the page says when the spend gate would refuse an owner's repaint (W-938).
_AI_REFUSED = {"off": "AI image generation is off.",
               "paused": "AI generation is paused.",
               "limit": "This month's AI limit is reached."}
# The same for a collage repaint: the page prefixes "Could not start — ".
_AI_REFUSED_TASK = {"off": "AI image generation is off",
                    "paused": "AI generation is paused",
                    "limit": "this month's AI limit is reached"}
```

In `generated_regenerate`, replace the `elif not svc.config.imagegen_enabled:` branch with:

```python
        elif svc.ai_refusal():
            error = _AI_REFUSED[svc.ai_refusal()]
```

In `collage_now`, after `repaint = "repaint" in form`:

```python
    refusal = svc.ai_refusal() if (repaint and svc.config.collage_generated) else None
    if refusal:
        return JSONResponse({"ok": False, "error": _AI_REFUSED_TASK[refusal]}, status_code=409)
```

Add the resume route after `collage_now`:

```python
@app.post("/api/ai/resume")
async def ai_resume(request: Request):
    """The owner's Resume after a runaway pause (W-938)."""
    if not _same_origin(request):
        return _forbidden_cross_origin()
    await run_in_threadpool(_svc(request).resume_ai)
    return JSONResponse({"ok": True})
```

In `index()`, replace `spend = await run_in_threadpool(genart.spend_for_month)` with:

```python
    spend = await run_in_threadpool(svc.spend_gate.summary)
```

If `genart` is now unused in `app.py`, remove its import.

- [ ] **Step 5: Run the tests**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_spend_page.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add server/featherframe/app.py server/tests/test_spend_page.py
git commit -m "The webapp saves the limit, resumes a pause, and says why a repaint is refused (W-938)" -m "Refs: W-938"
```

---

### Task 9: The page

**Files:**
- Modify: `server/templates/index.html`
- Test: `server/tests/test_spend_page.py` (add)

**Interfaces:**
- Consumes: `status.ai` (Task 6), `spend` (Task 8), `config.illustrations_generated`, `config.ai_monthly_limit_usd`.

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_spend_page.py`:

```python
def _section(html: str, sid: str) -> str:
    return html.split(f'id="{sid}"')[1].split("</details>")[0]


def test_the_section_opens_with_its_switch_and_has_the_limit(client):
    html = _section(client.get("/").text, "set-imagegen")
    assert 'id="ai-on"' in html and ">AI image generation<" in html
    assert 'name="ai_monthly_limit_usd"' in html and ">Monthly limit<" in html
    assert "OpenAI · $0.00 of $10.00" in client.get("/").text


def test_off_locks_the_two_feature_switches(client, svc):
    svc.config.imagegen_enabled = False
    html = client.get("/").text
    assert "AI image generation is off" in _section(html, "set-illustrations")
    assert 'id="ig-summary">Off<' in html


def test_the_missing_species_switch_posts_its_own_field(client):
    html = _section(client.get("/").text, "set-illustrations")
    assert 'name="illustrations_generated"' in html and 'name="imagegen_enabled"' not in html


def test_paused_shows_the_notice_and_resume(client, svc):
    svc.db.set("ai_pause", {"at": 1.0, "count": 6})
    html = _section(client.get("/").text, "set-imagegen")
    assert "AI generation is paused: 6 purchases in the last hour" in html
    assert 'id="ai-resume"' in html and ">Resume<" in html


def test_the_quality_menu_carries_what_the_projection_needs(client):
    html = client.get("/").text
    assert 'id="ai-projection"' in html and "data-prices=" in html
```

Before writing the template, find the Illustrations section's id: `grep -n 'details class="disc set" id="set-' server/templates/index.html`. If it is not `set-illustrations`, use the real id in the two tests above.

- [ ] **Step 2: Run them to see them fail**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_spend_page.py -q`
Expected: the five new tests FAIL.

- [ ] **Step 3: Template variables**

Beside `{% set ig_ready = … %}` (about line 1409):

```jinja
    {% set ai_live = ig_ready and config.imagegen_enabled %}
    {% set ai_lock_note = '' if ai_live else ('Needs an API key' if not ig_ready else 'AI image generation is off') %}
    {% set ai = status.ai %}
```

- [ ] **Step 4: The two feature switches**

The Illustrations row (about line 1670): change `ig_ready` to `ai_live`, the hint text to `{{ ai_lock_note }}`, and the switch to post its own field:

```jinja
          <div class="frow toggle{{ '' if ai_live else ' locked' }}">
            <div class="frow-text"><label class="lab" for="ig"><span class="badge ai">AI</span>Generate images for missing species<a class="btn sm ex preview" href="/static/examples/generated.png" data-ex="generated" data-cap="A plate generated for a species no book has." target="_blank" rel="noopener"><svg viewBox="0 0 24 24" aria-hidden="true"><use href="#ic-search"/></svg>Example</a></label><div class="hint" data-needs-key>{{ ai_lock_note }}</div></div>
            <div class="frow-ctl">{{ instant_switch('ig', 'illustrations_generated', config.illustrations_generated, not ai_live) }}</div>
          </div>
```

The Collage row (about line 1735): the same three changes (`ai_live`, `{{ ai_lock_note }}`, `not ai_live`); its field stays `collage_generated`.

- [ ] **Step 5: The AI image generation section**

Replace lines from `{% set ig_err = status.imagegen_error %}` through the end of the `#ig-banner` div with:

```jinja
      {% set ig_state = ai.state %}
      <details class="disc set" id="set-imagegen" data-state="{{ ig_state }}" {{ 'open' if ig_state == 'bad' else '' }}>
        {% call set_summary('ic-sparkle', 'AI image generation') %}<span class="d {{ ig_state }}" id="ig-dot"></span><span id="ig-summary">{{ ai.summary }}</span>{% endcall %}
        <div class="disc-body"><div class="set-body" id="imagegen-card">
        <form class="set-form" method="post" action="/settings">
          <input type="hidden" name="section" value="imagegen">
          <button type="submit" class="default-submit" tabindex="-1" aria-hidden="true"></button>
          <p class="intro"><a href="https://featherframe.app/help/ai" target="_blank" rel="noopener">Learn</a> how image generation works, as well as estimated API costs.</p>
          <div class="test-box {{ ig_state }} src-alarm{{ ' show' if ai.notice else '' }}" id="ig-banner" role="status" aria-live="polite" data-provider-name="{{ ig_names.get(config.imagegen_provider, config.imagegen_provider) }}">
            <span class="test-ic" aria-hidden="true"><svg viewBox="0 0 16 16"><use href="#ic-warn"/></svg></span>
            <span class="test-msg" id="ig-text" title="{{ ai.detail }}">{{ ai.text }}</span>
            <button class="btn sm" type="button" id="ai-resume" {{ '' if ai.notice == 'paused' else 'hidden' }}>Resume</button>
          </div>
          <div class="frow toggle{{ '' if ig_ready else ' locked' }}">
            <div class="frow-text"><label class="lab" for="ai-on">AI image generation</label><div class="hint" data-needs-key-only>{{ '' if ig_ready else 'Needs an API key' }}</div></div>
            <div class="frow-ctl">{{ instant_switch('ai-on', 'imagegen_enabled', config.imagegen_enabled, not ig_ready) }}</div>
          </div>
```

Then run `grep -n "ig_err" server/templates/index.html`: any other use of `ig_err` reads `ai` instead (`ai.text`, `ai.detail`, `ai.notice`).

After the a1111 endpoint row and before `<details class="disc adv ig-needs-key" …>`, add the limit row:

```jinja
          <div class="frow ig-needs-key" {{ '' if ig_ready else 'hidden' }}>
            <div class="frow-text"><label class="lab" for="ai-limit">Monthly limit</label></div>
            <div class="frow-ctl"><span class="unit-pre" aria-hidden="true">$</span><input type="number" id="ai-limit" name="ai_monthly_limit_usd" min="1" max="1000" step="1" inputmode="numeric" required value="{{ config.ai_monthly_limit_usd }}"></div>
          </div>
```

Under the Quality row's `</select></div></div>` (inside that `.frow`, after `.frow-ctl`), add the projection line, and give the select the numbers it needs:

```jinja
                <div class="hint" id="ai-projection" hidden></div>
```

and on the `<select … id="ig-quality">` element add:

```jinja
data-plates="{{ ai.by_kind_30d.get('plate', 0) }}" data-collages="{{ ai.by_kind_30d.get('collage', 0) }}" data-days="{{ ai.span_days }}" data-prices='{{ {"low": 0.034, "medium": 0.039, "high": 0.070, "xhigh": 0.103, "max": 0.194} | tojson }}'
```

(These prices are `spend.IMAGE_USD`. If you prefer, pass `spend.IMAGE_USD` into the template context from `index()` as `ai_prices` and use `{{ ai_prices | tojson }}`; then a test should assert the two agree.)

In the style block, after `.frow-ctl input[type=time] { width:auto; }`:

```css
    .frow-ctl .unit-pre { margin-right:6px; color:var(--faint); }
```

- [ ] **Step 6: The Generated illustrations line**

Replace:

```jinja
      {% if spend is defined and spend.images %}
      <p class="hint gen-spend">This month: {{ spend.images }} image{{ '' if spend.images == 1 else 's' }}{% if spend.usd %}, ≈ ${{ '%.2f' | format(spend.usd) }}{% endif %}.</p>
      {% endif %}
```

with:

```jinja
      {% if spend is defined and spend.count %}
      <p class="hint gen-spend">This month: ${{ '%.2f' | format(spend.usd) }} of ${{ '%.2f' | format(spend.limit) }}.</p>
      {% endif %}
```

- [ ] **Step 7: The script**

Replace `lockAiToggles` and the click guard after it (about line 2916) with:

```js
  // -- the AI switches: locked, never disabled -------------------------------
  // Without a key every AI switch is shown dimmed and refuses to flip; with
  // the master switch off the two feature switches do too (W-938). NOT
  // disabled: a disabled checkbox posts nothing, so saving the form would
  // quietly store the setting off.
  var aiReady = false;
  function aiOn() { var m = $('ai-on'); return !m || m.checked; }
  function lockAiToggles(ready) {
    aiReady = ready;
    var master = $('ai-on');
    if (master) {
      var mrow = master.closest('.frow');
      if (mrow) mrow.classList.toggle('locked', !ready);
      master.setAttribute('aria-disabled', ready ? 'false' : 'true');
      var mnote = mrow && mrow.querySelector('[data-needs-key-only]');
      if (mnote) mnote.textContent = ready ? '' : 'Needs an API key';
    }
    var live = ready && aiOn();
    ['ig', 'cg'].forEach(function (id) {
      var input = $(id); if (!input) return;
      var row = input.closest('.frow');
      if (row) row.classList.toggle('locked', !live);
      input.setAttribute('aria-disabled', live ? 'false' : 'true');
      var note = row && row.querySelector('[data-needs-key]');
      if (note) note.textContent = live ? '' : (ready ? 'AI image generation is off' : 'Needs an API key');
    });
    var br = $('branch-row'); if (br) br.classList.toggle('locked', !live);
  }
  ['ai-on', 'ig', 'cg'].forEach(function (id) {
    var input = $(id); if (!input) return;
    function locked() { return input.getAttribute('aria-disabled') === 'true'; }
    input.addEventListener('click', function (e) { if (locked()) e.preventDefault(); });
    input.addEventListener('change', function () { if (locked()) input.checked = !input.checked; });
  });
  if ($('ai-on')) $('ai-on').addEventListener('change', function () { lockAiToggles(aiReady); });

  // -- Resume after a runaway pause (W-938) ------------------------------------
  if ($('ai-resume')) $('ai-resume').addEventListener('click', function () {
    var btn = $('ai-resume'); btn.disabled = true;
    jsonFetch('/api/ai/resume', { method: 'POST', credentials: 'same-origin' })
      .then(function () { pollStatus(); })
      .catch(function (e) { showFlash('bad', 'Could not resume — ' + errText(e) + '.', 'ic-warn'); })
      .then(function () { btn.disabled = false; });
  });

  // -- what the month will cost at the chosen quality (W-938) ------------------
  (function () {
    var sel = $('ig-quality'), line = $('ai-projection');
    if (!sel || !line) return;
    function update() {
      var prices = JSON.parse(sel.getAttribute('data-prices') || '{}');
      var price = prices[sel.value];
      var plates = +sel.getAttribute('data-plates') || 0;
      var collages = +sel.getAttribute('data-collages') || 0;
      var days = +sel.getAttribute('data-days') || 0;
      if (!price || !days || !(plates + collages)) { line.hidden = true; return; }
      var month = (plates * price + collages * price * 1.1) * 30 / days;
      line.textContent = 'About $' + (month < 10 ? month.toFixed(2) : Math.round(month)) +
        ' a month at this station’s pace.';
      line.hidden = false;
    }
    sel.addEventListener('change', update);
    update();
  })();
```

The status updater: replace the block from `// AI image generation: its last failure` through its closing `}` (about line 2395) with:

```js
    // AI image generation: its one state (W-938).
    var ai = st.ai, igBanner = $('ig-banner'), igDot = $('ig-dot');
    if (igBanner && ai) {
      igBanner.className = 'test-box ' + ai.state + ' src-alarm' + (ai.notice ? ' show' : '');
      setText('ig-text', ai.text || '');
      $('ig-text').title = ai.detail || '';
      $('set-imagegen').dataset.state = ai.state;
      setHidden('ai-resume', ai.notice !== 'paused');
      if (igDot) { igDot.className = 'd ' + ai.state; setText('ig-summary', ai.summary); }
    }
```

Check the names used: `pollStatus`, `setHidden`, `setText`, `showFlash`, `jsonFetch`, `errText` must exist in the page's script (`grep -n "function pollStatus\|function setHidden" server/templates/index.html`). If the status poller has another name, call that one.

The `.typo` apostrophe: the projection uses a curly apostrophe (`’`) as the page's other strings do; if `check_copy.py` asks for a straight one, use `'`.

- [ ] **Step 8: Run the tests and the copy check**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_spend_page.py tests/test_copy_style.py tests/test_page_scripts.py tests/test_imagegen_failure.py tests/test_no_key_page.py -q`
Expected: PASS. A failure in `test_imagegen_failure.py` or `test_no_key_page.py` that reads the old summary markup: update its assertion to the new strings (`status()["ai"]`, `id="ig-summary">…<`), never the copy.

- [ ] **Step 9: Look at it**

Start the dev server with `FEATHERFRAME_NO_MDNS=1` (AGENTS.md: a dev server never advertises), open the page in the browser pane, open Settings → AI image generation, and check: the switch row first; the limit row; the summary `OpenAI · $0.00 of $10.00`; flipping the switch off locks Illustrations' and Collage's AI switches with "AI image generation is off"; picking Max in Quality shows the projection line once there are purchases. Screenshot it for the PR.

- [ ] **Step 10: Commit**

```bash
git add server/templates/index.html server/tests/
git commit -m "The webapp's AI section: one switch, a monthly limit, and what each state means (W-938)" -m "Refs: W-938"
```

---

### Task 10: `boot_art.py` goes through a gate

**Files:**
- Modify: `firmware/tools/screens/boot_art.py`

**Interfaces:**
- Consumes: `spend.Gate`, `spend.LocalStore`, `spend.Refused`; `featherframe.db.Database`.

`featherframe/preview.py` builds `GeneratedArtProvider(None)` and never buys, so it needs no change.

- [ ] **Step 1: Wrap the call**

In `boot_art.py`, add to the imports `from featherframe import spend` and `from featherframe.db import Database`. After `model = genart.OpenAIImageModel(…)` add:

```python
    # Every paid call goes through a spend gate (W-938): this tool's records
    # go in a scratch DB beside its output, with no limit (Wells's own key).
    gate = spend.Gate(spend.LocalStore(Database(out / "spend.db"), ledger_path=out / "none.jsonl"),
                      limit_usd=lambda: float("inf"), runaway_per_hour=None)
```

and replace `png = model.generate(prompt, GEN_SIZE, step_refs)` with:

```python
            with gate.purchase("plate", f"boot-{step}", model=model.name,
                               quality=model.quality, auto=False) as buy:
                png = model.generate(prompt, GEN_SIZE, step_refs)
                buy.settle(model.last_usage,
                           genart.estimate_cost_usd(model.name, model.last_usage))
```

- [ ] **Step 2: Check it still parses and dry-runs**

Run: `cd firmware/tools/screens && ../../../server/.venv/bin/python boot_art.py --dry-run --step base`
Expected: prints the reference plates and the prompt; no network call.

- [ ] **Step 3: Commit**

```bash
git add firmware/tools/screens/boot_art.py
git commit -m "The boot art tool buys through the spend gate too (W-938)" -m "Refs: W-938"
```

---

### Task 11: Docs, the whole suite, the PR and the Cloud deploy

**Files:**
- Modify: `AGENTS.md`
- Modify: the wiki page `AI-illustrations.md` in a checkout of `https://github.com/wr/featherframe.wiki.git`
- Modify: `docs/superpowers/specs/2026-10-01-ai-spend-guards-design.md` (two lines that the plan settled differently)

- [ ] **Step 1: AGENTS.md**

In the **Render pipeline** paragraph, after the sentence ending "…with a per-species cooldown.", insert:

```markdown
**Every paid call goes through the spend gate (W-938, `spend.py`).**
`Gate.purchase(kind, subject, …)` checks (the master switch
`Config.imagegen_enabled`, the runaway pause, the monthly limit
`ai_monthly_limit_usd`, the subject rule), records the purchase durably,
lets the one vendor call happen, then settles it; a record left open (a
timeout, a 5xx, a process that died) counts at its estimate and holds its
subject for a day. The rule is `spend.decide`, held to
`tests/fixtures/spend-cases.json`. Paid model methods are fenced: called
outside a purchase they raise `spend.Unguarded`. `LocalStore` keeps the
records in our SQLite (`spend` table); the old `spend.jsonl` is imported once
and no longer written. More than `RUNAWAY_PER_HOUR` (6) automatic images in an
hour pauses AI until the owner presses Resume. The Illustrations switch is
`illustrations_generated`.
```

- [ ] **Step 2: The two spec lines**

In the spec's Settle list, change "a broken image after a 200" so it reads: the vendor answered with the result is `settled` even if the image then fails to decode (it was billed). In **Runaway pause**, add: "It counts automatic `plate` and `collage` purchases only; briefs and weather cost cents, and a day's collage can need a dozen briefs."

- [ ] **Step 3: The wiki step**

```bash
git clone https://github.com/wr/featherframe.wiki.git /tmp/ff-wiki-w938
```

In `AI-illustrations.md`, after the step where the owner pastes their API key, add one numbered step, in the page's own style:

```markdown
1. In your OpenAI account, set a monthly budget for the project your key belongs to. Featherframe stops at its own **Monthly limit**; a budget at OpenAI holds even for anything else using the key.
```

Open OpenAI's project settings in the browser pane first and use the name its page gives the budget control, in bold, if it differs from "budget". Then:

```bash
cd server && ./.venv/bin/python scripts/check_copy.py --wiki /tmp/ff-wiki-w938
```

Expected: no findings. Commit and push the wiki checkout with the message "AI illustrations: set a budget at OpenAI too (W-938)".

- [ ] **Step 4: The whole suite**

Run from the repo root: `make test`
Expected: PASS.

- [ ] **Step 5: Push and open the PR**

```bash
git push -u origin wells/w-938-ai-spend-guards
gh pr create --title "AI spend guards: one switch, a monthly limit, and no purchase without a record (W-938)" --body-file /tmp/w938-pr.md
```

Write `/tmp/w938-pr.md` first: what changed and why (the 26–28 Sep numbers), how it was verified (the suite, the replay test, the page screenshot), and what is left (part 2: the front door holds the count on Cloud). End with `Refs: W-938`. Attach the PR to the Linear issue.

- [ ] **Step 6: Deploy to Cloud after merge**

After the PR merges (squash), from an up-to-date `main`:

```bash
cd hosted && npx wrangler deploy
```

(Docker must be running; retry on a registry push drop.) Then open `https://cloud.featherframe.app`, open AI image generation, and confirm the summary reads `OpenAI · $… of $10.00` and this month's spend matches the October lines of the old ledger.
