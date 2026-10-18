"""First run (W-880): what a new owner meets, in the order they meet it, reads
as intended rather than unfinished. Nothing set up is not a fault; a frame
asking to be added is good news; the placeholder before the first detection
is not a picture of one."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from starlette.testclient import TestClient

from featherframe.config import Config, save_config
from featherframe.render import pipeline
from tests._frames import EE03_PANEL
from tests.test_pushed_source import _go

KIT = {"X-Device-Id": "AA:AA:AA:00:00:03", "X-Panel": EE03_PANEL,
       "X-Board": "XIAO ESP32-S3 Plus + EE03"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    # No BirdNET-Pi database at the default path: a fresh install, not a fault.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.app import app
    from featherframe.service import FeatherframeService
    pipeline.DITHER_OVERRIDE = "none"
    svc = FeatherframeService()
    svc._render_welcome(svc._clock(), svc.source.available())
    app.state.service, app.state.hosted = svc, None
    yield TestClient(app)
    app.state.hosted = None


def _hosted(client):
    client.app.state.hosted = type("Link", (), {"settle": lambda *a: None})()


def _source(client, **cfg):
    svc = client.app.state.service
    save_config(svc.db, Config(ingest_token="tok", **cfg))
    svc.reload_config()
    return svc


def _src_head(html):
    return html.split('id="set-source"')[1].split("</summary>")[0]


# -- the detection source, before anything has arrived -----------------------
def test_the_default_database_missing_is_not_set_up_not_a_fault(client):
    svc = client.app.state.service
    st = client.get("/api/status").json()
    assert st["source_unset"] is True and st["birdnet_available"] is False
    head = _src_head(client.get("/").text)
    assert 'data-state="off" open' in head and 'class="d off" id="src-dot"' in head
    # Hours later it is still not set up: no outage, no gone-quiet alarm.
    now = datetime(2026, 10, 17, 9, 0)
    svc._track_source(now, available=False)
    assert svc.outage_state(now + timedelta(hours=5)) is None
    assert svc.quiet_state(now + timedelta(hours=30)) is None


def test_a_typed_database_path_that_is_missing_is_a_fault(client, tmp_path):
    svc = _source(client, detection_backend="custom", birdnet_db_path=str(tmp_path / "birds.db"))
    assert svc.source_unset() is False
    assert 'class="d bad" id="src-dot"' in _src_head(client.get("/").text)
    now = datetime(2026, 10, 17, 9, 0)
    svc._track_source(now, available=False)
    assert svc.outage_state(now + timedelta(hours=2)) is not None


def test_birdweather_with_no_station_is_not_set_up(client):
    svc = _source(client, detection_backend="birdweather")
    assert svc.source_unset() is True
    svc = _source(client, detection_backend="birdweather", birdweather_station_id="1234")
    assert svc.source_unset() is False


def test_a_push_source_waits_gray_then_ticks_itself_off_when_the_test_arrives(client):
    _source(client, detection_backend="birdnet_go")
    st = client.get("/api/status").json()
    assert st["source_unset"] is True
    assert st["push_setup"] == {"received": 0, "test_at": None}
    html = client.get("/").text
    assert 'class="d off" id="src-dot"' in _src_head(html)
    # The steps carry what they say while waiting, so the page can put it back.
    assert 'data-wait="Waiting for a test">Waiting for a test<' in html
    assert 'data-wait="Waiting for the test…"' in html and "data-guide-text>Waiting for the test…<" in html

    # BirdNET-Go's Test Provider: the status the page polls now says so.
    client.post("/api/ingest/birdnet-go/tok",
                json=_go("Test Bird Species", "Testus birdicus", note=1, first_days=0))
    st = client.get("/api/status").json()
    assert st["source_unset"] is False and st["push_setup"]["test_at"]
    html = client.get("/").text
    assert 'class="d good" id="src-dot"' in _src_head(html)
    assert "data-guide-text>Test received at " in html


def test_birdnet_pi_waits_for_its_first_detection(client):
    _source(client, detection_backend="apprise")
    assert client.get("/api/status").json()["source_unset"] is True
    client.post("/api/ingest/apprise/tok",
                json={"comname": "Blue Jay", "sciname": "Cyanocitta cristata", "confidence": 0.9,
                      "date": "2026-10-17", "time": "08:00:00"})
    st = client.get("/api/status").json()
    assert st["source_unset"] is False and st["push_setup"]["received"] == 1


# -- the preview and History, before the first detection ---------------------
def test_history_leaves_out_the_placeholder_before_the_first_detection(client):
    svc = client.app.state.service
    assert svc.pictures["plates"].meta.get("mode") == "welcome"
    assert client.get("/api/history").json()["items"] == []
    html = client.get("/").text
    assert 'id="hist-empty" >None yet.' in html
    svc.force_test_detection("Northern Cardinal", "Cardinalis cardinalis")
    items = client.get("/api/history").json()["items"]
    assert [i["mode"] for i in items] == ["single"]


# -- the Frames card -----------------------------------------------------------
def test_no_frames_self_hosted_ends_with_the_frame_asking_here(client):
    html = client.get("/").text
    empty = html.split('<div class="fr-empty">')[1].split("</div>")[0]
    assert "No frames yet. There are two ways to start." in empty
    assert "<strong>Self-hosted</strong>" in empty
    assert "Either one then asks to be added here." in empty
    assert "data-pair-open>" not in html


def test_no_frames_on_cloud_ends_with_a_code_and_the_pair_button(client):
    _hosted(client)
    html = client.get("/").text
    empty = html.split('<div class="fr-empty">')[1].split('<details class="sc-more')[0]
    assert "<strong>Featherframe Cloud</strong>" in empty
    assert "Either one then shows a code." in empty
    assert '<button class="btn primary" type="button" data-pair-open>Pair a frame</button>' in empty
    assert "asks to be added here" not in empty


def test_a_frame_asking_is_a_plain_notice_not_a_warning(client):
    assert client.get("/api/frame", headers=KIT).status_code == 403
    html = client.get("/").text
    note = html.split("wants to connect")[0].rsplit('<div class="test-box', 1)[1]
    assert note.startswith(' show fr-note panel-note"') and "#ic-info" in note
    assert "#ic-warn" not in note
    assert '<div class="fr-empty">' not in html      # the notice is the card


# -- AI image generation, with no key ----------------------------------------
def test_without_a_key_the_master_switch_waits_for_one(client):
    html = client.get("/").text
    ai = html.split('id="set-imagegen"')[1].split("</details>")[0]
    row = ai.split('for="ai-on"')[0].rsplit('<div class="frow ', 1)[1]
    assert row.startswith('toggle locked ig-needs-key" hidden>')


def test_an_empty_gallery_offers_restore_without_the_backup_note(client):
    html = client.get("/").text
    gen = html.split('id="set-generated"')[1].split("</details>")[0]
    assert "None yet." in gen and "Restore from a backup" in gen
    assert "live only on this server" not in gen
