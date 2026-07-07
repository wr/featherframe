"""Shared test fixtures. Puts server/ on the path so `featherframe` imports."""
from __future__ import annotations

import sys
from pathlib import Path

_SERVER = Path(__file__).resolve().parents[1]
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))

import pytest  # noqa: E402

from tests._fixtures import create_birds_db  # noqa: E402


@pytest.fixture
def birds_db(tmp_path) -> str:
    """A fixture birds.db with the default day's detections."""
    return str(create_birds_db(tmp_path / "birds.db"))


@pytest.fixture
def missing_db(tmp_path) -> str:
    return str(tmp_path / "does_not_exist.db")
