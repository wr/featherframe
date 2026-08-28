"""Filesystem locations, all overridable by env so install.sh can place state
wherever it likes on the Pi without touching code.

Layout (defaults):
  data_dir/                 FEATHERFRAME_DATA_DIR  (state that changes)
    featherframe.db         our own config/state DB
    frames/current.fff      last packed framebuffer served to the device
    frames/current.png      human-viewable preview of the current frame
  plates_dir/               FEATHERFRAME_PLATES_DIR (downloaded plate assets)
    index.json              species -> plate mapping (written by fetch_plates)
    img/plate-XXX-*.jpg     the plate images
  <package>/fonts           bundled EB Garamond (read-only, ships with code)
"""
from __future__ import annotations

import os
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent
_REPO_SERVER_DIR = _PKG_DIR.parent  # .../server


def _env_path(name: str, default: Path) -> Path:
    val = os.environ.get(name)
    return Path(val).expanduser() if val else default


def data_dir() -> Path:
    d = _env_path("FEATHERFRAME_DATA_DIR", _REPO_SERVER_DIR / "data")
    d.mkdir(parents=True, exist_ok=True)
    return d


def frames_dir() -> Path:
    d = data_dir() / "frames"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return _env_path("FEATHERFRAME_DB", data_dir() / "featherframe.db")


def generated_dir() -> Path:
    """AI-generated plate cache. Lives under data_dir so a generated plate is
    state that survives deploys and is never re-bought."""
    d = data_dir() / "generated"
    d.mkdir(parents=True, exist_ok=True)
    return d


def collages_dir() -> Path:
    """Nightly day-in-review composite sheets, one per date."""
    d = data_dir() / "collages"
    d.mkdir(parents=True, exist_ok=True)
    return d


def plates_dir() -> Path:
    d = _env_path("FEATHERFRAME_PLATES_DIR", _REPO_SERVER_DIR / "plates")
    return d


def plate_index_path() -> Path:
    return plates_dir() / "index.json"


def plate_images_dir() -> Path:
    return plates_dir() / "img"


def fonts_dir() -> Path:
    return _PKG_DIR / "fonts"


def templates_dir() -> Path:
    return _REPO_SERVER_DIR / "templates"


def static_dir() -> Path:
    return _REPO_SERVER_DIR / "static"


def test_output_dir() -> Path:
    d = _env_path("FEATHERFRAME_TEST_OUTPUT", _REPO_SERVER_DIR.parent / "test_output")
    d.mkdir(parents=True, exist_ok=True)
    return d
