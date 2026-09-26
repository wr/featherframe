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
    python -m featherframe.plate_library build OUT_DIR --against https://plates.featherframe.app
    python -m featherframe.plate_library upload OUT_DIR  # to the R2 bucket, index last

`--against` takes only the crops the published library lacks (a new folio's,
W-702), so an update needs neither the whole edition's scans nor its upload.
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import io
import json
import logging
import os
from pathlib import Path
from typing import Optional

import requests
from PIL import Image

from . import paths
from .names import SpeciesIndex, folio_of, has_plate
from .render import plate
from .render.provider import ArtProvider, Artwork

log = logging.getLogger("featherframe.plate_library")

LIBRARY_VERSION = 1
INDEX_NAME = "library.json"
FETCH_TIMEOUT_S = 30


def entry_key(entry: dict, caption: bool = False) -> str:
    """One key per distinct crop: the plate, and how it is cut. Species that
    share a composite plate share its file. `caption`: the cut paints out a
    caption (plate.lifts_caption, W-883), so it is not the crop published
    before that under the same plate."""
    how = [bool(entry.get("composite")), entry.get("crop_box")]
    if entry.get("margins") or entry.get("tight"):
        # only a folio's own cut: Havell's keys stand
        how += [entry.get("margins"), f"white{plate.TIGHT_PAD}-despeckled-small" if entry.get("tight") else False]
    if entry.get("mask"):
        how += [{"mask": entry["mask"]}]
    if caption:
        how += ["caption-lifted"]
    how = json.dumps(how, sort_keys=True)
    stem = Path(str(entry["image"])).stem
    return f"{stem}-{hashlib.sha1(how.encode()).hexdigest()[:8]}"


def _raw_color_crop(path: Path, composite: bool, crop_box, margins=None,
                    tight: bool = False, mask=None) -> Image.Image:
    """plate.extract_color() up to, not including, its normalisation."""
    rgb = plate._trim_marginalia(plate.load_color(path), margins, mask)
    return plate._cut(rgb, plate._box(rgb.convert("L"), composite, crop_box, tight), tight)


def color_pair_from_raw(raw: Image.Image) -> tuple[Image.Image, Image.Image]:
    """The rest of plate.extract_color(): (gray, colour) of the same crop."""
    raw = raw.convert("RGB")
    return plate.paper_normalize(raw.convert("L")), plate.paper_normalize_color(raw)


def build(out_dir: Path, index_path: Optional[Path] = None,
          images_dir: Optional[Path] = None, published: frozenset = frozenset()) -> dict:
    """Write the library for every entry with a plate and a scan on disk.
    Idempotent: a crop already in `out_dir`, or already `published`, is not
    taken again (it is still named in library.json)."""
    index_path = Path(index_path or paths.plate_index_path())
    images_dir = Path(images_dir or paths.plate_images_dir())
    out_dir = Path(out_dir)
    (out_dir / "lib").mkdir(parents=True, exist_ok=True)
    data = json.loads(index_path.read_text())
    species, made, kept = [], 0, 0
    for entry in data.get("species", []):
        entry = dict(entry)
        scan = images_dir / str(entry.get("image") or "")
        if has_plate(entry) and entry.get("image") and scan.is_file():
            key = entry_key(entry, plate.lifts_caption(scan, entry.get("margins"), entry.get("mask")))
            gray_p = out_dir / "lib" / f"{key}.gray.png"
            color_p = out_dir / "lib" / f"{key}.color.webp"
            if key in published or (gray_p.exists() and color_p.exists()):
                kept += 1
            else:
                composite, box = bool(entry.get("composite")), entry.get("crop_box")
                margins, tight = entry.get("margins"), bool(entry.get("tight"))
                mask = entry.get("mask")
                gray = plate.extract(scan, composite=composite, crop_box=box,
                                     margins=margins, tight=tight, mask=mask)
                raw = _raw_color_crop(scan, composite, box, margins, tight, mask)
                _atomic_save(gray, gray_p, format="PNG", optimize=True)
                _atomic_save(raw, color_p, format="WEBP", lossless=True, method=4)
                made += 1
            entry["library"] = key
        entry.pop("image", None)
        species.append(entry)
    out = {"version": LIBRARY_VERSION, "generated_from": data.get("generated_at"),
           "folios": {k: {f: v for f, v in h.items() if f != "catalog"}
                      for k, h in (data.get("folios") or {}).items()},
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
            self._index = SpeciesIndex(data.get("species", []), folios=data.get("folios"))
        return self._index

    def image(self, key: str, kind: str) -> Image.Image:
        name = f"lib/{key}.gray.png" if kind == "gray" else f"lib/{key}.color.webp"
        with Image.open(io.BytesIO(self._read(name))) as im:
            im.load()
            return im.convert("L") if kind == "gray" else im.convert("RGB")


class LibraryProvider(ArtProvider):
    """PlateProvider's twin for a server without the scans: the same curated
    index, the same crops, from the library."""

    name = "plates"

    def __init__(self, library: PlateLibrary) -> None:
        self.library = library
        self.region: Optional[str] = None    # the household's Region: its folios first

    def reload(self) -> None:
        self.library._index = None

    @property
    def index(self) -> SpeciesIndex:
        return self.library.index()

    @property
    def species_count(self) -> int:
        return self.library.index().count

    def artwork(self, common_name: str, scientific_name: str) -> Optional[Artwork]:
        for entry in self.library.index().entries(common_name, scientific_name, self.region):
            key = entry.get("library")
            if not key:
                continue
            try:
                gray = self.library.image(key, "gray")
            except (OSError, ValueError, requests.RequestException) as exc:
                log.warning("library plate %s unavailable for %s: %s", key, common_name, exc)
                return None
            return Artwork(image=gray, plate=int(entry["plate"]), volume_no=entry.get("volume_no"),
                           folio=folio_of(entry),
                           composite=bool(entry.get("composite")),
                           legend=[str(x) for x in (entry.get("legend") or [])],
                           color_loader=lambda: color_pair_from_raw(self.library.image(key, "color")))
        return None


def published_keys(source: str) -> frozenset:
    """The crop keys a published library already has."""
    lib = PlateLibrary(source)
    lib.index()
    return frozenset(e["library"] for f in lib.index()._folios.values()   # noqa: SLF001
                     for e in f.by_sci.values() if e.get("library"))


BUCKET = "featherframe-plates"
_HOSTED_DIR = Path(__file__).resolve().parents[2] / "hosted"     # its wrangler and config
_TYPES = {".png": "image/png", ".webp": "image/webp", ".json": "application/json"}


def upload(out_dir: Path, bucket: str = BUCKET) -> int:
    """Put the built crops, then library.json, into the R2 bucket behind
    plates.featherframe.app (wrangler, as the signed-in account). The index
    goes last, so no server is ever handed a key whose crop is not there yet."""
    out_dir = Path(out_dir)
    files = sorted((out_dir / "lib").glob("*")) + [out_dir / INDEX_NAME]
    for f in files:
        key = f.relative_to(out_dir).as_posix()
        subprocess.run(["npx", "wrangler", "r2", "object", "put", f"{bucket}/{key}",
                        f"--file={f}", f"--content-type={_TYPES.get(f.suffix, 'application/octet-stream')}",
                        "--remote"], check=True, cwd=_HOSTED_DIR if _HOSTED_DIR.is_dir() else None)
        log.info("uploaded %s", key)
    return len(files)


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
    b.add_argument("--against", metavar="URL",
                   help="a published library: take only the crops it lacks")
    u = sub.add_parser("upload", help="put a built library into the R2 bucket, index last")
    u.add_argument("out_dir", type=Path)
    u.add_argument("--bucket", default=BUCKET)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.cmd == "build":
        have = published_keys(args.against) if args.against else frozenset()
        print(json.dumps(build(args.out_dir, published=have)))
    elif args.cmd == "upload":
        print(json.dumps({"uploaded": upload(args.out_dir, args.bucket)}))


if __name__ == "__main__":
    main()
