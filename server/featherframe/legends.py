"""Audubon's printed plate legends: the figure key and plant line engraved under
each plate's title, transcribed into scripts/legends.yaml by Havell number.

`resolve` turns a plate's raw lines into the lines shown under ONE species: a
single-species plate passes through; a composite sheet (several species, each
with its own "Title, 1. Male, 2. Female." key) yields only the detected
species' key — never another bird's — plus the shared plant line.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

import yaml

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "legends.yaml"

# "Chesnut-backed Titmouse, 1. Male, 2. Female." -> title + figure key.
_KEY = re.compile(r"^(?P<title>[^,]+?),\s*(?P<key>(?:\d|Male|Female|Adult|Young|Old).*)$")


def _norm(title: str) -> str:
    t = title.lower().replace("&", " and ")
    t = re.sub(r"\(composite\)", " ", t)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def load(path: Optional[Path] = None) -> dict[int, dict[str, Any]]:
    """{plate: {"lines": [...], "composite": bool}} from legends.yaml."""
    p = Path(path) if path else DEFAULT_PATH
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text()) or {}
    out: dict[int, dict[str, Any]] = {}
    for plate, rec in (data.get("legends") or {}).items():
        rec = rec or {}
        out[int(plate)] = {"lines": [str(x) for x in rec.get("lines") or []],
                           "composite": bool(rec.get("composite", False))}
    return out


def resolve(audubon_title: str, composite: bool, lines: list[str]) -> list[str]:
    """The legend lines to print under `audubon_title`'s plate."""
    if not composite:
        return list(lines)
    want = _norm(audubon_title)
    species: list[tuple[str, str]] = []   # (normalised title, key)
    plant: list[str] = []
    for line in lines:
        line = line.strip()
        if line.startswith("(") and species:
            # "(and Nest.)" continues the species key just above it.
            t, k = species[-1]
            species[-1] = (t, f"{k} {line}")
            continue
        m = _KEY.match(line)
        if m:
            species.append((_norm(m.group("title")), m.group("key").strip()))
        else:
            plant.append(line)
    key = next((k for t, k in species if t == want), None)
    return ([key] if key else []) + plant
