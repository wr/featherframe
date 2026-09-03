#!/usr/bin/env python3
"""One-time (idempotent) plate fetcher for Featherframe.

Reads server/scripts/species.yaml, downloads each species' Audubon plate from
the public-domain mirror, and writes plates/index.json — the runtime crosswalk
the render pipeline uses to turn a detection into a plate.

Source: github.com/nathanbuchar/audubon-bird-plates (mirrors audubon.org's
public-domain scans, with a plate index at data.json). Falls back to
media.audubon.org if a raw file is missing.

Usage:
    python scripts/fetch_plates.py                # download everything in species.yaml
    python scripts/fetch_plates.py --dry-run      # resolve plates, download nothing
    python scripts/fetch_plates.py --species other_list.yaml
    python scripts/fetch_plates.py --force        # re-download even if present
    python scripts/fetch_plates.py --all          # also cache every plate in the catalog (~2.9 GB)

`--all` caches the whole Havell edition, not just the curated species, so adding
a species to species.yaml later is an index rewrite with no network, and every
plate is on hand as a style reference for the AI provider. It is idempotent:
plates already on disk are skipped, so a re-run after a flaky night only fetches
what is missing.

Public-domain credit line to preserve when displaying: "Courtesy of the John
James Audubon Center at Mill Grove, Montgomery County Audubon Collection, and
Zebra Publishing."
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

# Make the featherframe package importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from featherframe import paths  # noqa: E402
from featherframe.names import fuzzy_resolve_plate  # noqa: E402

RAW_BASE = "https://raw.githubusercontent.com/nathanbuchar/audubon-bird-plates/master"
DATA_JSON_URL = f"{RAW_BASE}/data.json"
AUDUBON_MEDIA = "https://media.audubon.org/boa_illustration"
DEFAULT_SPECIES_YAML = Path(__file__).resolve().parent / "species.yaml"
USER_AGENT = "Featherframe/1.0 (+https://github.com; personal e-paper art frame)"

# Transient failures (connection/DNS errors, 5xx) are retried per URL with
# exponential backoff. CT 113's resolver drops requests under bursts, and a
# 435-plate run is exactly such a burst. A 4xx is final: the mirror just
# doesn't have the file, so fall through to the next source.
RETRIES = 3
RETRY_BACKOFF_S = 2.0
MIN_PLATE_BYTES = 1024  # anything smaller is an error page, not a scan
POLITE_PAUSE_S = 0.15


def _bucket(plate: int) -> str:
    """Subfolder the mirror files a plate under (1-99, 100-199, ... 400-435)."""
    if plate < 100:
        return "1-99"
    lo = (plate // 100) * 100
    hi = 435 if lo == 400 else lo + 99
    return f"{lo}-{hi}"


def load_catalog(session: requests.Session, cache: Path, force: bool = False) -> dict[int, dict]:
    """Return {plate_number: {plate, name, slug, fileName}} from the mirror index,
    caching data.json locally so re-runs work offline."""
    if cache.exists() and not force:
        raw = json.loads(cache.read_text())
    else:
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


def resolve_plate(entry: dict, catalog: dict[int, dict]) -> int | None:
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
    if not candidates:
        print(f"  !  no plate match for {entry.get('common')!r} — will use typographic fallback")
        return None
    top = candidates[0]
    print(f"  ?  {entry.get('common')!r}: not pinned. Best guess plate {top['plate']} "
          f"({top['name']!r}). Pin it in species.yaml with  plate: {top['plate']}")
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Download Audubon plates for Featherframe.")
    ap.add_argument("--species", type=Path, default=DEFAULT_SPECIES_YAML,
                    help="species list YAML (default: scripts/species.yaml)")
    ap.add_argument("--dry-run", action="store_true", help="resolve plates but download nothing")
    ap.add_argument("--force", action="store_true", help="re-download even if present")
    ap.add_argument("--all", action="store_true",
                    help="also cache every plate in the catalog, not just the curated species (~2.9 GB)")
    args = ap.parse_args()

    doc = yaml.safe_load(args.species.read_text()) or {}
    species = doc.get("species", [])
    if not species:
        print(f"No species found in {args.species}")
        return 1

    images_dir = paths.plate_images_dir()
    index_path = paths.plate_index_path()
    catalog_cache = paths.plates_dir() / "data.json"

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    print(f"Loading plate catalog…")
    catalog = load_catalog(session, catalog_cache, force=args.force)
    print(f"Catalog has {len(catalog)} plates. Processing {len(species)} species.\n")

    index_species = []
    downloaded = fallback = failed = 0
    for entry in species:
        common = entry.get("common", "?")
        plate = resolve_plate(entry, catalog)
        record = {
            "common": common,
            "scientific": entry.get("scientific", ""),
            "plate": plate,
            "audubon_title": entry.get("audubon_title", ""),
            "composite": bool(entry.get("composite", False)),
            "crop_box": entry.get("crop_box"),
            "sci_synonyms": entry.get("sci_synonyms", []),
            "image": None,
        }
        if plate is None:
            print(f"  ·  {common}: typographic fallback (no plate)")
            fallback += 1
            index_species.append(record)
            continue

        if args.dry_run:
            state = "cached" if _cached(catalog.get(plate, {}), images_dir) else "would download"
            print(f"  ·  {common}: plate {plate} ({state}, dry-run)")
            index_species.append(record)
            continue

        filename = download_plate(session, plate, catalog, images_dir, args.force)
        if filename:
            record["image"] = filename
            downloaded += 1
            print(f"  ✓  {common}: plate {plate} -> {filename}")
        else:
            failed += 1
        index_species.append(record)
        time.sleep(POLITE_PAUSE_S)  # be polite to the mirror

    if args.all:
        print(f"\nCaching the full catalog ({len(catalog)} plates)…")
        all_stats = cache_all(session, catalog, images_dir, args.force, dry_run=args.dry_run)
        failed += all_stats["failed"]

    rows = catalog_rows(catalog, images_dir)
    cached = sum(1 for r in rows if r["image"])
    index = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "images_dir": str(images_dir),
        "source": "github.com/nathanbuchar/audubon-bird-plates",
        "credit": ("Courtesy of the John James Audubon Center at Mill Grove, "
                   "Montgomery County Audubon Collection, and Zebra Publishing."),
        "species": index_species,
        "catalog": rows,
    }
    if args.dry_run:
        # Never rewrite a live index with no images recorded.
        print(f"\nDry run: {index_path} left untouched")
    else:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(json.dumps(index, indent=2))
        print(f"\nWrote {index_path}")
    print(f"  {downloaded} downloaded, {fallback} typographic-fallback, {failed} failed")
    print(f"  {cached} of {len(catalog)} catalog plates cached "
          f"({_dir_size_mb(images_dir) / 1000:.2f} GB in {images_dir})")
    if failed:
        print("  Some downloads failed — re-run to retry; the mirror is occasionally flaky.")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
