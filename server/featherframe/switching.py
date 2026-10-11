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
    """BirdNET-Go's *Test* button (W-865): kept as `test_at`, never a bird. Like
    `parse`, only a detection body counts, so a warning that names the test
    species is not a test."""
    if not isinstance(payload, dict):
        return False
    if kind == "birdnet_go":
        md = payload.get("metadata")
        sci = (name(md.get("scientific_name"))
               if payload.get("type") == "detection" and isinstance(md, dict) else "")
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
    """Returns "wake", "same", or the species key every screen is switched to.
    `queued` is what the server has not yet taken, in order: {"push": parse()}
    or {"marker": True}. Counts run over all of it, the choice over the pushes
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
