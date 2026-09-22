"""The two pictures (W-833).

A frame is a frame — the kit on the wall, a second kit, a TRMNL, a tablet —
and every one of them shows a *picture*. There are exactly two: `plates`, the
bird that was just heard drawn as an Audubon plate, and `collage`, the day.
They are the same kind of thing and are kept the same way.

A picture owns what it is of (`meta`), what identifies it (`etag`), and the
composed sheet — plus the sheet's colour twin, when a colour screen is
watching — that every frame's output is finished from. It is drawn only while
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
        # Draws this same sheet again with the art in colour, for the screens
        # that show it in colour. In memory: a restart renders again instead.
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
               color_sheet: Optional[Image.Image] = None, recompose=None) -> None:
        """This picture is now `sheet`. Finishing it for a frame's panel is
        that frame's own business (`service._draw_frame`)."""
        self.meta = meta
        self.etag = etag
        self.key = key
        self.at = now.isoformat(timespec="seconds")
        self.recompose = recompose
        if sheet is not None or color_sheet is not None:
            write_sheet(self.sheet_path, sheet)
            write_sheet(self.color_sheet_path, color_sheet)

    def drop(self) -> None:
        """Nobody shows it any more: stop paying for it."""
        self.meta = {}
        self.etag = self.key = self.at = None
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
        # The picture "the frame" means on a server that has no frame yet: the
        # page still asks, and the answer has to be something. A server with a
        # kit takes it from that kit instead (service._default_shows).
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
        self.save()
