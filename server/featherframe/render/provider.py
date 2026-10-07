"""Art provider interface.

A provider turns a species into bird artwork. That's the one piece meant to be
swapped later (e.g. an AI-generation provider) — everything around it (museum
captioning, layout, dithering, framebuffer packing) is Featherframe's and stays
put. So the contract is deliberately narrow: given a species and a target box,
return a grayscale image of the bird, or None if you have no art for it (the
caller then renders the typographic fallback — never a wrong bird).
"""
from __future__ import annotations

import hashlib
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Union

from PIL import Image

from .. import credits
from ..names import SpeciesIndex
from . import plate

log = logging.getLogger("featherframe.provider")

# The ref of no art at all: the typographic fallback on the empty bough.
FALLBACK_REF = "fallback"


def art_ref(kind: str, *parts) -> str:
    """A piece of art's ref (W-1012): everything about it that reaches a
    sheet, as one short string. Two Artworks with one ref draw one sheet."""
    body = json.dumps(parts, sort_keys=True, default=str)
    return f"{kind}:{hashlib.sha256(body.encode()).hexdigest()[:16]}"


@dataclass
class Artwork:
    image: Image.Image          # grayscale 'L', the bird art (no caption)
    plate: Optional[int] = None   # the folio's own plate number, if any
    volume_no: Optional[Union[int, str]] = None   # its volume, where a folio numbers per volume (2, "Supp.")
    folio: Optional[str] = None   # the folio the plate is from ("havell"), if any
    artist: Optional[str] = None  # who made it, as the corner names them ("John James Audubon")
    composite: bool = False
    generated: bool = False     # True when the art is AI-generated, not a scan
    legend: list = field(default_factory=list)   # printed figure key / plant lines
    # For a colour panel: loads (gray, colour) of the same art, lazily so a
    # gray panel never pays for it. None = this art has no colour.
    color_loader: Optional[Callable[[], tuple]] = None
    # What this art is (`art_ref`), as the provider's `ref` names it; None
    # when the provider cannot say.
    ref: Optional[str] = None

    def color_pair(self) -> Optional[tuple]:
        """(gray 'L', colour 'RGB') at identical size, or None."""
        if self.color_loader is None:
            return None
        try:
            return self.color_loader()
        except (OSError, ValueError) as exc:
            log.warning("colour load failed, showing the gray art: %s", exc)
            return None


class ArtProvider(ABC):
    name: str = "base"

    @abstractmethod
    def artwork(self, common_name: str, scientific_name: str) -> Optional[Artwork]:
        """Return bird artwork for the species, or None if unavailable."""
        raise NotImplementedError

    def ref(self, common_name: str, scientific_name: str) -> Optional[str]:
        """The ref of the art `artwork` would return, said without loading
        it or buying it (W-1012): "" when this provider has none for the
        species, None when it cannot say without drawing."""
        return None


class ChainedProvider(ArtProvider):
    """First provider with art wins. The chain preserves the never-a-wrong-bird
    contract: every link returns None rather than guess, so a full miss still
    ends in the typographic fallback."""

    name = "chain"

    def __init__(self, providers: list[ArtProvider]) -> None:
        self.providers = list(providers)

    def artwork(self, common_name: str, scientific_name: str) -> Optional[Artwork]:
        for p in self.providers:
            art = p.artwork(common_name, scientific_name)
            if art is not None:
                return art
        return None

    def ref(self, common_name: str, scientific_name: str) -> Optional[str]:
        for p in self.providers:
            ref = p.ref(common_name, scientific_name)
            if ref is None or ref:
                return ref
        return FALLBACK_REF


class PlateProvider(ArtProvider):
    """Curated public-domain plates from the folios' scans on disk: Havell's
    Audubon first, then the other folios (W-702)."""

    name = "plates"

    def __init__(self, index: Optional[SpeciesIndex] = None) -> None:
        self._index = index or SpeciesIndex.load()
        self.region: Optional[str] = None    # the household's Region: its folios first

    def reload(self) -> None:
        self._index = SpeciesIndex.load()

    @property
    def index(self) -> SpeciesIndex:
        """The curated index the plates are matched against."""
        return self._index

    @property
    def species_count(self) -> int:
        return self._index.count

    def ref(self, common_name: str, scientific_name: str) -> Optional[str]:
        match = self._index.match(common_name, scientific_name, self.region)
        return "" if match is None else self._ref(match)

    @staticmethod
    def _ref(match) -> str:
        return art_ref("scan", Path(match.image_path).name, match.crop_box, match.margins,
                       match.mask, match.tight, match.composite, match.folio,
                       match.plate_number, match.volume_no, list(match.legend),
                       credits.drawn_by(match.folio, match.plate_number, match.volume_no))

    def artwork(self, common_name: str, scientific_name: str) -> Optional[Artwork]:
        match = self._index.match(common_name, scientific_name, self.region)
        if match is None:
            return None
        try:
            img = plate.extract(match.image_path, composite=match.composite,
                                crop_box=match.crop_box, margins=match.margins,
                                tight=match.tight, mask=match.mask)
        except (OSError, ValueError) as exc:
            # Corrupt/missing image -> fall back rather than break the frame.
            log.warning("plate extract failed for %s (%s): %s",
                        common_name, match.image_path, exc)
            return None
        return Artwork(image=img, plate=match.plate_number, volume_no=match.volume_no, folio=match.folio,
                       artist=credits.drawn_by(match.folio, match.plate_number, match.volume_no),
                       composite=match.composite,
                       legend=list(match.legend), ref=self._ref(match),
                       color_loader=lambda: plate.extract_color(
                           match.image_path, composite=match.composite,
                           crop_box=match.crop_box, margins=match.margins,
                           tight=match.tight, mask=match.mask))
