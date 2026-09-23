"""Official firmware releases, offered to the frames (W-838).

"Official" is one thing: the latest non-draft, non-prerelease release of
wr/featherframe (GitHub's `releases/latest` is exactly that) carrying a
`firmware-manifest.json` (firmware/tools/release_assets.py) — a version, and
per kit its board string and every file's size and SHA-256. Nothing else is
ever offered, and no image is served that does not match its manifest.

The check runs from the service's tick at most once a day and soft-fails: an
unreachable GitHub keeps the last manifest. Images are downloaded ahead of the
check-in that wants them (a frame's request never waits on GitHub), into
`data/firmware/<version>/`, and only once verified.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

KEY = "firmware_release"
REPO = "wr/featherframe"
MANIFEST_NAME = "firmware-manifest.json"
CHECK_EVERY = timedelta(hours=24)
RETRY_AFTER = timedelta(hours=1)     # after a failed check
TIMEOUT_S = 10
MAX_IMAGE_BYTES = 8 << 20            # an app slot is 6.5 MB on the 16 MB layout
MAX_NOTES = 20000                    # a release's notes, as kept for the page
_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_NAME = re.compile(r"^featherframe-[a-z0-9]+-\d+\.\d+\.\d+(-[a-z0-9_]+)?\.bin$")


def latest_url() -> str:
    """Where the latest release is asked for; a fork or a test points this
    elsewhere, and "off" (the test suite) never asks at all."""
    url = os.environ.get("FEATHERFRAME_RELEASES_URL",
                         f"https://api.github.com/repos/{REPO}/releases/latest").strip()
    return "" if url.lower() in ("", "off", "0") else url


def parse_version(value) -> Optional[tuple]:
    """(major, minor, patch) for an official version; None for a dev build
    ("2026.09.22+a1b2c3d", "dev", a TRMNL's user agent, nothing)."""
    m = _VERSION.match(str(value or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def release_url(version) -> Optional[str]:
    """The GitHub release an official version came from; None for a dev build."""
    if parse_version(version) is None:
        return None
    return f"https://github.com/{REPO}/releases/tag/v{str(version).strip()}"


def is_newer(a, b) -> bool:
    """Whether official version `a` is newer than `b`. A dev build is never
    newer than anything, and anything official is newer than a dev build."""
    va, vb = parse_version(a), parse_version(b)
    if va is None:
        return False
    return vb is None or va > vb


def _valid_manifest(man, tag: str) -> bool:
    if not isinstance(man, dict) or parse_version(man.get("version")) is None:
        return False
    if man.get("tag") != tag or man.get("tag") != f"v{man['version']}":
        return False
    kits = man.get("kits")
    if not isinstance(kits, list) or not kits:
        return False
    for kit in kits:
        if not isinstance(kit, dict) or not kit.get("board") or not kit.get("kit"):
            return False
        files = [kit.get("app")] + list(kit.get("parts") or [])
        for f in files:
            if not (isinstance(f, dict) and _NAME.match(str(f.get("name") or ""))
                    and re.fullmatch(r"[0-9a-f]{64}", str(f.get("sha256") or ""))
                    and isinstance(f.get("size"), int) and 0 < f["size"] <= MAX_IMAGE_BYTES):
                return False
    return True


class ReleaseStore:
    """The latest official release as this server knows it, and its images."""

    def __init__(self, db, data_dir: Path, http=None) -> None:
        self.db = db
        self.root = Path(data_dir) / "firmware"
        if http is None:
            import requests
            http = requests
        self.http = http

    # -- the manifest --------------------------------------------------------
    def state(self) -> dict:
        st = self.db.get(KEY)
        return st if isinstance(st, dict) else {}

    def manifest(self) -> Optional[dict]:
        return self.state().get("manifest")

    def about(self) -> dict:
        """The release's name, notes (Markdown, as GitHub has them) and page:
        what the Update dialog shows. Empty until a release is known."""
        if not self.manifest():
            return {}
        about = self.state().get("about")
        return dict(about) if isinstance(about, dict) else {}

    def version(self) -> Optional[str]:
        man = self.manifest()
        return man.get("version") if man else None

    def check(self, now: datetime, force: bool = False) -> Optional[dict]:
        """Ask GitHub for the latest release, at most daily (hourly after a
        failure) unless forced. Returns the manifest in force afterwards."""
        st = self.state()
        url = latest_url()
        if not url:
            return st.get("manifest")
        if not force:
            try:
                last = datetime.fromisoformat(str(st.get("checked_at")))
                wait = RETRY_AFTER if st.get("error") else CHECK_EVERY
                if now - last < wait:
                    return st.get("manifest")
            except ValueError:
                pass
        stamp = now.isoformat(timespec="seconds")
        try:
            rel = self._get_json(url, missing_ok=True)
            if rel is None:
                # No release at all yet: nothing to offer, and nothing wrong.
                self.db.set(KEY, {**st, "checked_at": stamp, "error": None})
                return st.get("manifest")
            if rel.get("draft") or rel.get("prerelease"):
                raise ValueError("latest release is a draft or prerelease")
            assets = {a.get("name"): a.get("browser_download_url")
                      for a in rel.get("assets") or [] if isinstance(a, dict)}
            if MANIFEST_NAME not in assets:
                # A release with no firmware in it (plates-v1): nothing to offer.
                self.db.set(KEY, {**st, "checked_at": stamp, "error": None})
                return st.get("manifest")
            man = self._get_json(assets[MANIFEST_NAME])
            if not _valid_manifest(man, str(rel.get("tag_name") or "")):
                raise ValueError("firmware-manifest.json does not match its release")
            urls = {n: u for n, u in assets.items() if n and _NAME.match(n) and u}
            # What the owner reads before pressing Update: the release's own
            # name and notes, as GitHub has them.
            about = {"name": str(rel.get("name") or rel.get("tag_name") or "")[:120],
                     "notes": str(rel.get("body") or "")[:MAX_NOTES],
                     "url": str(rel.get("html_url") or ""),
                     "published_at": str(rel.get("published_at") or "")}
            self.db.set(KEY, {"checked_at": stamp, "error": None, "manifest": man,
                              "urls": urls, "about": about})
            if man.get("version") != (st.get("manifest") or {}).get("version"):
                log.info("firmware release %s is available", man["version"])
                self._prune(man["version"])
            return man
        except Exception as exc:  # noqa: BLE001 — never let GitHub break a tick
            log.warning("firmware release check failed: %s", exc)
            self.db.set(KEY, {**st, "checked_at": stamp, "error": str(exc)[:200]})
            return st.get("manifest")

    def _get_json(self, url: str, missing_ok: bool = False) -> Optional[dict]:
        r = self.http.get(url, timeout=TIMEOUT_S,
                          headers={"Accept": "application/vnd.github+json",
                                   "User-Agent": "featherframe-server"})
        if missing_ok and r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise ValueError(f"{url}: not a JSON object")
        return data

    # -- one kit -------------------------------------------------------------
    def kit_for_board(self, board) -> Optional[dict]:
        """The release's build for a board string (X-Board), or None."""
        man = self.manifest()
        board = str(board or "").strip()
        if not man or not board:
            return None
        for kit in man.get("kits") or []:
            if kit.get("board") == board:
                return kit
        return None

    def kit(self, name: str) -> Optional[dict]:
        man = self.manifest()
        for kit in (man or {}).get("kits") or []:
            if kit.get("kit") == name:
                return kit
        return None

    # -- files ---------------------------------------------------------------
    def _path(self, entry: dict) -> Path:
        return self.root / str(self.version()) / entry["name"]

    def cached(self, entry: Optional[dict]) -> Optional[Path]:
        """The verified file on disk, or None. Verification happens once, on
        download; a file here was renamed into place only after it passed."""
        if not entry or not self.version():
            return None
        path = self._path(entry)
        try:
            return path if path.stat().st_size == entry["size"] else None
        except OSError:
            return None

    def ensure(self, entry: Optional[dict], board: Optional[str] = None) -> Optional[Path]:
        """Download one file of the release if it is not here yet, and keep it
        only if its size and SHA-256 match the manifest — and, for an app
        image, it is an ESP image carrying its board's string."""
        have = self.cached(entry)
        if have is not None or not entry:
            return have
        url = (self.state().get("urls") or {}).get(entry["name"])
        if not url:
            return None
        dest = self._path(entry)
        tmp = dest.with_suffix(".part")
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            r = self.http.get(url, timeout=TIMEOUT_S * 6, stream=True,
                              headers={"User-Agent": "featherframe-server"})
            r.raise_for_status()
            h, n = hashlib.sha256(), 0
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(64 * 1024):
                    n += len(chunk)
                    if n > MAX_IMAGE_BYTES:
                        raise ValueError("larger than any firmware image")
                    h.update(chunk)
                    fh.write(chunk)
            if n != entry["size"] or h.hexdigest() != entry["sha256"]:
                raise ValueError("does not match the release manifest")
            if board is not None:
                data = tmp.read_bytes()
                if not data.startswith(b"\xe9"):
                    raise ValueError("not an ESP image")
                if board.encode("ascii", "ignore") not in data:
                    raise ValueError(f"not a {board} build")
            os.replace(tmp, dest)
            log.info("firmware %s downloaded and verified", entry["name"])
            return dest
        except Exception as exc:  # noqa: BLE001
            log.warning("firmware download %s failed: %s", entry.get("name"), exc)
            try:
                tmp.unlink()
            except OSError:
                pass
            return None

    def app_for_board(self, board, download: bool = False) -> Optional[Path]:
        """The verified OTA image for a board, if this release has one."""
        kit = self.kit_for_board(board)
        if not kit:
            return None
        return self.ensure(kit["app"], kit["board"]) if download else self.cached(kit["app"])

    # -- USB install (W-840) ---------------------------------------------------
    def flash_manifest(self, kit_name: str) -> Optional[dict]:
        """One kit as esp-web-tools reads it. Its parts are bootloader,
        partitions, boot_app0 and the app at their offsets — never NVS, so a
        board keeps its Wi-Fi unless the owner ticks Erase. Paths are relative
        to the manifest's own URL."""
        kit = self.kit(kit_name)
        if not kit or not kit.get("parts"):
            return None
        return {"name": f"Featherframe ({kit_name.upper()})", "version": self.version(),
                "new_install_prompt_erase": True,
                "builds": [{"chipFamily": kit.get("chip") or "ESP32-S3",
                            "parts": [{"path": p["name"], "offset": p["offset"]}
                                      for p in kit["parts"]]}]}

    def part(self, kit_name: str, name: str) -> Optional[Path]:
        """One USB-install part of a kit, downloaded and verified on first ask."""
        kit = self.kit(kit_name)
        entry = next((p for p in (kit or {}).get("parts") or [] if p.get("name") == name), None)
        if entry is None:
            return None
        app = kit.get("app") or {}
        return self.ensure(entry, kit["board"] if entry["name"] == app.get("name") else None)

    def _prune(self, keep: str) -> None:
        """Only the current release's images are kept."""
        try:
            for d in self.root.iterdir():
                if d.is_dir() and d.name != keep:
                    shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass
