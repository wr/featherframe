"""Art provider interface.

A provider turns a species into bird artwork. That's the one piece meant to be
swapped later (e.g. an AI-generation provider) — everything around it (museum
captioning, layout, dithering, framebuffer packing) is Featherframe's and stays
put. So the contract is deliberately narrow: given a species and a target box,
return a grayscale image of the bird, or None if you have no art for it (the
caller then renders the typographic fallback — never a wrong bird).
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional

from PIL import Image

from ..names import SpeciesIndex
from . import plate

log = logging.getLogger("featherframe.provider")


@dataclass
class Artwork:
    image: Image.Image          # grayscale 'L', the bird art (no caption)
    audubon_plate: Optional[int]  # source Havell plate number, if any
    composite: bool = False
    generated: bool = False     # True when the art is AI-generated, not a scan
    generated_by: Optional[str] = None   # who drew it ("OpenAI"), for the provenance line
    legend: list = field(default_factory=list)   # printed figure key / plant lines
    # For a colour panel: loads (gray, colour) of the same art, lazily so a
    # gray panel never pays for it. None = this art has no colour (the bough).
    color_loader: Optional[Callable[[], tuple]] = None

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


class AudubonProvider(ArtProvider):
    """v1 provider: curated public-domain Audubon plates."""

    name = "audubon"

    def __init__(self, index: Optional[SpeciesIndex] = None) -> None:
        self._index = index or SpeciesIndex.load()

    def reload(self) -> None:
        self._index = SpeciesIndex.load()

    @property
    def species_count(self) -> int:
        return self._index.count

    def artwork(self, common_name: str, scientific_name: str) -> Optional[Artwork]:
        match = self._index.match(common_name, scientific_name)
        if match is None:
            return None
        try:
            img = plate.extract(match.image_path, composite=match.composite,
                                crop_box=match.crop_box)
        except (OSError, ValueError) as exc:
            # Corrupt/missing image -> fall back rather than break the frame.
            log.warning("plate extract failed for %s (%s): %s",
                        common_name, match.image_path, exc)
            return None
        return Artwork(image=img, audubon_plate=match.plate_number, composite=match.composite,
                       legend=list(match.legend),
                       color_loader=lambda: plate.extract_color(
                           match.image_path, composite=match.composite,
                           crop_box=match.crop_box))
