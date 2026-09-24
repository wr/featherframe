"""Species-name matching: BirdNET names -> a plate in one of the folios.

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
     which Audubon never painted), that folio has nothing for it.
  3. Folios are asked in order (W-702): Havell first, then the others. The
     first folio with a real plate wins; none -> typographic fallback.

`fuzzy_resolve_plate` is a *build-time* helper used by fetch_plates to suggest
a plate number for a species whose plate isn't pinned in folios/havell.yaml. It is
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


def display_common_name(name: str) -> str:
    """A typed name, set like a field-guide entry: each word capitalised,
    the rest lowered, and only the first half of a hyphenated word takes a
    capital ("Black-capped Chickadee"). Species the curated index knows are
    spelled its way instead (SpeciesIndex.canonical_common)."""
    words = (name or "").split()
    return " ".join("-".join(seg.capitalize() if i == 0 else seg.lower()
                             for i, seg in enumerate(w.split("-")))
                    for w in words)


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


# The folio an index entry belongs to when it names none: every index and
# plate library written before W-702 is Havell's alone.
DEFAULT_FOLIO = "havell"


def folio_of(entry: dict) -> str:
    return str(entry.get("folio") or DEFAULT_FOLIO)


def has_plate(entry: Optional[dict]) -> bool:
    return bool(entry) and entry.get("plate") not in (None, "none", "None", False)


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
    legend: list = field(default_factory=list)   # the plate's printed figure key / plant lines
    folio: str = DEFAULT_FOLIO

    @property
    def has_image(self) -> bool:
        return bool(self.image_path) and Path(self.image_path).exists()


class _Folio:
    """One folio's entries, keyed by normalised scientific (and synonym) and
    common name."""

    def __init__(self) -> None:
        self.by_sci: dict[str, dict] = {}
        self.by_common: dict[str, dict] = {}

    def register(self, e: dict[str, Any]) -> None:
        sci = normalize(e.get("scientific", ""))
        com = normalize(e.get("common", ""))
        if sci:
            self.by_sci[sci] = e
        if com:
            self.by_common[com] = e
        # Register any extra scientific synonyms (taxonomic splits/renames).
        for syn in e.get("sci_synonyms", []) or []:
            self.by_sci[normalize(syn)] = e

    def find(self, common_name: str, scientific_name: str) -> Optional[dict]:
        return self.by_sci.get(normalize(scientific_name)) or self.by_common.get(normalize(common_name))


class SpeciesIndex:
    """Loads the curated plate index written by fetch_plates and matches
    detections against it. Construct from a dict (tests) or from disk.

    A species may have an entry in several folios; they are asked in
    `self.folios` order (the order the index lists them, Havell first)."""

    def __init__(self, entries: Optional[list[dict[str, Any]]] = None,
                 images_dir: Optional[Path] = None) -> None:
        self._images_dir = Path(images_dir) if images_dir else paths.plate_images_dir()
        self._folios: dict[str, _Folio] = {}
        for e in entries or []:
            self._folios.setdefault(folio_of(e), _Folio()).register(e)

    @property
    def folios(self) -> list[str]:
        return list(self._folios)

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
        # Unique entries (an entry is registered under several keys).
        return len({id(v) for f in self._folios.values() for v in f.by_sci.values()})

    def _any(self, common_name: str, scientific_name: str = "") -> Optional[dict]:
        for f in self._folios.values():
            entry = f.find(common_name, scientific_name)
            if entry is not None:
                return entry
        return None

    def canonical_common(self, common_name: str) -> Optional[str]:
        """The index's own spelling of a common name, matched loosely
        (case, hyphens, apostrophes), or None if the species is unknown."""
        entry = self._any(common_name)
        return str(entry["common"]) if entry and entry.get("common") else None

    def scientific_for(self, common_name: str) -> Optional[str]:
        """The curated scientific name for a common name, or None."""
        entry = self._any(common_name)
        return str(entry["scientific"]) if entry and entry.get("scientific") else None

    def entries(self, common_name: str, scientific_name: str = "") -> list[dict]:
        """Every folio's entry with a real plate for this species, matched
        exactly (scientific name first), in folio order. A folio's explicit
        "no plate" hands the species on to the next folio."""
        found = (f.find(common_name, scientific_name) for f in self._folios.values())
        return [e for e in found if has_plate(e)]

    def entry(self, common_name: str, scientific_name: str = "") -> Optional[dict]:
        """The first folio's entry with a plate, or None: never guess, always
        fall back."""
        found = self.entries(common_name, scientific_name)
        return found[0] if found else None

    def match(self, common_name: str, scientific_name: str = "") -> Optional[PlateMatch]:
        """A PlateMatch from the first folio whose scan is on disk, or None
        (-> fallback)."""
        for entry in self.entries(common_name, scientific_name):
            image_name = entry.get("image")
            m = PlateMatch(
                common_name=entry.get("common", common_name),
                scientific_name=entry.get("scientific", scientific_name),
                plate_number=int(entry["plate"]),
                image_path=str(self._images_dir / image_name) if image_name else None,
                audubon_title=entry.get("audubon_title", ""),
                composite=bool(entry.get("composite", False)),
                crop_box=entry.get("crop_box"),
                matched_by="exact",
                legend=[str(x) for x in (entry.get("legend") or [])],
                folio=folio_of(entry),
            )
            # A scan missing on disk degrades to the next folio, then the
            # fallback, rather than crash.
            if m.has_image:
                return m
        return None


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
