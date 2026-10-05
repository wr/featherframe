"""Hosted mode (W-844): this server as one household's, in a Container that
sleeps between runs.

The household's state is its data dir — the kv SQLite, the pictures, the
outputs, the generated plates — kept in R2 behind the household's front door
(a Durable Object, W-843). The Container has no R2 credentials of its own: it
speaks to the front door's internal API with a per-household key.

    GET    {base}/files                 {"files": {path: sha256}}
    GET    {base}/files/<path>          the bytes
    PUT    {base}/files/<path>          the bytes (X-SHA256)
    DELETE {base}/files/<path>
    POST   {base}/state                 service.hosted_state(): the frames the
                                        front door answers while this sleeps,
                                        and when to wake it next
    POST   {base}/checkins/take         {"checkins": [...]}: what the frames
                                        said while this slept, handed over once

On start the data dir is pulled before the service opens its database; after
every tick (and every request that changed something) what changed is pushed
and the state reported. Nothing else about the server is different: the rules,
the renders and the page are the box's.

A start pulls only what a tick reads (W-915). The big files something asks for
one at a time — a generated illustration, a day's collage sheet, a kept
collage, a past picture full size, a firmware image — stay at the front door
until then: `local()` fetches one, `exists()`, `glob()` and `remove()` see the
ones still there. Off hosted each is the plain filesystem call.

The small files a start does read (sidecars, thumbnails, history's PNGs, the
caches) travel as a few archives, `bundles/*.tar`, one request each way
apiece instead of one per file (W-985): ~85 requests a start became ~15.
History's PNGs, which change with every picture, have an archive of their
own, so the rest is sent again only when one of them changes. What the front
door serves itself (`frames/out/`, `frames/views/`), the pictures, the
database and anything over BUNDLE_MAX_BYTES stay files of their own. An
archive is newer than a file of the same path at the front door: the push
sends the archives before it removes what they replaced.
"""
from __future__ import annotations

import fnmatch
import hashlib
import io
import logging
import os
import sqlite3
import tarfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests

from . import paths

log = logging.getLogger("featherframe.hosted")

TIMEOUT_S = 60
PULL_WORKERS = 8
DB_NAME = "featherframe.db"
# Caches, not state: rebuilt on demand, never pushed. (frames/views/ is
# pushed: the front door serves a viewer's image from it, W-849.)
_SKIP_PREFIXES = ("plate-library/",)
# What stays at the front door until it is read (W-915): the folder and the
# files in it. Sidecars, thumbnails and history's small PNGs come down with
# the rest, so every listing, age and prune reads a local file.
_LAZY = (("generated/", (".png",)),               # read when its species is drawn
         ("collages/", (".png",)),                # read when that day's collage is drawn
         ("frames/collage-days/", (".png",)),     # read when downloaded
         ("frames/history/", (".jpg",)),          # read when zoomed
         ("firmware/", (".bin",)))                # read when a frame updates


def is_lazy(rel: str) -> bool:
    return "/thumbs/" not in rel and any(rel.startswith(d) and rel.endswith(s) for d, s in _LAZY)


# The archives the small files travel in (W-985), by the folder they are in;
# the rest go in REST_BUNDLE.
BUNDLE_DIR = "bundles/"
BUNDLE_MAX_BYTES = 256 * 1024
REST_BUNDLE = BUNDLE_DIR + "files.tar"
_BUNDLES = (("frames/history/", BUNDLE_DIR + "history.tar"),)
# Files of their own whatever their size: what the front door reads itself,
# and the pictures, which change with every one.
_OWN_FILES = ("frames/out/", "frames/views/", "frames/pictures/", BUNDLE_DIR)


def bundle_of(rel: str, size: int) -> Optional[str]:
    """The archive `rel` travels in, or None for a file of its own."""
    if rel == DB_NAME or is_lazy(rel) or size > BUNDLE_MAX_BYTES or rel.startswith(_OWN_FILES):
        return None
    return next((name for prefix, name in _BUNDLES if rel.startswith(prefix)), REST_BUNDLE)


def _tar(files: dict[str, Path]) -> bytes:
    """An archive of `files` (path in the data dir -> file), the same bytes
    for the same contents: sorted, and no times or owners."""
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for rel in sorted(files):
            body = files[rel].read_bytes()
            info = tarfile.TarInfo(rel)
            info.size, info.mode, info.mtime = len(body), 0o644, 0
            tar.addfile(info, io.BytesIO(body))
    return out.getvalue()


def _untar(body: bytes) -> dict[str, bytes]:
    """An archive's files, by path; anything but a plain file is skipped."""
    out = {}
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:") as tar:
        for info in tar:
            if info.isfile():
                f = tar.extractfile(info)
                if f is not None:
                    out[info.name] = f.read()
    return out


def config_from_env() -> Optional[tuple[str, str]]:
    """(base URL, key) when this server is a hosted household's."""
    base = os.environ.get("FEATHERFRAME_HOSTED_URL", "").strip().rstrip("/")
    key = os.environ.get("FEATHERFRAME_HOSTED_KEY", "").strip()
    return (base, key) if base and key else None


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class HostedLink:
    def __init__(self, base: str, key: str, data_dir: Optional[Path] = None,
                 session: Optional[requests.Session] = None) -> None:
        self.base = base.rstrip("/")
        self.data_dir = Path(data_dir or paths.data_dir())
        self.http = session or requests.Session()
        self.http.headers["Authorization"] = f"Bearer {key}"
        self._remote: dict[str, str] = {}              # path -> sha, as the front door has it
        self._bundles: dict[str, dict[str, str]] = {}  # archive -> {path: sha} it holds there
        self._stat: dict[str, tuple] = {}              # path -> (size, mtime_ns, sha)
        self._lazy: set[str] = set()                   # at the front door, not fetched yet
        self._lock = threading.RLock()                 # one sync at a time

    def _url(self, rel: str = "") -> str:
        return f"{self.base}/{rel}" if rel else self.base

    # -- files -----------------------------------------------------------------
    def pull(self) -> int:
        """Bring the data dir up to the front door's copy, but for what waits
        there until it is read (`is_lazy`). Returns files fetched."""
        with self._lock:
            r = self.http.get(self._url("files"), timeout=TIMEOUT_S)
            r.raise_for_status()
            remote = dict(r.json().get("files") or {})
            self._bundles, bundled = {}, set()
            for name in sorted(n for n in remote if n.startswith(BUNDLE_DIR)):
                got = self.http.get(self._url("files/" + quote(name)), timeout=TIMEOUT_S)
                got.raise_for_status()
                self._bundles[name] = self._unpack(_untar(got.content))
                bundled |= set(self._bundles[name])
            todo, lazy = [], set()
            for rel, sha in remote.items():
                if not _safe(rel) or rel.startswith(BUNDLE_DIR) or rel in bundled:
                    continue
                dest = self.data_dir / rel
                if dest.exists() and _sha(dest) == sha:
                    continue
                if is_lazy(rel) and not dest.exists():
                    lazy.add(rel)
                    continue
                todo.append((rel, sha))
            # A few at a time: each is a round trip through the front door,
            # and the Container is paid for while it waits (W-915).
            with ThreadPoolExecutor(max_workers=PULL_WORKERS) as pool:
                list(pool.map(lambda job: self._fetch(*job), todo))
            self._remote, self._lazy = remote, lazy
            log.info("hosted: pulled %d of %d files and %d in %d archive(s) (%d left until read)",
                     len(todo), len(remote), len(bundled), len(self._bundles), len(lazy))
            return len(todo)

    def _unpack(self, files: dict[str, bytes]) -> dict[str, str]:
        """Write an archive's files into the data dir; {path: sha} of them."""
        held = {}
        for rel, body in files.items():
            if not _safe(rel):
                continue
            sha = hashlib.sha256(body).hexdigest()
            dest = self.data_dir / rel
            if not (dest.exists() and _sha(dest) == sha):
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_name(dest.name + ".tmp")
                tmp.write_bytes(body)
                os.replace(tmp, dest)
            st = dest.stat()
            self._stat[rel] = (st.st_size, st.st_mtime_ns, sha)
            held[rel] = sha
        return held

    def _fetch(self, rel: str, sha: str) -> None:
        got = self.http.get(self._url("files/" + quote(rel)), timeout=TIMEOUT_S)
        got.raise_for_status()
        body = got.content
        dest = self.data_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.write_bytes(body)
        os.replace(tmp, dest)
        # Hashed here, while it is in memory: the first push need not read
        # every file back to learn it is unchanged.
        if hashlib.sha256(body).hexdigest() == sha:
            st = dest.stat()
            self._stat[rel] = (st.st_size, st.st_mtime_ns, sha)

    def fetch(self, rel: str) -> bool:
        """One file that waits at the front door, brought down now. False if
        it is not one, or the front door could not hand it over."""
        with self._lock:
            if rel not in self._lazy:
                return False
            try:
                self._fetch(rel, self._remote.get(rel, ""))
            except (requests.RequestException, OSError):
                log.warning("hosted: %s not fetched", rel, exc_info=True)
                return False
            self._lazy.discard(rel)
            return True

    def waiting(self, rel: str) -> bool:
        return rel in self._lazy

    def waiting_in(self, folder: str) -> list[str]:
        """The files still at the front door directly inside `folder`."""
        prefix = folder.rstrip("/") + "/"
        with self._lock:
            return [r for r in self._lazy if r.startswith(prefix) and "/" not in r[len(prefix):]]

    def forget(self, rel: str) -> None:
        """A file removed here that was never fetched: the next push drops it
        from the front door too."""
        with self._lock:
            self._lazy.discard(rel)

    def _local(self) -> dict[str, Path]:
        """Every file that is state, by its path in the data dir. The database
        is a consistent snapshot, never the live file."""
        found: dict[str, Path] = {}
        db = Path(paths.db_path())
        for p in self.data_dir.rglob("*"):
            if not p.is_file() or p.name.endswith((".tmp", "-journal", "-wal", "-shm")):
                continue
            rel = p.relative_to(self.data_dir).as_posix()
            if rel.startswith(_SKIP_PREFIXES) or p == db or rel.startswith(".hosted/"):
                continue
            found[rel] = p
        if db.exists():
            snap = self.data_dir / ".hosted" / DB_NAME
            snap.parent.mkdir(exist_ok=True)
            # Into a new file: a backup over the last one bumps its change
            # counter, so an unchanged database hashed new and was sent again
            # on every sync (W-985).
            snap.unlink(missing_ok=True)
            src = sqlite3.connect(str(db))
            dst = sqlite3.connect(str(snap))
            try:
                src.backup(dst)
            finally:
                src.close()
                dst.close()
            found[DB_NAME] = snap
        return found

    def _hash(self, rel: str, p: Path) -> str:
        st = p.stat()
        known = self._stat.get(rel)
        if known and known[:2] == (st.st_size, st.st_mtime_ns) and rel != DB_NAME:
            return known[2]
        sha = _sha(p)
        self._stat[rel] = (st.st_size, st.st_mtime_ns, sha)
        return sha

    def push(self) -> int:
        """Send the front door whatever changed since it last had it; drop what
        is gone. Returns the number of files written or removed."""
        with self._lock:
            local = self._local()
            own: dict[str, tuple[Path, str]] = {}
            groups: dict[str, dict[str, tuple[Path, str]]] = {}
            for rel, p in local.items():
                self._lazy.discard(rel)           # written here since: this copy is the one
                sha = self._hash(rel, p)
                name = bundle_of(rel, p.stat().st_size)
                (groups.setdefault(name, {}) if name else own)[rel] = (p, sha)
            n = 0
            # The archives first: one replaces the files of its own it held
            # before (the removals below), never the other way round.
            for name in sorted(set(groups) | set(self._bundles)):
                want = {rel: sha for rel, (_, sha) in groups.get(name, {}).items()}
                if want == self._bundles.get(name):
                    continue
                if want:
                    body = _tar({rel: p for rel, (p, _) in groups[name].items()})
                    sha = hashlib.sha256(body).hexdigest()
                    r = self.http.put(self._url("files/" + quote(name)), data=body,
                                      headers={"X-SHA256": sha}, timeout=TIMEOUT_S)
                    r.raise_for_status()
                    self._remote[name], self._bundles[name] = sha, want
                else:
                    r = self.http.delete(self._url("files/" + quote(name)), timeout=TIMEOUT_S)
                    if r.status_code not in (200, 204, 404):
                        r.raise_for_status()
                    self._remote.pop(name, None)
                    self._bundles.pop(name, None)
                n += 1
            for rel, (p, sha) in own.items():
                if self._remote.get(rel) == sha:
                    continue
                r = self.http.put(self._url("files/" + quote(rel)), data=p.read_bytes(),
                                  headers={"X-SHA256": sha}, timeout=TIMEOUT_S)
                r.raise_for_status()
                self._remote[rel] = sha
                n += 1
            # Gone here, or sent in an archive now: the file of its own goes.
            for rel in [r for r in self._remote
                        if r not in own and r not in self._bundles and r not in self._lazy]:
                r = self.http.delete(self._url("files/" + quote(rel)), timeout=TIMEOUT_S)
                if r.status_code not in (200, 204, 404):
                    r.raise_for_status()
                self._remote.pop(rel, None)
                if rel not in local:
                    self._stat.pop(rel, None)
                n += 1
            if n:
                log.info("hosted: pushed %d change(s)", n)
            return n

    # -- the front door ----------------------------------------------------------
    def report(self, state: dict) -> None:
        r = self.http.post(self._url("state"), json=state, timeout=TIMEOUT_S)
        r.raise_for_status()

    def take_checkins(self) -> list:
        r = self.http.post(self._url("checkins/take"), timeout=TIMEOUT_S)
        r.raise_for_status()
        return list(r.json().get("checkins") or [])

    # -- one call from the service ---------------------------------------------
    def take(self, service) -> None:
        """Only what the frames said: a wake takes it before its tick, so a
        frame just paired is added and drawn for in that same tick."""
        try:
            self._apply_checkins(service)
        except (requests.RequestException, OSError, ValueError, sqlite3.Error):
            log.warning("hosted: taking check-ins failed", exc_info=True)

    def settle(self, service, apply_checkins: bool = True) -> None:
        """Take what the frames said, then push what changed and say what the
        front door now answers. A failure is logged and retried on the next
        call: the files stay where they are until they reach it."""
        try:
            if apply_checkins:
                self._apply_checkins(service)
            # The state first: it draws each viewer's image (W-849), which the
            # push then takes along with everything else.
            state = service.hosted_state()
            self.push()
            self.report(state)
        except (requests.RequestException, OSError, ValueError, sqlite3.Error):
            log.warning("hosted: sync with the front door failed", exc_info=True)

    def _apply_checkins(self, service) -> None:
        """Hosted, a frame joins a household only by pairing (its code, or
        USB): a check-in from one this household does not have — one queued
        just before its owner removed it — is not a frame asking to connect,
        and is dropped. Only a pairing's `add` makes a row."""
        from .app import parse_checkin     # the one reading of a kit's headers
        for c in self.take_checkins():
            if isinstance(c.get("viewer"), dict):
                # A TRMNL, an e-reader or a tablet page (W-849).
                vid = str(c.get("id") or "")
                if not c.get("add") and service.frames.get(vid.upper()[:40]) is None:
                    continue
                service.apply_viewer_checkin(vid, c["viewer"], ip=c.get("ip"), at=c.get("at"))
                if c.get("add"):
                    service.answer_frame(vid.upper()[:40], "add")
                continue
            parsed = parse_checkin(_lower(c.get("headers")))
            fid = (parsed.get("device_id") or "")[:40]
            if not c.get("add") and (not fid or service.frames.get(fid) is None):
                continue
            service.apply_checkin(parsed, ip=c.get("ip"), user_agent=c.get("ua"),
                                  result=str(c.get("result") or "304"),
                                  etag=c.get("etag"), at=c.get("at"))
            if c.get("add") and parsed.get("device_id"):
                # The owner paired it (W-845): typing its code is the
                # answer, so it is added without a second step.
                service.answer_frame(parsed["device_id"][:40], "add")

def _lower(headers) -> dict:
    return {str(k).lower(): str(v) for k, v in (headers or {}).items()}


def _safe(rel: str) -> bool:
    """A path from the front door stays inside the data dir."""
    parts = Path(rel).parts
    return bool(parts) and not Path(rel).is_absolute() and ".." not in parts


# -- the data dir, as a hosted server has it (W-915) ---------------------------
# The link this process pulled with, set once at start; None off hosted.
_active: Optional[HostedLink] = None


def activate(link: Optional[HostedLink]) -> None:
    global _active
    _active = link


def link() -> Optional[HostedLink]:
    """The link this process pulled with, or None off hosted."""
    return _active


def _rel(path: Path) -> Optional[str]:
    if _active is None:
        return None
    try:
        return Path(path).relative_to(_active.data_dir).as_posix()
    except ValueError:
        return None


def local(path: Path) -> Path:
    """`path`, fetched first if it waits at the front door. A fetch that
    fails leaves it missing here; `exists()` still says it is there."""
    rel = _rel(path)
    if rel is not None and not Path(path).exists():
        _active.fetch(rel)
    return path


def exists(path: Path) -> bool:
    """Here, or at the front door waiting to be read."""
    if Path(path).exists():
        return True
    rel = _rel(path)
    return rel is not None and _active.waiting(rel)


def glob(folder: Path, pattern: str) -> list[Path]:
    """`folder.glob(pattern)`, and the files still at the front door."""
    found = set(Path(folder).glob(pattern))
    rel = _rel(folder)
    if rel is not None:
        found |= {_active.data_dir / r for r in _active.waiting_in(rel)
                  if fnmatch.fnmatch(r.rsplit("/", 1)[-1], pattern)}
    return list(found)


def waiting_under(folder: Path) -> list[Path]:
    """Every file under `folder`, at any depth, still at the front door."""
    rel = _rel(folder)
    if rel is None:
        return []
    prefix = rel.rstrip("/") + "/"
    with _active._lock:
        return [_active.data_dir / r for r in _active._lazy if r.startswith(prefix)]


def remove(path: Path) -> None:
    """Delete `path` here and, on the next push, at the front door."""
    Path(path).unlink(missing_ok=True)
    rel = _rel(path)
    if rel is not None:
        _active.forget(rel)
