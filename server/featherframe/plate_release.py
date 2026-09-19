"""The Havell plates as checksummed GitHub Release assets (W-764).

`scripts/fetch_plates.py` used to depend on one stranger's mirror staying up.
The whole edition is now also published under this repo's `plates-v1` release:
one plain tar per hundred plates (JPEGs don't compress, and every part stays
well under GitHub's 2 GB asset limit), the mirror's `data.json` catalog, a
`plates-manifest.json` naming each part's plates and SHA-256, and a
`SHA256SUMS` sidecar for anyone checking by hand.

`pack()` builds the assets from a full local cache (`scripts/pack_plates.py`);
`fetch_parts()` is the install side. Everything on the fetch side soft-fails —
an unreachable release just means the per-plate mirror path runs as before.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tarfile
from pathlib import Path

RELEASE_TAG = "plates-v1"
DEFAULT_BASE_URL = f"https://github.com/wr/featherframe/releases/download/{RELEASE_TAG}"
MANIFEST_NAME = "plates-manifest.json"
CATALOG_NAME = "data.json"
SUMS_NAME = "SHA256SUMS"
LAST_PLATE = 435

# The only member names a part may extract: a flat plate scan, nothing else.
_MEMBER_RE = re.compile(r"^plate-(\d+)-[a-z0-9-]+\.jpg$")


def base_url() -> str:
    """Where the release assets live; a fork or a test points this elsewhere."""
    return os.environ.get("FEATHERFRAME_PLATES_RELEASE_URL", DEFAULT_BASE_URL).rstrip("/")


def part_name(plate: int) -> str:
    """The part a plate ships in: one per hundred, like the mirror's folders."""
    lo = max(1, (plate // 100) * 100)
    hi = LAST_PLATE if lo == 400 else (lo // 100) * 100 + 99
    return f"havell-{lo:03d}-{hi:03d}.tar"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _plain(info: tarfile.TarInfo) -> tarfile.TarInfo:
    """Strip everything that varies between machines so a re-pack of the same
    scans produces the same bytes (and so the same checksums)."""
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mode = 0o644
    return info


def pack(catalog: dict[int, dict], images_dir: Path, out_dir: Path) -> dict:
    """Write the release assets for `catalog` into `out_dir`; return the manifest.

    Refuses to pack an incomplete cache: a release that silently lacks plates
    would turn into typographic fallbacks on every fresh install.
    """
    missing = [m["fileName"] for _, m in sorted(catalog.items())
               if not (images_dir / m["fileName"]).is_file()]
    if missing:
        raise FileNotFoundError(f"{len(missing)} plates not cached (first: {missing[0]}); "
                                f"run fetch_plates.py --all first")
    out_dir.mkdir(parents=True, exist_ok=True)
    groups: dict[str, list[int]] = {}
    for plate in sorted(catalog):
        groups.setdefault(part_name(plate), []).append(plate)

    parts = []
    for name, plates in groups.items():
        dest = out_dir / name
        with tarfile.open(dest, "w", format=tarfile.USTAR_FORMAT) as tar:
            for plate in plates:
                fn = catalog[plate]["fileName"]
                tar.add(images_dir / fn, arcname=fn, filter=_plain)
        parts.append({"name": name, "sha256": _sha256(dest),
                      "bytes": dest.stat().st_size, "plates": plates})

    cat_path = out_dir / CATALOG_NAME
    cat_path.write_text(json.dumps([catalog[p] for p in sorted(catalog)]))
    manifest = {"version": 1, "edition": "havell", "parts": parts,
                "catalog": {"name": CATALOG_NAME, "sha256": _sha256(cat_path)}}
    manifest_path = out_dir / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=1))
    sums = [(p["sha256"], p["name"]) for p in parts]
    sums += [(manifest["catalog"]["sha256"], CATALOG_NAME), (_sha256(manifest_path), MANIFEST_NAME)]
    (out_dir / SUMS_NAME).write_text("".join(f"{h}  {n}\n" for h, n in sums))
    return manifest


def _get_json(session, url: str):
    try:
        with session.get(url, timeout=30) as r:
            return r.json() if r.status_code == 200 else None
    except Exception:  # noqa: BLE001 — unreachable, not JSON: all "no release"
        return None


def fetch_catalog(session, base: str) -> list | None:
    """The released data.json, or None when the release can't be reached."""
    raw = _get_json(session, f"{base}/{CATALOG_NAME}")
    return raw if isinstance(raw, list) else None


def _download_verified(session, url: str, dest: Path, sha256: str) -> bool:
    """Stream `url` to `dest`, hashing as it lands; keep it only if it matches."""
    h = hashlib.sha256()
    try:
        with session.get(url, timeout=60, stream=True) as r:
            if r.status_code != 200:
                return False
            with open(dest, "wb") as fh:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    fh.write(chunk)
                    h.update(chunk)
    except Exception as exc:  # noqa: BLE001
        print(f"       {url} failed: {exc}")
        dest.unlink(missing_ok=True)
        return False
    if h.hexdigest() != sha256:
        print(f"  !  {url}: checksum mismatch — discarded")
        dest.unlink(missing_ok=True)
        return False
    return True


def _extract(tar_path: Path, images_dir: Path) -> None:
    """Unpack plate scans that aren't on disk yet. Only flat, regular
    `plate-N-slug.jpg` members are written — never a path, link or device."""
    with tarfile.open(tar_path, "r:") as tar:
        for info in tar:
            if not info.isfile() or not _MEMBER_RE.match(info.name):
                continue
            dest = images_dir / info.name
            if dest.exists() and dest.stat().st_size > 0:
                continue
            src = tar.extractfile(info)
            if src is None:
                continue
            tmp = dest.with_suffix(dest.suffix + ".part")
            with open(tmp, "wb") as fh:
                for chunk in iter(lambda: src.read(1 << 20), b""):
                    fh.write(chunk)
            tmp.replace(dest)


def fetch_parts(session, base: str, wanted: set[int], catalog: dict[int, dict],
                images_dir: Path) -> set[int]:
    """Download and unpack every part that holds a `wanted` plate. Returns the
    wanted plates that are on disk afterwards (empty if the release is down).

    A part is a few hundred MB: it lands as one temp file beside the images,
    is verified against the manifest before a byte is extracted, and is
    deleted either way.
    """
    if not wanted:
        return set()
    manifest = _get_json(session, f"{base}/{MANIFEST_NAME}")
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        return set()
    images_dir.mkdir(parents=True, exist_ok=True)
    for part in manifest.get("parts", []):
        hits = wanted.intersection(part.get("plates", []))
        if not hits:
            continue
        name = part["name"]
        print(f"  ·  {name}: {len(hits)} plates wanted, {part.get('bytes', 0) / 1e6:.0f} MB…")
        tmp = images_dir / f".{name}.part"
        try:
            if _download_verified(session, f"{base}/{name}", tmp, part["sha256"]):
                _extract(tmp, images_dir)
        except (tarfile.TarError, OSError) as exc:
            print(f"  !  {name}: {exc}")
        finally:
            tmp.unlink(missing_ok=True)
    return {p for p in wanted
            if p in catalog and (images_dir / catalog[p]["fileName"]).is_file()}
