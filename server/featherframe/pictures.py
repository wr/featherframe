"""The two pictures (W-833).

A frame is a frame — the kit on the wall, a second kit, a TRMNL, a tablet —
and every one of them shows a *picture*. There are exactly two: `plates`, the
bird that was just heard drawn as an Audubon plate, and `collage`, the day.
Before this the wall's picture was the server's state and the other one was a
"side picture" bolted beside it (W-831); they are the same kind of thing and
are kept the same way here.

A picture owns what it is of (`meta`), what identifies it (`etag`), and the
composed sheet — plus the sheet's colour twin, when a colour screen is
watching — that every screen's render is drawn from. It is drawn only while
some frame shows it, and dropped when the last one looks away.

The decisions — which picture a frame shows, and of what — stay in the
service. This is the state they leave behind.
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Optional

from PIL import Image

from . import paths

log = logging.getLogger("featherframe.pictures")

PLATES, COLLAGE = "plates", "collage"
KINDS = (PLATES, COLLAGE)

KEY = "pictures"                      # our kv row: {kind: row}
# The stores this one replaces. They are read once, by `load`, and never
# written again; they stay in the DB so a rollback still finds them.
LEGACY_FRAME_KEY = "current_frame"    # the resident frame's meta
LEGACY_SIDE_KEY = "side_pictures"     # W-831's second picture
_LEGACY_SHEET = "current_sheet.png"
_LEGACY_SHEET_COLOR = "current_sheet_color.png"

# Which picture a committed render's `mode` belongs to. "welcome" is a plate
# with nothing to say yet, so it belongs to the plates picture.
_KIND_OF_MODE = {"single": PLATES, "welcome": PLATES, "collage": COLLAGE}


def kind_of_mode(mode: Optional[str]) -> str:
    """The picture a frame's `mode` names. Anything unknown is a plate: that
    is what a frame with nothing else to show has."""
    return _KIND_OF_MODE.get(str(mode or ""), PLATES)


def etag_for(sheet: Image.Image) -> str:
    """A picture's identity when no framebuffer was packed for it: a hash of
    the composed sheet's own pixels, the same width as a framebuffer ETag."""
    return hashlib.sha256(sheet.tobytes()).hexdigest()[:16]


def write_sheet(target, sheet: Optional[Image.Image]) -> None:
    """Keep (or clear) one sheet on disk. Best-effort, like a thumbnail:
    without it a screen's render falls back to the preview, and a half-written
    file would be worse than none."""
    try:
        if sheet is None:
            target.unlink(missing_ok=True)
            return
        tmp = target.with_suffix(".tmp")
        sheet.save(tmp, format="PNG", compress_level=1)
        os.replace(tmp, target)
    except Exception:  # noqa: BLE001
        log.warning("%s not saved", target.name, exc_info=True)
        target.unlink(missing_ok=True)


class Picture:
    """One of the two. A state holder with a few methods — every decision
    about it is made by the service."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        # What it is of: mode, label, species_key, rendered_at, novelty,
        # held_since, the footnote, collage_at. Persisted.
        self.meta: dict = {}
        self.etag: Optional[str] = None
        self.key: Optional[str] = None     # what was drawn: a detection, a date
        self.at: Optional[str] = None      # when it was drawn, ISO
        # The framebuffer of the kit that shows this picture, while it does.
        # In memory only (`current.fff` is its copy on disk); step 2b turns it
        # into one output per frame.
        self.frame: Optional[bytes] = None
        # Draws this same sheet again with the art in colour, for a gray
        # frame's colour screens. In memory: a restart renders again instead.
        self.recompose = None

    # -- on disk -----------------------------------------------------------
    @property
    def dir(self):
        d = paths.frames_dir() / "pictures" / self.kind
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def sheet_path(self):
        return self.dir / "sheet.png"

    @property
    def color_sheet_path(self):
        return self.dir / "sheet_color.png"

    def sheets(self, color: bool = False) -> list:
        """The files this picture can be drawn again from, best first. A
        colour ask falls back to the gray sheet: colour is a nicety."""
        want = ([self.color_sheet_path] if color else []) + [self.sheet_path]
        return [p for p in want if p.exists()]

    def has_color(self) -> bool:
        return self.color_sheet_path.exists()

    # -- state -------------------------------------------------------------
    def commit(self, meta: dict, etag: str, now, key: Optional[str] = None,
               sheet: Optional[Image.Image] = None,
               color_sheet: Optional[Image.Image] = None,
               frame: Optional[bytes] = None, recompose=None) -> None:
        """This picture is now `sheet`. `frame` is the packed framebuffer when
        the kit that shows it was drawn for in the same pass."""
        self.meta = meta
        self.etag = etag
        self.key = key
        self.at = now.isoformat(timespec="seconds")
        self.recompose = recompose
        if frame is not None:
            self.frame = frame
        if sheet is not None or color_sheet is not None:
            write_sheet(self.sheet_path, sheet)
            write_sheet(self.color_sheet_path, color_sheet)

    def drop(self) -> None:
        """Nobody shows it any more: stop paying for it."""
        self.meta = {}
        self.etag = self.key = self.at = self.frame = None
        self.recompose = None
        for f in (self.sheet_path, self.color_sheet_path):
            f.unlink(missing_ok=True)

    # -- persistence -------------------------------------------------------
    def row(self) -> dict:
        return {"etag": self.etag, "key": self.key, "at": self.at, "meta": self.meta}

    def restore(self, row: dict) -> None:
        if not isinstance(row, dict):
            return
        meta = row.get("meta")
        self.meta = dict(meta) if isinstance(meta, dict) else {}
        self.etag = row.get("etag") or None
        self.key = row.get("key") or None
        self.at = row.get("at") or None


class Pictures:
    """Both pictures and the one kv row they live in. There are two of them,
    read on every request, so a dict is the whole data structure."""

    def __init__(self, db) -> None:
        self.db = db
        self._by_kind = {kind: Picture(kind) for kind in KINDS}
        # Which picture the primary kit last had painted on its glass. It is
        # not what the settings say it should show — that is a decision, and
        # this is what happened.
        self.shown: str = PLATES
        self.load()

    def __getitem__(self, kind: str) -> Picture:
        return self._by_kind[kind]

    def __iter__(self):
        return iter(self._by_kind)

    def values(self):
        return self._by_kind.values()

    def items(self):
        return self._by_kind.items()

    def save(self) -> None:
        row = {kind: pic.row() for kind, pic in self.items()}
        row["shown"] = self.shown
        self.db.set(KEY, row)

    def load(self) -> None:
        rows = self.db.get(KEY)
        if isinstance(rows, dict) and rows:
            for kind, row in rows.items():
                if kind in KINDS:
                    self[kind].restore(row)
            if rows.get("shown") in KINDS:
                self.shown = rows["shown"]
            return
        self._adopt_legacy()
        self.save()

    # -- migration ---------------------------------------------------------
    def _adopt_legacy(self) -> None:
        """An install upgrading mid-flight has the resident frame's meta and
        sheet, and maybe W-831's side picture. Adopt them into the picture
        each one belongs to, so nothing is re-rendered and the wall's ETag
        does not move across the upgrade. The old files are copied, not moved:
        a rollback still finds them."""
        frames = paths.frames_dir()
        meta = self.db.get(LEGACY_FRAME_KEY, {})
        if isinstance(meta, dict) and meta.get("etag"):
            pic = self[kind_of_mode(meta.get("mode"))]
            self.shown = pic.kind
            pic.meta = dict(meta)
            pic.etag = str(meta["etag"])
            pic.at = meta.get("rendered_at")
            _copy(frames / _LEGACY_SHEET, pic.sheet_path)
            _copy(frames / _LEGACY_SHEET_COLOR, pic.color_sheet_path)
            log.info("adopted the resident frame as the %s picture", pic.kind)
        side = self.db.get(LEGACY_SIDE_KEY, {})
        for kind, row in (side if isinstance(side, dict) else {}).items():
            if kind not in KINDS or not isinstance(row, dict) or self[kind].etag:
                continue
            pic = self[kind]
            pic.etag = row.get("etag") or None
            pic.key = row.get("key") or None
            pic.at = row.get("at") or None
            pic.meta = {"mode": "collage" if kind == COLLAGE else "single"}
            _copy(frames / f"side_{kind}_sheet.png", pic.sheet_path)
            _copy(frames / f"side_{kind}_sheet_color.png", pic.color_sheet_path)
            log.info("adopted the side %s as the %s picture", kind, kind)


def _copy(src, dst) -> None:
    if not src.exists() or dst.exists():
        return
    try:
        tmp = dst.with_suffix(".tmp")
        tmp.write_bytes(src.read_bytes())
        os.replace(tmp, dst)
    except OSError:
        log.warning("%s not adopted", src.name, exc_info=True)
