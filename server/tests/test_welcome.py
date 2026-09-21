"""The welcome plate (W-734): a fresh install hangs a rendered frame, not a
503 and a broken preview, until the first bird replaces it."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

from featherframe.render import welcome
from featherframe.service import FeatherframeService
from featherframe.render import pipeline as pipeline  # noqa: E402
from tests._frames import FRAME_ID, add_kit


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    service = FeatherframeService()
    service.source.db_path = str(tmp_path / "missing.db")   # source unreachable
    add_kit(service)
    pipeline.DITHER_OVERRIDE = "none"
    yield service


def _ink(img):
    return int((np.asarray(img) < 128).sum())


def test_fresh_install_serves_a_welcome_plate_not_503(svc):
    assert svc._frame_bytes is None
    svc._ensure_initial_frame()
    status, body, etag = svc.get_frame(FRAME_ID, None)
    assert status == 200 and body and etag
    assert svc._meta["mode"] == "welcome"
    assert svc._meta["source_ok"] is False
    assert svc.current_png_bytes()          # the dashboard preview has a picture
    assert svc.get_frame(FRAME_ID, etag)[0] == 304   # and the device 304s on it after


def test_welcome_rerenders_once_when_the_source_appears(svc, monkeypatch):
    svc._ensure_initial_frame()
    before = svc.current_etag()
    monkeypatch.setattr(svc.source, "available", lambda: True)
    svc.tick()
    mid = svc.current_etag()
    assert mid != before
    assert svc._meta["mode"] == "welcome" and svc._meta["source_ok"] is True
    svc.tick()
    assert svc.current_etag() == mid        # no render churn while still waiting


def test_first_bird_replaces_the_welcome_plate(svc):
    svc._ensure_initial_frame()
    svc.force_test_detection("Northern Cardinal", "Cardinalis cardinalis")
    assert svc._meta["mode"] == "single"
    assert svc._meta["label"].startswith("Northern Cardinal")


def test_listening_since_survives_a_restart(svc):
    svc._ensure_initial_frame()
    since = svc.db.get("welcome_since")
    assert since
    again = FeatherframeService(db=svc.db)
    again._ensure_initial_frame()
    assert again._meta["mode"] == "welcome"
    assert again.db.get("welcome_since") == since


def test_welcome_plate_says_when_and_whether_the_source_is_up():
    since = datetime(2026, 9, 11, 23, 32)
    down = welcome.render_welcome(since, source_ok=False, now=since)
    up = welcome.render_welcome(since, source_ok=True, now=since)
    assert _ink(down) > 2000 and _ink(up) > 2000
    assert down.tobytes() != up.tobytes()
    assert welcome.since_words(since) == "Listening since 11 September, 11:32 pm"
