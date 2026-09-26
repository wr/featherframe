#!/usr/bin/env python3
"""One-time (idempotent) plate fetcher for Featherframe.

Reads every folio's crosswalk in server/scripts/folios/ (W-702: one file per
historical edition, Havell's first), downloads each species' plate, and writes
plates/index.json — the runtime crosswalk the render pipeline uses to turn a
detection into a plate. Every index entry names its `folio`; the index's
`folios` block carries each folio's header (title, artist, years, credit).

Havell's scans:
Sources, in order: this repo's `plates-v1` GitHub Release (the whole Havell
edition as checksummed tarballs — featherframe/plate_release.py), then
github.com/nathanbuchar/audubon-bird-plates (mirrors audubon.org's
public-domain scans, with a plate index at data.json), then media.audubon.org.
A part is only worth its few hundred MB when several plates are missing, so a
fresh install restores from the release and an upgrade that needs one new
plate asks the mirror — and falls back to the release if the mirror is down.

Usage:
    python scripts/fetch_plates.py                # download every folio's species
    python scripts/fetch_plates.py --dry-run      # resolve plates, download nothing
    python scripts/fetch_plates.py --folios other_dir/
    python scripts/fetch_plates.py --force        # re-download even if present
    python scripts/fetch_plates.py --all          # also cache every plate in the catalog (~2.9 GB)
    python scripts/fetch_plates.py --no-release   # skip the release tarballs, mirror only

`--all` caches the whole Havell edition, not just the curated species, so adding
a species to folios/havell.yaml later is an index rewrite with no network, and every
plate is on hand as a style reference for the AI provider. It is idempotent:
plates already on disk are skipped, so a re-run after a flaky night only fetches
what is missing.

Each folio's credit line (its header's `credit`, carried into the index) is
the one to preserve when displaying its plates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

# Make the featherframe package importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from featherframe import legends, paths, plate_release  # noqa: E402
from featherframe.names import fuzzy_resolve_plate  # noqa: E402

RAW_BASE = os.environ.get(
    "FEATHERFRAME_PLATES_MIRROR_URL",
    "https://raw.githubusercontent.com/nathanbuchar/audubon-bird-plates/master").rstrip("/")
DATA_JSON_URL = f"{RAW_BASE}/data.json"
AUDUBON_MEDIA = "https://media.audubon.org/boa_illustration"
DEFAULT_FOLIOS_DIR = Path(__file__).resolve().parent / "folios"
HAVELL = "havell"
DEFAULT_LEGENDS_YAML = Path(__file__).resolve().parent / "legends.yaml"
USER_AGENT = "Featherframe/1.0 (+https://github.com; personal e-paper art frame)"

# Transient failures (connection/DNS errors, 5xx) are retried per URL with
# exponential backoff. CT 113's resolver drops requests under bursts, and a
# 435-plate run is exactly such a burst. A 4xx is final: the mirror just
# doesn't have the file, so fall through to the next source.
RETRIES = 3
RETRY_BACKOFF_S = 2.0
MIN_PLATE_BYTES = 1024  # anything smaller is an error page, not a scan
POLITE_PAUSE_S = 0.15
# Below this many missing plates in one part, ask the mirror per plate first
# rather than pull the whole part.
PART_MIN_MISSING = 8


def _bucket(plate: int) -> str:
    """Subfolder the mirror files a plate under (1-99, 100-199, ... 400-435)."""
    if plate < 100:
        return "1-99"
    lo = (plate // 100) * 100
    hi = 435 if lo == 400 else lo + 99
    return f"{lo}-{hi}"


def load_catalog(session: requests.Session, cache: Path, force: bool = False,
                 release: str | None = None) -> dict[int, dict]:
    """Return {plate_number: {plate, name, slug, fileName}} from the plate index
    (the release's copy first, then the mirror's), caching data.json locally so
    re-runs work offline."""
    if cache.exists() and not force:
        raw = json.loads(cache.read_text())
    else:
        raw = plate_release.fetch_catalog(session, release) if release else None
        if raw is None:
            resp = session.get(DATA_JSON_URL, timeout=30)
            resp.raise_for_status()
            raw = resp.json()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(raw))
    catalog = {int(e["plate"]): e for e in raw}
    _warn_name_file_disagreements(catalog)
    return catalog


def _warn_name_file_disagreements(catalog: dict[int, dict]) -> None:
    """The mirror's data.json is known to shift its 'name' column +1 for plates
    361-399 while 'fileName' follows true Havell numbering. Downloads key off
    fileName (correct), but titles and fuzzy suggestions come from 'name' — so
    surface any disagreement instead of trusting it silently."""
    import re as _re
    bad = []
    for plate, meta in sorted(catalog.items()):
        m = _re.match(r"plate-\d+-(.+)\.jpg", meta.get("fileName", ""))
        if not m:
            continue
        slug_words = m.group(1).split("-")
        name_words = _re.sub(r"[^a-z0-9]+", " ", meta.get("name", "").lower()).split()
        hits = sum(1 for w in slug_words if w in name_words)
        if slug_words and hits / len(slug_words) < 0.5:
            bad.append(plate)
    if bad:
        print(f"  !  catalog 'name' disagrees with fileName for plates "
              f"{bad[0]}-{bad[-1]} ({len(bad)} plates) — titles there are "
              f"unreliable; trust pinned numbers + fileName, not fuzzy matches.")


def species_legend(entry: dict, plate: int | None,
                   plate_legends: dict[int, dict]) -> list[str]:
    """The legend lines printed under this species on its plate: the plate's
    transcribed lines, reduced to this species' own figure key on a composite
    sheet (legends.resolve). Empty when the plate has no transcription."""
    if plate is None:
        return []
    rec = plate_legends.get(int(plate))
    if not rec:
        return []
    composite = bool(entry.get("composite", False)) or bool(rec.get("composite", False))
    return legends.resolve(entry.get("audubon_title", ""), composite, rec.get("lines", []))


def resolve_plate(entry: dict, catalog: dict[int, dict], quiet: bool = False) -> int | None:
    """Return the plate number for a species entry, or None for 'no plate'.

    Honours an explicit `plate:` in the yaml; otherwise suggests one by fuzzy
    title match and prints it for the user to pin.
    """
    plate = entry.get("plate")
    if plate in (None, "none", "None", False):
        return None
    if isinstance(plate, int):
        return plate
    # Not pinned: fuzzy-resolve and report.
    candidates = fuzzy_resolve_plate(entry.get("common", ""), catalog.values())
    if quiet:
        return candidates[0]["plate"] if candidates else None
    if not candidates:
        print(f"  !  no plate match for {entry.get('common')!r} — will use typographic fallback")
        return None
    top = candidates[0]
    print(f"  ?  {entry.get('common')!r}: not pinned. Best guess plate {top['plate']} "
          f"({top['name']!r}). Pin it in folios/havell.yaml with  plate: {top['plate']}")
    for c in candidates[1:]:
        print(f"         alt: plate {c['plate']} ({c['name']!r})")
    return top["plate"]


def _fetch_to(session: requests.Session, url: str, dest: Path) -> bool:
    """GET `url` into `dest` atomically. True on success.

    Retries transient failures RETRIES times with backoff; returns False on a
    4xx (not here — try the next source) or once the retries are spent.
    """
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(RETRIES + 1):
        try:
            with session.get(url, timeout=60, stream=True) as r:
                if r.status_code == 200:
                    with open(tmp, "wb") as fh:
                        for chunk in r.iter_content(chunk_size=1 << 16):
                            fh.write(chunk)
                    if tmp.stat().st_size < MIN_PLATE_BYTES:
                        tmp.unlink(missing_ok=True)
                        return False
                    tmp.replace(dest)
                    return True
                if r.status_code >= 500:
                    raise requests.RequestException(f"HTTP {r.status_code}")
                return False
        except requests.RequestException as exc:
            tmp.unlink(missing_ok=True)
            if attempt == RETRIES:
                print(f"       {url} failed: {exc}")
                return False
            time.sleep(RETRY_BACKOFF_S * (2 ** attempt))
    return False


def download_plate(session: requests.Session, plate: int, catalog: dict[int, dict],
                   dest_dir: Path, force: bool) -> str | None:
    """Download plate image, return the stored filename (or None on failure)."""
    meta = catalog.get(plate)
    if not meta:
        print(f"  !  plate {plate} not in catalog")
        return None
    filename = meta["fileName"]  # e.g. plate-131-american-robin.jpg
    dest = dest_dir / filename
    if dest.exists() and dest.stat().st_size > 0 and not force:
        return filename

    urls = [
        f"{RAW_BASE}/plates/{_bucket(plate)}/{filename}",
        f"{AUDUBON_MEDIA}/{filename}",
    ]
    dest_dir.mkdir(parents=True, exist_ok=True)
    for url in urls:
        if _fetch_to(session, url, dest):
            return filename
    print(f"  !  could not download plate {plate} ({filename})")
    return None


def restore_from_release(session: requests.Session, release: str | None, wanted: set[int],
                         catalog: dict[int, dict], images_dir: Path,
                         min_missing: int = PART_MIN_MISSING) -> set[int]:
    """Pull release parts for the `wanted` plates that aren't on disk. Only
    parts missing at least `min_missing` of them are fetched; returns the
    plates restored."""
    if not release:
        return set()
    missing = {p for p in wanted if p in catalog and not _cached(catalog[p], images_dir)}
    by_part: dict[str, set[int]] = {}
    for p in missing:
        by_part.setdefault(plate_release.part_name(p), set()).add(p)
    worth = set().union(*(ps for ps in by_part.values() if len(ps) >= min_missing))
    return plate_release.fetch_parts(session, release, worth, catalog, images_dir)


def _cached(meta: dict, images_dir: Path) -> bool:
    f = images_dir / meta.get("fileName", "")
    return bool(meta.get("fileName")) and f.exists() and f.stat().st_size > 0


def cache_all(session: requests.Session, catalog: dict[int, dict], images_dir: Path,
              force: bool, dry_run: bool = False) -> dict[str, int]:
    """Fetch every plate in the catalog that isn't on disk yet. Returns counts."""
    stats = {"downloaded": 0, "present": 0, "failed": 0}
    todo = [p for p, m in sorted(catalog.items()) if force or not _cached(m, images_dir)]
    stats["present"] = len(catalog) - len(todo)
    if dry_run:
        print(f"  ·  {stats['present']} of {len(catalog)} plates cached; "
              f"{len(todo)} to fetch (dry-run)")
        return stats
    for i, plate in enumerate(todo, 1):
        if download_plate(session, plate, catalog, images_dir, force):
            stats["downloaded"] += 1
        else:
            stats["failed"] += 1
        if i % 25 == 0 or i == len(todo):
            print(f"  ·  {i}/{len(todo)} fetched ({_dir_size_mb(images_dir):.0f} MB on disk)")
        time.sleep(POLITE_PAUSE_S)
    return stats


def _dir_size_mb(d: Path) -> float:
    return sum(f.stat().st_size for f in d.glob("*.jpg")) / 1e6 if d.is_dir() else 0.0


def catalog_rows(catalog: dict[int, dict], images_dir: Path) -> list[dict]:
    """The catalog as written to index.json: every Havell plate, with the
    on-disk filename when it is cached so the server can see what it has."""
    return [{"plate": p, "name": m["name"],
             "image": m["fileName"] if _cached(m, images_dir) else None}
            for p, m in sorted(catalog.items())]


def _first_year(header: dict) -> int:
    m = re.search(r"\d{4}", str(header.get("years", "")))
    return int(m.group()) if m else 9999


def load_folios(folios_dir: Path) -> list[tuple[str, dict, list]]:
    """(id, header, species) for every folio file, Havell's first, then the
    rest as they were published (W-870: Europe 1832 before Australia 1840), so
    a new folio never takes a species from one a household already sees: the
    order the index lists them is the order a species' folios are asked in."""
    out = []
    for f in folios_dir.glob("*.yaml"):
        doc = yaml.safe_load(f.read_text()) or {}
        out.append((f.stem, dict(doc.get("folio") or {}), list(doc.get("species") or [])))
    return sorted(out, key=lambda x: (x[0] != HAVELL, _first_year(x[1]), x[0]))


def fetch_havell(session: requests.Session, species: list, args, images_dir: Path,
                 plate_legends: dict[int, dict], header: dict) -> tuple[list[dict], dict, dict]:
    """Havell's crosswalk -> (index records, counts, the folio's extra header:
    its catalog). The mirror, the release and data.json's quirks stay here."""
    catalog_cache = paths.plates_dir() / "data.json"
    print(f"Loading plate catalog…")
    release = None if args.no_release else plate_release.base_url()
    catalog = load_catalog(session, catalog_cache, force=args.force, release=release)
    print(f"Catalog has {len(catalog)} plates. Processing {len(species)} species.\n")

    # --force means "re-download from the source scans", so it skips the release.
    wanted = set(catalog) if args.all else {
        p for p in (resolve_plate(e, catalog, quiet=True) for e in species) if p is not None}
    if not args.dry_run and not args.force:
        restored = restore_from_release(session, release, wanted, catalog, images_dir)
        if restored:
            print(f"  ✓  {len(restored)} plates restored from the {plate_release.RELEASE_TAG} release\n")

    records = []
    counts = {"downloaded": 0, "fallback": 0, "failed": 0}
    for entry in species:
        common = entry.get("common", "?")
        plate = resolve_plate(entry, catalog)
        record = {
            "folio": HAVELL,
            "common": common,
            "scientific": entry.get("scientific", ""),
            "plate": plate,
            "audubon_title": entry.get("audubon_title", ""),
            "composite": bool(entry.get("composite", False)),
            "crop_box": entry.get("crop_box"),
            "sci_synonyms": entry.get("sci_synonyms", []),
            "image": None,
            "legend": species_legend(entry, plate, plate_legends),
        }
        if plate is None:
            print(f"  ·  {common}: typographic fallback (no plate)")
            counts["fallback"] += 1
            records.append(record)
            continue

        if args.dry_run:
            state = "cached" if _cached(catalog.get(plate, {}), images_dir) else "would download"
            print(f"  ·  {common}: plate {plate} ({state}, dry-run)")
            records.append(record)
            continue

        on_disk = _cached(catalog.get(plate, {}), images_dir) and not args.force
        filename = download_plate(session, plate, catalog, images_dir, args.force)
        if not filename and plate in catalog:
            # Mirror and audubon.org both failed: the release part is the last resort.
            if restore_from_release(session, release, {plate}, catalog, images_dir, min_missing=1):
                filename = catalog[plate]["fileName"]
        if filename:
            record["image"] = filename
            counts["downloaded"] += 1
            print(f"  ✓  {common}: plate {plate} -> {filename}")
        else:
            counts["failed"] += 1
        records.append(record)
        if not on_disk:
            time.sleep(POLITE_PAUSE_S)  # be polite to the mirror

    if args.all:
        print(f"\nCaching the full catalog ({len(catalog)} plates)…")
        all_stats = cache_all(session, catalog, images_dir, args.force, dry_run=args.dry_run)
        if all_stats["failed"]:
            all_stats["failed"] -= len(restore_from_release(
                session, release, set(catalog), catalog, images_dir, min_missing=1))
        counts["failed"] += all_stats["failed"]

    rows = catalog_rows(catalog, images_dir)
    cached = sum(1 for r in rows if r["image"])
    print(f"  {cached} of {len(catalog)} Havell plates cached "
          f"({_dir_size_mb(images_dir) / 1000:.2f} GB in {images_dir})")
    return records, counts, {"catalog": rows}


def scan_filename(folio: str, entry: dict) -> str:
    """Where a scanned folio keeps a sheet, under img/: one file per leaf of
    the book, shared by every species on it. Named by leaf, not plate number:
    a copy's numbering can disagree with the list (Gould's 447/448)."""
    return f"{folio}/{entry['volume']}-{int(entry['leaf']):04d}.jpg"


def scan_margins(entry: dict, header: dict):
    """The part of an upright sheet kept before the crop: the plate's own
    `margins`, else its volume's (`volume_margins`, keyed by the entry's
    `volume`: a binding's gutter or gilt edge shows on some volumes and not
    others), else the folio's. Resolved here, so the index carries the answer
    and the runtime and the plate library never look it up."""
    return (entry.get("margins")
            or (header.get("volume_margins") or {}).get(entry.get("volume"))
            or header.get("margins"))


def _paper_surface(small):
    """The paper's own tone across the sheet, per channel: a quadratic surface
    fitted to the paper pixels only (those not much darker than the fit,
    refitted a few times), so no subject however large is mistaken for paper."""
    import numpy as np
    a = np.asarray(small, dtype=np.float32)
    h, w, _ = a.shape
    yy, xx = np.mgrid[0:h, 0:w]
    x, y = (xx / w - 0.5).ravel(), (yy / h - 0.5).ravel()
    basis = np.stack([np.ones_like(x), x, y, x * x, x * y, y * y], axis=1)
    luma = a.mean(axis=2).ravel()
    paper = luma > np.percentile(luma, 50)
    for _ in range(4):
        coef, *_ = np.linalg.lstsq(basis[paper], luma[paper], rcond=None)
        paper = luma > basis @ coef - 12        # within a few levels of the fit
    chans = [np.linalg.lstsq(basis[paper], a[..., c].ravel()[paper], rcond=None)[0]
             for c in range(3)]
    return np.stack([(basis @ c).reshape(h, w) for c in chans], axis=2)


# How close to the paper's own tone a pixel must be to become pure white: the
# paper's grain and stains are not the artist's, and on e-ink any of it left
# near-white dithers into a grey speckle (Wells, 24 Sep 2026: "quite light").
PAPER_CLEAR = 0.94


def flatten_paper(im, clear: float = PAPER_CLEAR):
    """Divide out the sheet's own paper tone (foxing, a lighting gradient) and
    clear it to pure white: a sparse sheet is mostly paper, and what
    paper_normalize would leave of it is a grey cast on the glass. Anything
    within `clear` of the paper becomes white; darker ink keeps its tone,
    stretched by the same factor. Row strips keep the float working set small."""
    import numpy as np
    from PIL import Image
    small_w = 256
    small = im.resize((small_w, max(1, round(im.height * small_w / im.width))), Image.BILINEAR)
    surf = _paper_surface(small)
    bg = Image.fromarray(np.clip(surf, 1, 255).astype(np.uint8)).resize(im.size, Image.BILINEAR)
    out = Image.new("RGB", im.size)
    for y in range(0, im.height, 512):
        box = (0, y, im.width, min(im.height, y + 512))
        a = np.asarray(im.crop(box), dtype=np.float32)
        b = np.maximum(np.asarray(bg.crop(box), dtype=np.float32), 1.0)
        out.paste(Image.fromarray(np.clip(a / b / clear * 255.0, 0, 255).astype(np.uint8)), box[:2])
    return out


def store_scan(raw: Path, dest: Path, rotate: int = 0, flatten: bool = False) -> None:
    """The master, stood upright (and its paper evened, for a folio that asks),
    as a JPEG like Havell's: what plate.py reads."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    with Image.open(raw) as im:
        im = im.convert("RGB")
        if rotate:
            im = im.rotate(rotate, expand=True)
        if flatten:
            im = flatten_paper(im)
        tmp = dest.with_name(dest.name + ".part")
        im.save(tmp, format="JPEG", quality=95)
    tmp.replace(dest)


def from_release(session: requests.Session, header: dict, entry: dict, dest: Path,
                 manifests: dict) -> bool:
    """The sheet from the folio's dataset release (its header's `release`,
    github.com/wr/historical-bird-plates, W-868) when the scan's own source
    fails. The release holds exactly what store_scan makes of the scan, so it
    is stored as it comes, once its sha256 matches the release's manifest."""
    base = (header.get("release") or "").rstrip("/")
    if not base:
        return False
    if base not in manifests:
        try:
            r = session.get(f"{base}/manifest.json", timeout=60)
            manifests[base] = r.json().get("files", {}) if r.status_code == 200 else {}
        except (requests.RequestException, ValueError):
            manifests[base] = {}
    fname = f"sheet-{entry['volume']}-{int(entry['leaf']):04d}.jpg"
    want = (manifests[base].get(fname) or {}).get("sha256")
    if not want:
        return False
    tmp = dest.with_name(dest.name + ".rel")
    if not _fetch_to(session, f"{base}/{fname}", tmp):
        return False
    if hashlib.sha256(tmp.read_bytes()).hexdigest() != want:
        print(f"       {fname}: checksum does not match the release manifest")
        tmp.unlink(missing_ok=True)
        return False
    tmp.replace(dest)
    return True


def fetch_scans(folio: str):
    """The fetcher for a folio whose header names a `scans` URL template:
    one master per plate, {volume} and {leaf} filled from the entry."""
    def fetch(session: requests.Session, species: list, args, images_dir: Path,
              plate_legends: dict[int, dict], header: dict) -> tuple[list[dict], dict, dict]:
        counts = {"downloaded": 0, "fallback": 0, "failed": 0}
        records = []
        manifests: dict = {}
        for entry in species:
            common = entry.get("common", "?")
            record = {
                "folio": folio,
                "common": common,
                "scientific": entry.get("scientific", ""),
                "plate": entry.get("plate"),
                "volume_no": entry.get("volume_no") if header.get("plates_per_volume") else None,
                "title": entry.get("gould_title") or entry.get("title", ""),
                "composite": bool(entry.get("composite", False)),
                "crop_box": entry.get("crop_box"),
                "margins": scan_margins(entry, header),
                "mask": entry.get("mask"),
                "tight": bool(entry.get("tight", header.get("tight", False))),
                "sci_synonyms": entry.get("sci_synonyms", []),
                "image": None,
                "legend": [str(x) for x in entry.get("legend") or []],
            }
            if entry.get("preferred"):
                record["preferred"] = True   # asked ahead of the publication order (W-871)
            records.append(record)
            if entry.get("plate") in (None, "none", False):
                counts["fallback"] += 1
                continue
            name = scan_filename(folio, entry)
            dest = images_dir / name
            if dest.exists() and dest.stat().st_size > 0 and not args.force:
                record["image"] = name
                counts["downloaded"] += 1
                continue
            url = header["scans"].format(volume=entry["volume"], leaf=int(entry["leaf"]))
            if args.dry_run:
                print(f"  ·  {common}: plate {entry['plate']} (would download {url}, dry-run)")
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            raw = dest.with_name(dest.name + ".src")
            if _fetch_to(session, url, raw):
                try:
                    flatten = entry.get("flatten", header.get("flatten", False))
                    store_scan(raw, dest, int(entry.get("rotate") or 0), bool(flatten))
                finally:
                    raw.unlink(missing_ok=True)
                record["image"] = name
                counts["downloaded"] += 1
                print(f"  ✓  {common}: plate {entry['plate']} -> {name}")
                time.sleep(POLITE_PAUSE_S)
            elif from_release(session, header, entry, dest, manifests):
                record["image"] = name
                counts["downloaded"] += 1
                print(f"  ✓  {common}: plate {entry['plate']} -> {name} (from the dataset release)")
            else:
                counts["failed"] += 1
                print(f"  !  could not download {common} ({url})")
        return records, counts, {}
    return fetch


# Havell's own fetcher; any other folio is fetched from its header's `scans`.
FETCHERS = {HAVELL: fetch_havell}


def fetcher_for(folio: str, header: dict):
    if folio in FETCHERS:
        return FETCHERS[folio]
    return fetch_scans(folio) if header.get("scans") else None


def main() -> int:
    ap = argparse.ArgumentParser(description="Download the folios' plates for Featherframe.")
    ap.add_argument("--folios", type=Path, default=DEFAULT_FOLIOS_DIR,
                    help="directory of folio crosswalks (default: scripts/folios/)")
    ap.add_argument("--legends", type=Path, default=DEFAULT_LEGENDS_YAML,
                    help="Havell plate legends YAML (default: scripts/legends.yaml)")
    ap.add_argument("--dry-run", action="store_true", help="resolve plates but download nothing")
    ap.add_argument("--force", action="store_true", help="re-download even if present")
    ap.add_argument("--all", action="store_true",
                    help="also cache every Havell plate, not just the curated species (~2.9 GB)")
    ap.add_argument("--no-release", action="store_true",
                    help="skip the release tarballs; fetch plate by plate from the mirror")
    args = ap.parse_args()

    folios = load_folios(args.folios)
    if not any(species for _, _, species in folios):
        print(f"No species found in {args.folios}")
        return 1
    plate_legends = legends.load(args.legends)
    images_dir = paths.plate_images_dir()
    index_path = paths.plate_index_path()

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    index_species, headers = [], {}
    totals = {"downloaded": 0, "fallback": 0, "failed": 0}
    for folio, header, species in folios:
        fetch = fetcher_for(folio, header)
        if fetch is None:
            print(f"  !  no fetcher for folio {folio!r}; skipped")
            continue
        print(f"== {header.get('title', folio)} ({folio})")
        records, counts, extra = fetch(session, species, args, images_dir, plate_legends, header)
        index_species += records
        headers[folio] = {k: v for k, v in {**header, **extra}.items() if k != "scans"}
        for k in totals:
            totals[k] += counts[k]

    index = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "images_dir": str(images_dir),
        "folios": headers,
        "species": index_species,
    }
    if args.dry_run:
        # Never rewrite a live index with no images recorded.
        print(f"\nDry run: {index_path} left untouched")
    else:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(json.dumps(index, indent=2))
        print(f"\nWrote {index_path}")
    print(f"  {totals['downloaded']} downloaded, {totals['fallback']} typographic-fallback, "
          f"{totals['failed']} failed")
    if totals["failed"]:
        print("  Some downloads failed — re-run to retry; the mirror is occasionally flaky.")
    return 0 if totals["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
