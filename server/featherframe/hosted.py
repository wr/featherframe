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
"""
from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests

from . import paths

log = logging.getLogger("featherframe.hosted")

TIMEOUT_S = 60
DB_NAME = "featherframe.db"
# Caches, not state: rebuilt on demand, never pushed. (frames/views/ is
# pushed: the front door serves a viewer's image from it, W-849.)
_SKIP_PREFIXES = ("plate-library/",)


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
        self._stat: dict[str, tuple] = {}              # path -> (size, mtime_ns, sha)
        self._lock = threading.Lock()                  # one sync at a time

    def _url(self, rel: str = "") -> str:
        return f"{self.base}/{rel}" if rel else self.base

    # -- files -----------------------------------------------------------------
    def pull(self) -> int:
        """Bring the data dir up to the front door's copy. Returns files fetched."""
        with self._lock:
            r = self.http.get(self._url("files"), timeout=TIMEOUT_S)
            r.raise_for_status()
            remote = dict(r.json().get("files") or {})
            fetched = 0
            for rel, sha in remote.items():
                if not _safe(rel):
                    continue
                dest = self.data_dir / rel
                if dest.exists() and _sha(dest) == sha:
                    continue
                got = self.http.get(self._url("files/" + quote(rel)), timeout=TIMEOUT_S)
                got.raise_for_status()
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_name(dest.name + ".tmp")
                tmp.write_bytes(got.content)
                os.replace(tmp, dest)
                fetched += 1
            self._remote = remote
            log.info("hosted: pulled %d of %d files", fetched, len(remote))
            return fetched

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
            n = 0
            for rel, p in local.items():
                sha = self._hash(rel, p)
                if self._remote.get(rel) == sha:
                    continue
                r = self.http.put(self._url("files/" + quote(rel)), data=p.read_bytes(),
                                  headers={"X-SHA256": sha}, timeout=TIMEOUT_S)
                r.raise_for_status()
                self._remote[rel] = sha
                n += 1
            for rel in [r for r in self._remote if r not in local]:
                r = self.http.delete(self._url("files/" + quote(rel)), timeout=TIMEOUT_S)
                if r.status_code not in (200, 204, 404):
                    r.raise_for_status()
                self._remote.pop(rel, None)
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
        from .app import parse_checkin     # the one reading of a kit's headers
        for c in self.take_checkins():
            if isinstance(c.get("viewer"), dict):
                # A TRMNL, an e-reader or a tablet page (W-849).
                vid = str(c.get("id") or "")
                service.apply_viewer_checkin(vid, c["viewer"], ip=c.get("ip"), at=c.get("at"))
                if c.get("add"):
                    service.answer_frame(vid.upper()[:40], "add")
                continue
            parsed = parse_checkin(_lower(c.get("headers")))
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
