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


def test_the_test_button_is_known_before_it_is_parsed():
    body = {"type": "detection", "metadata": {"species": "Testus", "scientific_name": "Testus birdicus"}}
    assert switching.is_test("birdnet_go", body) and switching.parse("birdnet_go", body) is None
    assert not switching.is_test("birdnet_go", {"type": "detection", "metadata": {"scientific_name": "Turdus migratorius"}})
    # A warning on the same channel is not the Test button, whatever it names.
    assert not switching.is_test("birdnet_go", {"type": "warning", "metadata": {"scientific_name": "Testus birdicus"}})
