"""Species-name matching: BirdNET names -> Audubon plate.

This is the module the spec correctly predicts will break in the field, so it
is deliberately conservative. The guiding rule is priority #2: *never a wrong
bird*. When we are not confident, we return no match and the caller renders a
typographic fallback plate instead of guessing.

Matching strategy, in order:
  1. Exact lookup in the curated index (keyed by scientific AND common name,
     normalised). Scientific name is preferred because it is stable; BirdNET's
     common names track modern taxonomy while Audubon's plate titles are 1830s
     archaic ("Snow Bird" for the Dark-eyed Junco, etc.).
  2. If the curated entry explicitly says "no plate" (e.g. European Starling,
     which Audubon never painted), we return None on purpose.
  3. Otherwise None -> typographic fallback.

`fuzzy_resolve_plate` is a *build-time* helper used by fetch_plates to suggest
a plate number for a species whose plate isn't pinned in species.yaml. It is
never used to match a live detection, because token overlap is too eager
("European Starling" shares "starling" with plate 67, which is a Red-winged
Blackbird — exactly the wrong-bird trap we must avoid).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from . import paths

# Tokens that carry no discriminating signal in a plate title.
_STOPWORDS = {
    "the", "or", "and", "of", "a", "an", "bird", "common",
}


def normalize(name: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace. Hyphens -> spaces."""
    if not name:
        return ""
    s = name.lower().replace("-", " ")
    s = re.sub(r"[^a-z0-9\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _tokens(name: str) -> set[str]:
    return {t for t in normalize(name).split() if t and t not in _STOPWORDS}


@dataclass
class PlateMatch:
    common_name: str
    scientific_name: str
    plate_number: Optional[int]
    image_path: Optional[str]
    audubon_title: str = ""
    composite: bool = False
    crop_box: Optional[list] = None  # normalised [x, y, w, h] in 0..1, or None
    matched_by: str = "exact"

    @property
    def has_image(self) -> bool:
        return bool(self.image_path) and Path(self.image_path).exists()


class SpeciesIndex:
    """Loads the curated plate index written by fetch_plates and matches
    detections against it. Construct from a dict (tests) or from disk."""

    def __init__(self, entries: Optional[list[dict[str, Any]]] = None,
                 images_dir: Optional[Path] = None) -> None:
        self._images_dir = Path(images_dir) if images_dir else paths.plate_images_dir()
        self._by_sci: dict[str, dict] = {}
        self._by_common: dict[str, dict] = {}
        for e in entries or []:
            self._register(e)

    def _register(self, e: dict[str, Any]) -> None:
        sci = normalize(e.get("scientific", ""))
        com = normalize(e.get("common", ""))
        if sci:
            self._by_sci[sci] = e
        if com:
            self._by_common[com] = e
        # Register any extra scientific synonyms (taxonomic splits/renames).
        for syn in e.get("sci_synonyms", []) or []:
            self._by_sci[normalize(syn)] = e

    @classmethod
    def load(cls, index_path: Optional[Path] = None) -> "SpeciesIndex":
        index_path = Path(index_path) if index_path else paths.plate_index_path()
        if not index_path.exists():
            return cls([], images_dir=paths.plate_images_dir())
        data = json.loads(index_path.read_text())
        images_dir = Path(data.get("images_dir") or paths.plate_images_dir())
        if not images_dir.is_dir():
            # index.json may have been generated on another machine; its
            # recorded absolute path is meaningless here. The images always
            # live in img/ next to the index itself.
            images_dir = index_path.parent / "img"
        return cls(data.get("species", []), images_dir=images_dir)

    @property
    def count(self) -> int:
        # Unique entries (a species is registered under both keys).
        return len({id(v) for v in self._by_sci.values()})

    def match(self, common_name: str, scientific_name: str = "") -> Optional[PlateMatch]:
        """Return a PlateMatch with a usable image, or None (-> fallback)."""
        entry = self._by_sci.get(normalize(scientific_name)) or self._by_common.get(normalize(common_name))
        if entry is None:
            return None

        plate = entry.get("plate")
        # Explicit "no plate" species: never guess, always fall back.
        if plate in (None, "none", "None", False):
            return None

        image_name = entry.get("image")
        image_path = str(self._images_dir / image_name) if image_name else None
        m = PlateMatch(
            common_name=entry.get("common", common_name),
            scientific_name=entry.get("scientific", scientific_name),
            plate_number=int(plate),
            image_path=image_path,
            audubon_title=entry.get("audubon_title", ""),
            composite=bool(entry.get("composite", False)),
            crop_box=entry.get("crop_box"),
            matched_by="exact",
        )
        # If the image is missing on disk, degrade to fallback rather than crash.
        if not m.has_image:
            return None
        return m


def fuzzy_resolve_plate(
    common_name: str,
    catalog: Iterable[dict[str, Any]],
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Build-time suggestion only. Score plate titles by token overlap with the
    modern common name. Returns ranked candidates for a human to confirm.

    Each catalog item is a plate dict: {"plate": int, "name": str}.
    """
    want = _tokens(common_name)
    if not want:
        return []
    scored: list[tuple[float, dict]] = []
    for item in catalog:
        title = item.get("name", "")
        have = _tokens(title)
        if not have:
            continue
        overlap = want & have
        if not overlap:
            continue
        # Favour overlap size, then a tighter title (fewer stray species tokens
        # -> more likely a single-species plate), then a lower plate number.
        score = len(overlap) - 0.05 * len(have - want)
        scored.append((score, item))
    scored.sort(key=lambda s: (-s[0], len(_tokens(s[1].get("name", ""))), s[1].get("plate", 9999)))
    out = []
    for score, item in scored[:limit]:
        out.append({
            "plate": item.get("plate"),
            "name": item.get("name"),
            "score": round(score, 3),
            "composite": len(_tokens(item.get("name", ""))) > 4,
        })
    return out
