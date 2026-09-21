"""The Frames card (W-833 step 3): one row component for every screen.

A frame is a frame — the kit on the wall, a second kit, a TRMNL, a tablet —
so the page renders the SAME row for all of them and offers each only what its
own capabilities allow. Nothing shared is offered per frame, and no frame
setting is rendered anywhere but that row.
"""
from __future__ import annotations

from html import escape
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from featherframe.render import pipeline
from tests._frames import EE02_PANEL, EE03_PANEL

GRAY = {"X-Device-Id": "AA:AA:AA:00:00:03", "X-Panel": EE03_PANEL,
        "X-Board": "XIAO ESP32-S3 Plus + EE03", "X-Battery-Voltage": "3.95",
        "X-Battery-Percent": "72", "X-Wifi-Rssi": "-61", "X-FF-Version": "2026.09.20"}
COLOUR = {"X-Device-Id": "BB:BB:BB:00:00:02", "X-Panel": EE02_PANEL,
          "X-Board": "XIAO ESP32-S3 Plus + EE02"}
TRMNL_X = {"ID": "CC:CC:CC:00:00:01", "Model": "x", "Width": "1872", "Height": "1404",
           "Battery-Voltage": "4.02"}
KOBO = {"ID": "DD:DD:DD:00:00:01"}          # a script client: an ID and nothing else
PAGE = "/api/view/state?viewer=PAGE-TEST&w=1536&h=2048&device=iPad"

ROW_END = "\n    </li>"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.app import app
    from featherframe.service import FeatherframeService
    pipeline.DITHER_OVERRIDE = "none"
    svc = FeatherframeService()
    svc.source.db_path = str(tmp_path / "missing.db")
    svc._render_welcome(svc._clock(), False)
    app.state.service = svc
    return TestClient(app)


def _populate(client):
    """One of each kind of screen, all on."""
    svc = client.app.state.service
    client.get("/api/frame", headers=GRAY)                    # the first kit is let in
    client.get("/api/frame", headers=COLOUR)                  # …and the second asks
    client.post("/api/frames", data={"id": COLOUR["X-Device-Id"], "action": "add"})
    client.get("/api/display", headers=TRMNL_X)
    client.get("/api/display", headers=KOBO)
    client.get(PAGE)
    svc.tick()
    return svc


def _card(client) -> str:
    return client.get("/").text.split('id="frames-card"')[1].split("</section>")[0]


def _row(card: str, frame_id: str) -> str:
    return card.split(f'data-frame="{frame_id}"')[1].split(ROW_END)[0]


def _listed(svc, frame_id: str) -> dict:
    return [f for f in svc.frames_list() if f["id"] == frame_id][0]


# -- one row component, controls by capability --------------------------------
# What each kind of screen has, and what it must NOT be offered.
WANTED = {
    "gray kit": (GRAY["X-Device-Id"],
                 ["name", "shows", "rotation", "power_mode", "device_poll_seconds",
                  "wake_interval_minutes", "mat_inset_pct", "mat_offset_x_px",
                  "mat_offset_y_px"],
                 ["fmt", "dark_quiet", "width", "height"]),
    "colour kit": (COLOUR["X-Device-Id"],
                   ["name", "shows", "rotation", "power_mode", "mat_inset_pct"],
                   ["fmt", "dark_quiet", "width", "height"]),
    "sized TRMNL": (TRMNL_X["ID"],
                    ["name", "shows", "rotation"],
                    ["power_mode", "mat_inset_pct", "fmt", "dark_quiet",
                     "width", "height"]),
    "unsized TRMNL": (KOBO["ID"],
                      ["name", "shows", "rotation", "width", "height"],
                      ["power_mode", "mat_inset_pct", "fmt", "dark_quiet"]),
    "page": ("PAGE-TEST",
             ["name", "shows", "fmt", "dark_quiet"],
             ["rotation", "power_mode", "mat_inset_pct", "width", "height"]),
}


@pytest.mark.parametrize("kind", sorted(WANTED))
def test_each_screen_gets_the_same_row_with_only_its_own_controls(client, kind):
    _populate(client)
    frame_id, has, has_not = WANTED[kind]
    row = _row(_card(client), frame_id)
    # The same component every time: a head that opens onto a body, then Save
    # and Remove.
    for part in ('class="fr-head"', 'aria-expanded="false"', 'class="fr-sum"',
                 'class="fr-body"', 'data-fr-action="save"', 'data-fr-action="forget"'):
        assert part in row, part
    for field in has:
        assert f'data-f="{field}"' in row, field
    for field in has_not:
        assert f'data-f="{field}"' not in row, field


def _select(row: str, field: str) -> str:
    return row.split(f'data-f="{field}"')[1].split("</select>")[0]


def test_a_kits_rotation_is_only_the_ones_its_panel_takes(client):
    _populate(client)
    card = _card(client)
    gray = _select(_row(card, GRAY["X-Device-Id"]), "rotation")
    colour = _select(_row(card, COLOUR["X-Device-Id"]), "rotation")
    assert 'value="90"' in gray and 'value="270"' in gray and 'value="0"' not in gray
    assert 'value="0"' in colour and 'value="180"' in colour and 'value="90"' not in colour
    # A viewer turns in the render, so any quarter turn is drawable — and it is
    # described in words, not degrees.
    trmnl = _row(card, TRMNL_X["ID"])
    assert "Upright" in trmnl and "Upside down" in trmnl


def test_the_collapsed_row_says_what_it_is_and_what_it_shows(client):
    svc = _populate(client)
    assert _listed(svc, GRAY["X-Device-Id"])["summary"].endswith(" · Plates")
    assert _listed(svc, COLOUR["X-Device-Id"])["summary"].endswith(" · Collage")
    # Unnamed, the title is the short of what it is and the summary the rest:
    # the collapsed row never says the same thing twice.
    gray = _listed(svc, GRAY["X-Device-Id"])
    assert gray["title"] == "EE03" and gray["summary"] == '10.3" gray · Plates'
    page = _listed(svc, "PAGE-TEST")
    assert page["title"] == "iPad" and page["summary"] == "Plates · color"
    # Named, the summary says what it is in full.
    client.post("/api/frames/PAGE-TEST", json={"name": "Kitchen"})
    assert _listed(svc, "PAGE-TEST")["summary"] == "iPad · Plates · color"
    client.post("/api/frames/PAGE-TEST", json={"name": ""})
    card = _card(client)
    for fid in (GRAY["X-Device-Id"], "PAGE-TEST"):
        shown = _row(card, fid).split('class="fr-sum"')[1].split(">", 1)[1].split("<")[0]
        assert shown == escape(_listed(svc, fid)["summary"], quote=False).replace(chr(34), "&#34;")


# -- no frame setting anywhere but the Frames card ----------------------------
FRAME_FIELDS = ("mode", "power_mode", "panel_rotation", "mat_inset_pct",
                "mat_offset_x_px", "mat_offset_y_px", "wake_interval_minutes",
                "device_poll_seconds", "panel")


def test_the_household_form_has_no_frame_fields(client):
    _populate(client)
    html = client.get("/").text
    form = html.split('action="/settings"')[1].split("</form>")[0]
    for field in FRAME_FIELDS:
        assert f'name="{field}"' not in form, field
    # What is left is the household's, and it is still there.
    for field in ("quiet_hours_mode", "collage_interval_hours", "species_blocklist",
                  "detection_backend", "imagegen_enabled"):
        assert f'name="{field}"' in form, field


def test_settings_no_longer_accepts_frame_fields(client):
    svc = _populate(client)
    fid = GRAY["X-Device-Id"]
    before = svc.frame_config(svc.frames.get(fid))
    r = client.post("/settings", data={"quiet_hours_mode": "off", "panel_rotation": "270",
                                       "mat_inset_pct": "9.5", "power_mode": "sleep",
                                       "wake_interval_minutes": "60", "mode": "collage",
                                       "panel": "ee02"},
                    follow_redirects=False)
    assert r.status_code == 303
    after = svc.frame_config(svc.frames.get(fid))
    assert (after.panel_rotation, after.mat_inset_pct, after.power_mode,
            after.wake_interval_minutes, after.panel) == \
           (before.panel_rotation, before.mat_inset_pct, before.power_mode,
            before.wake_interval_minutes, before.panel)
    assert svc.frames.get(fid)["set"] == {}
    assert svc.config.quiet_hours_mode == "off"      # the household's did save


# -- saving one frame ---------------------------------------------------------
@pytest.mark.parametrize("frame_id", [GRAY["X-Device-Id"], COLOUR["X-Device-Id"],
                                      TRMNL_X["ID"], KOBO["ID"], "PAGE-TEST"])
def test_every_frame_can_be_renamed_through_the_one_endpoint(client, frame_id):
    svc = _populate(client)
    assert client.post(f"/api/frames/{frame_id}", json={"name": "Landing"}).json()["ok"]
    assert _listed(svc, frame_id)["title"] == "Landing"
    assert "Landing" in _row(_card(client), frame_id)
    # Blank falls back to what it is.
    assert client.post(f"/api/frames/{frame_id}", json={"name": "  "}).json()["ok"]
    row = _listed(svc, frame_id)
    assert row["name"] == "" and row["title"] == row["what"].split(" · ")[0]


def test_a_save_takes_only_what_the_screen_has(client):
    """Capability validation: a page cannot be given a panel rotation, a mat or
    a power model, and a kit cannot be given a lit screen's Look."""
    svc = _populate(client)
    r = client.post("/api/frames/PAGE-TEST",
                    json={"shows": "collage", "fmt": "gray256", "dark_quiet": False,
                          "rotation": 90, "power_mode": "sleep", "mat_inset_pct": 9,
                          "nonsense": 1})
    assert r.json()["ok"]
    own = svc.frames.get("PAGE-TEST")["set"]
    assert own["shows"] == "collage" and own["fmt"] == "gray256" and own["dark_quiet"] is False
    for refused in ("rotation", "power_mode", "mat_inset_pct", "nonsense"):
        assert refused not in own
    fid = GRAY["X-Device-Id"]
    assert client.post(f"/api/frames/{fid}", json={"fmt": "color", "dark_quiet": True,
                                                   "rotation": 270}).json()["ok"]
    own = svc.frames.get(fid)["set"]
    assert own["panel_rotation"] == 270 and "fmt" not in own and "dark_quiet" not in own


def test_a_sized_trmnl_is_not_asked_how_big_it_is(client):
    svc = _populate(client)
    assert client.post(f"/api/frames/{TRMNL_X['ID']}", json={"width": 800}).json()["ok"]
    assert "width" not in svc.frames.get(TRMNL_X["ID"])["set"]
    # The one that said nothing is asked, and keeps the answer.
    assert client.post(f"/api/frames/{KOBO['ID']}",
                       json={"width": 1264, "height": 1680}).json()["ok"]
    assert _listed(svc, KOBO["ID"])["settings"]["width"] == 1264


def test_a_frame_nobody_has_heard_of_is_a_404(client):
    _populate(client)
    assert client.post("/api/frames/ZZ:ZZ", json={"name": "Nowhere"}).status_code == 404
    assert client.post("/api/frames/ZZ:ZZ", json={"forget": True}).status_code == 404


def test_remove_forgets_any_frame(client):
    svc = _populate(client)
    assert client.post("/api/frames/PAGE-TEST", json={"forget": True}).json()["ok"]
    assert svc.frames.get("PAGE-TEST") is None
    assert 'data-frame="PAGE-TEST"' not in _card(client)


# -- asking, ignored, and the empty state -------------------------------------
def test_a_frame_that_is_asking_is_offered_add_or_ignore(client):
    client.get("/api/frame", headers=GRAY)
    client.get("/api/frame", headers=COLOUR)
    card = _card(client)
    assert "A frame is asking to connect." in card
    assert f'data-frame-action="add" data-frame-id="{COLOUR["X-Device-Id"]}"' in card
    assert f'data-frame-action="ignore" data-frame-id="{COLOUR["X-Device-Id"]}"' in card
    # There is no current frame, so there is nothing to replace.
    assert "Replace the current frame" not in card
    assert 'data-frame-action="switch"' not in card


def test_an_ignored_frame_folds_at_the_bottom_with_add_and_forget(client):
    client.get("/api/frame", headers=GRAY)
    client.get("/api/frame", headers=COLOUR)
    client.post("/api/frames", data={"id": COLOUR["X-Device-Id"], "action": "ignore"})
    card = _card(client)
    assert "Ignored (1)" in card and "A frame is asking to connect." not in card
    ignored = card.split('class="ign-list"')[1]
    assert f'data-frame-action="add" data-frame-id="{COLOUR["X-Device-Id"]}"' in ignored
    assert f'data-frame-action="forget" data-frame-id="{COLOUR["X-Device-Id"]}"' in ignored


def test_with_no_frames_at_all_the_card_is_an_invitation(client):
    card = _card(client)
    assert "Nothing is showing plates yet" in card
    # Two roads: a screen you already own, and the kit.
    assert 'id="fr-view-url"' in card and "/view" in card
    assert "Featherframe-Setup" in card
    # Not an apology, and not the old note.
    assert "hasn't checked in yet" not in card
    assert "class=\"fr-list\"" not in card


# -- per-frame endpoints ------------------------------------------------------
def test_each_frame_has_its_own_preview(client):
    _populate(client)
    for frame_id in (GRAY["X-Device-Id"], COLOUR["X-Device-Id"], "PAGE-TEST"):
        r = client.get(f"/api/frames/{frame_id}/preview.png")
        assert r.status_code == 200, frame_id
        assert r.content[:4] == b"\x89PNG"
    assert client.get("/api/frames/ZZ:ZZ/preview.png").status_code == 404
    # The picker under the plate points at them, one chip per frame.
    svc = client.app.state.service
    html = client.get("/").text
    pick = html.split('id="frame-pick"')[1].split("</div>")[0]
    for frame_id in (GRAY["X-Device-Id"], COLOUR["X-Device-Id"], "PAGE-TEST"):
        assert f'data-src="{_listed(svc, frame_id)["preview_url"]}"' in pick, frame_id
    assert 'data-pick=' in pick


def test_the_battery_endpoint_is_per_frame(client):
    svc = _populate(client)
    fid = GRAY["X-Device-Id"]
    body = client.get(f"/api/battery?hours=24&frame={fid}").json()
    assert body["hours"] == 24 and body["items"]
    assert body["items"][-1]["voltage"] == pytest.approx(3.95)
    # The colour kit reported no voltage, so it has no line of its own.
    assert client.get(f"/api/battery?frame={COLOUR['X-Device-Id']}").json()["items"] == []
    assert svc.db.battery_history("2000-01-01", fid)


def test_the_health_card_has_a_row_per_frame_and_the_source(client):
    _populate(client)
    body = client.get("/").text.split('id="health-card"')[1].split('id="history-card"')[0]
    for frame_id in (GRAY["X-Device-Id"], COLOUR["X-Device-Id"], TRMNL_X["ID"], "PAGE-TEST"):
        assert f'data-health="{frame_id}"' in body, frame_id
    gray = body.split(f'data-health="{GRAY["X-Device-Id"]}"')[1].split(ROW_END)[0]
    assert "72%" in gray and "2026.09.20" in gray and GRAY["X-Board"] in gray
    assert 'data-h="spark"' in gray                  # its own 24 h trend
    # The source's half is still there, and no setting is.
    assert "Now showing" in body and "Species heard" in body
    for field in FRAME_FIELDS:
        assert f'name="{field}"' not in body


# -- the shims are gone -------------------------------------------------------
def test_no_step_three_shims_are_left(client):
    """Every `# W-833 step 3` shim was a single-frame view of a many-frame
    server. None may survive this step."""
    server = Path(__file__).resolve().parents[1]
    hits = [str(p.relative_to(server)) for p in
            list(server.glob("featherframe/**/*.py")) + list(server.glob("templates/*.html"))
            if "W-833 step 3" in p.read_text()]
    assert hits == []
    svc = client.app.state.service
    for gone in ("frames_view", "page_config", "update_page_frame", "_first_kit",
                 "viewer_rows", "panel_notices"):
        assert not hasattr(svc, gone), gone
    for gone in ("device", "frame_card", "panel_notices"):
        assert gone not in svc.status(), gone


def test_a_closed_browser_tab_is_not_a_frame_in_trouble(client):
    """A kit or a TRMNL that stops asking is overdue; a page last open
    yesterday was just closed."""
    from datetime import timedelta
    svc = _populate(client)
    svc._clock = (lambda real=svc._clock: lambda: real() + timedelta(days=1))()
    assert _listed(svc, "PAGE-TEST")["card"]["overdue"] is False
    assert _listed(svc, GRAY["X-Device-Id"])["card"]["overdue"] is True
