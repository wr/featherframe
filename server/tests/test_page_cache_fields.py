"""What the cached webapp needs from the server (W-946): its page build, and
epoch times the page turns into "4 min ago" itself, so a copy served later
still says the right age."""
from __future__ import annotations

from datetime import datetime, timedelta

from tests._frames import add_kit

from featherframe import page_build
from featherframe.service import FeatherframeService


def _svc(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    return FeatherframeService()


def test_page_build_is_12_hex_and_follows_the_template(tmp_path, monkeypatch):
    b = page_build.build()
    assert len(b) == 12 and int(b, 16) >= 0
    tpl = tmp_path / "templates"
    tpl.mkdir()
    (tpl / "index.html").write_text("a")
    one = page_build.build(tpl, tmp_path / "nostatic")
    (tpl / "index.html").write_text("b")
    assert page_build.build(tpl, tmp_path / "nostatic") != one


def test_hosted_state_carries_the_page_build(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    assert svc.hosted_state()["page_build"] == page_build.build()


def test_a_frame_card_carries_its_last_check_in_as_epoch_seconds(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    then = datetime.now().replace(microsecond=0) - timedelta(minutes=7)
    add_kit(svc, reported={"last_checkin": then.isoformat(timespec="seconds")})
    card = svc.frames_list()[0]["card"]
    assert card["last_checkin_ts"] == int(then.timestamp())


def test_a_frame_never_heard_from_has_no_epoch(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    add_kit(svc)
    assert svc.frames_list()[0]["card"]["last_checkin_ts"] is None


def test_a_frame_view_carries_its_preview_etag(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    add_kit(svc)
    view = svc.frames_list()[0]
    assert "preview_etag" in view and "queued_until" in view
