"""The shared plate library (W-842).

The Havell scans are 2.7 GB, and a render only ever uses the crop `plate.py`
takes of one: the bird's own content box, or a composite plate whole, or a
curated crop box. The library is those crops, taken once, so a server with no
scans of its own — the hosted render Container above all — can draw every
plate the box draws, pixel for pixel:

    library.json                    index.json's species list, each entry
                                    naming its crop by "library" key
    lib/<key>.gray.png              plate.extract()'s output ('L', final)
    lib/<key>.color.webp            the raw colour crop, lossless: the colour
                                    pair is normalised from it at load time,
                                    exactly as plate.extract_color() does

Both files are lossless, so a plate drawn from the library is the plate drawn
from the scan (a test holds it). About 1.2 GB for the whole edition.

A source is a directory or an http(s) base URL (the public R2 bucket). Remote
files are fetched on first use and kept in a local cache.

    python -m featherframe.plate_library build OUT_DIR   # from this box's scans
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
from pathlib import Path
from typing import Optional

import requests
from PIL import Image

from . import paths
from .names import SpeciesIndex
from .render import plate
from .render.provider import ArtProvider, Artwork

log = logging.getLogger("featherframe.plate_library")

LIBRARY_VERSION = 1
INDEX_NAME = "library.json"
FETCH_TIMEOUT_S = 30


def entry_key(entry: dict) -> str:
    """One key per distinct crop: the plate, and how it is cut. Species that
    share a composite plate share its file."""
    how = json.dumps([bool(entry.get("composite")), entry.get("crop_box")], sort_keys=True)
    stem = Path(str(entry["image"])).stem
    return f"{stem}-{hashlib.sha1(how.encode()).hexdigest()[:8]}"


def _raw_color_crop(path: Path, composite: bool, crop_box) -> Image.Image:
    """plate.extract_color() up to, not including, its normalisation."""
    rgb = plate._trim_marginalia(plate.load_color(path))
    gray = rgb.convert("L")
    if crop_box:
        box = plate._norm_box(gray, crop_box)
    elif composite:
        box = (0, 0, gray.width, gray.height)
    else:
        box = plate.content_box(gray)
    return rgb.crop(box)


def color_pair_from_raw(raw: Image.Image) -> tuple[Image.Image, Image.Image]:
    """The rest of plate.extract_color(): (gray, colour) of the same crop."""
    raw = raw.convert("RGB")
    return plate.paper_normalize(raw.convert("L")), plate.paper_normalize_color(raw)


def build(out_dir: Path, index_path: Optional[Path] = None,
          images_dir: Optional[Path] = None) -> dict:
    """Write the library for every entry with a plate and a scan on disk.
    Idempotent: a crop already in `out_dir` is not taken again."""
    index_path = Path(index_path or paths.plate_index_path())
    images_dir = Path(images_dir or paths.plate_images_dir())
    out_dir = Path(out_dir)
    (out_dir / "lib").mkdir(parents=True, exist_ok=True)
    data = json.loads(index_path.read_text())
    species, made, kept = [], 0, 0
    for entry in data.get("species", []):
        entry = dict(entry)
        has_plate = entry.get("plate") not in (None, "none", "None", False)
        scan = images_dir / str(entry.get("image") or "")
        if has_plate and entry.get("image") and scan.is_file():
            key = entry_key(entry)
            gray_p = out_dir / "lib" / f"{key}.gray.png"
            color_p = out_dir / "lib" / f"{key}.color.webp"
            if gray_p.exists() and color_p.exists():
                kept += 1
            else:
                composite, box = bool(entry.get("composite")), entry.get("crop_box")
                gray = plate.extract(scan, composite=composite, crop_box=box)
                raw = _raw_color_crop(scan, composite, box)
                _atomic_save(gray, gray_p, format="PNG", optimize=True)
                _atomic_save(raw, color_p, format="WEBP", lossless=True, method=4)
                made += 1
            entry["library"] = key
        entry.pop("image", None)
        species.append(entry)
    out = {"version": LIBRARY_VERSION, "generated_from": data.get("generated_at"),
           "species": species}
    (out_dir / INDEX_NAME).write_text(json.dumps(out, indent=1, sort_keys=True))
    log.info("plate library: %d crops taken, %d kept, %d entries", made, kept, len(species))
    return {"made": made, "kept": kept, "entries": len(species)}


def _atomic_save(img: Image.Image, dest: Path, **kw) -> None:
    tmp = dest.with_name(dest.name + ".tmp")
    img.save(tmp, **kw)
    os.replace(tmp, dest)


class PlateLibrary:
    """Reads a built library from a directory or a base URL."""

    def __init__(self, source: str, cache_dir: Optional[Path] = None) -> None:
        self.source = str(source).rstrip("/")
        self.remote = self.source.startswith(("http://", "https://"))
        self.cache_dir = Path(cache_dir or paths.data_dir() / "plate-library")
        self._index: Optional[SpeciesIndex] = None

    def _read(self, rel: str) -> bytes:
        if not self.remote:
            return (Path(self.source) / rel).read_bytes()
        cached = self.cache_dir / rel
        if cached.exists():
            return cached.read_bytes()
        r = requests.get(f"{self.source}/{rel}", timeout=FETCH_TIMEOUT_S)
        r.raise_for_status()
        cached.parent.mkdir(parents=True, exist_ok=True)
        tmp = cached.with_name(cached.name + ".tmp")
        tmp.write_bytes(r.content)
        os.replace(tmp, cached)
        return r.content

    def index(self) -> SpeciesIndex:
        if self._index is None:
            # The index is small and changes with the edition: always asked
            # for, never cached (a remote that is down leaves it empty).
            try:
                if self.remote:
                    r = requests.get(f"{self.source}/{INDEX_NAME}", timeout=FETCH_TIMEOUT_S)
                    r.raise_for_status()
                    data = r.json()
                else:
                    data = json.loads(self._read(INDEX_NAME))
            except (OSError, ValueError, requests.RequestException) as exc:
                log.warning("plate library index unavailable (%s): %s", self.source, exc)
                return SpeciesIndex([])
            self._index = SpeciesIndex(data.get("species", []))
        return self._index

    def image(self, key: str, kind: str) -> Image.Image:
        name = f"lib/{key}.gray.png" if kind == "gray" else f"lib/{key}.color.webp"
        with Image.open(io.BytesIO(self._read(name))) as im:
            im.load()
            return im.convert("L") if kind == "gray" else im.convert("RGB")


class LibraryProvider(ArtProvider):
    """AudubonProvider's twin for a server without the scans: the same curated
    index, the same crops, from the library."""

    name = "audubon"

    def __init__(self, library: PlateLibrary) -> None:
        self.library = library

    def reload(self) -> None:
        self.library._index = None

    @property
    def index(self) -> SpeciesIndex:
        return self.library.index()

    @property
    def species_count(self) -> int:
        return self.library.index().count

    def artwork(self, common_name: str, scientific_name: str) -> Optional[Artwork]:
        entry = self.library.index().entry(common_name, scientific_name)
        key = entry.get("library") if entry else None
        if not key:
            return None
        try:
            gray = self.library.image(key, "gray")
        except (OSError, ValueError, requests.RequestException) as exc:
            log.warning("library plate %s unavailable for %s: %s", key, common_name, exc)
            return None
        return Artwork(image=gray, audubon_plate=int(entry["plate"]),
                       composite=bool(entry.get("composite")),
                       legend=[str(x) for x in (entry.get("legend") or [])],
                       color_loader=lambda: color_pair_from_raw(self.library.image(key, "color")))


def from_env() -> Optional[LibraryProvider]:
    """FEATHERFRAME_PLATE_LIBRARY (a directory or a URL) puts the library in
    place of the scans."""
    source = os.environ.get("FEATHERFRAME_PLATE_LIBRARY", "").strip()
    return LibraryProvider(PlateLibrary(source)) if source else None


def main() -> None:
    ap = argparse.ArgumentParser(description="Featherframe plate library")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="take every crop from this box's scans")
    b.add_argument("out_dir", type=Path)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.cmd == "build":
        print(json.dumps(build(args.out_dir)))


if __name__ == "__main__":
    main()
