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
    """One of each kind of screen, all asked for and all added — every frame of
    every transport is approved on the server (W-833)."""
    svc = client.app.state.service
    client.get("/api/frame", headers=GRAY)
    client.get("/api/frame", headers=COLOUR)
    client.get("/api/display", headers=TRMNL_X)
    client.get("/api/display", headers=KOBO)
    client.get(PAGE)
    for fid in (GRAY["X-Device-Id"], COLOUR["X-Device-Id"], TRMNL_X["ID"], KOBO["ID"],
                "PAGE-TEST"):
        assert client.post("/api/frames", data={"id": fid, "action": "add"}).status_code == 200
    client.get("/api/frame", headers=GRAY)                    # its telemetry, now it is on
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
             ["name", "shows"],
             ["rotation", "power_mode", "mat_inset_pct", "width", "height",
              "fmt", "dark_quiet"]),
}


@pytest.mark.parametrize("kind", sorted(WANTED))
def test_each_screen_gets_the_same_row_with_only_its_own_controls(client, kind):
    _populate(client)
    frame_id, has, has_not = WANTED[kind]
    row = _row(_card(client), frame_id)
    # The same component every time, and it IS the page's disclosure: a
    # details.disc whose summary carries the frame's health, then its
    # settings, its Details, then Save and Remove.
    for part in ('<summary>', 'class="fr-main"', 'class="fr-desc"',
                 'class="fr-body"', '<span>Details</span>',
                 'data-fr-action="save"', 'data-fr-action="forget"'):
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
    # A viewer turns in the render, so any quarter turn is drawable — in
    # degrees, like every other screen's.
    trmnl = _select(_row(card, TRMNL_X["ID"]), "rotation")
    for r in (0, 90, 180, 270):
        assert f'value="{r}"' in trmnl and ">%d\u00b0<" % r in trmnl


def test_the_collapsed_row_says_what_it_is_and_what_it_shows(client):
    svc = _populate(client)
    assert _listed(svc, GRAY["X-Device-Id"])["summary"].endswith(" · Individual detections")
    assert _listed(svc, COLOUR["X-Device-Id"])["summary"].endswith(" · Collage")
    # Unnamed, the title is the short of what it is and the summary the rest:
    # the collapsed row never says the same thing twice.
    gray = _listed(svc, GRAY["X-Device-Id"])
    assert gray["title"] == "EE03" and gray["summary"] == '10.3" gray · Individual detections'
    page = _listed(svc, "PAGE-TEST")
    assert page["title"] == "iPad" and page["summary"] == "Individual detections"
    # Named, the summary says what it is in full.
    client.post("/api/frames/PAGE-TEST", json={"name": "Kitchen"})
    assert _listed(svc, "PAGE-TEST")["summary"] == "iPad · Individual detections"
    client.post("/api/frames/PAGE-TEST", json={"name": ""})
    card = _card(client)
    for fid in (GRAY["X-Device-Id"], "PAGE-TEST"):
        shown = _row(card, fid).split('class="fr-desc"')[1].split(">", 1)[1].split("<")[0]
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
    a power model, and no screen has a Look or a Dark in quiet hours any more."""
    svc = _populate(client)
    r = client.post("/api/frames/PAGE-TEST",
                    json={"shows": "collage", "fmt": "gray256", "dark_quiet": False,
                          "rotation": 90, "power_mode": "sleep", "mat_inset_pct": 9,
                          "nonsense": 1})
    assert r.json()["ok"]
    own = svc.frames.get("PAGE-TEST")["set"]
    assert own["shows"] == "collage"
    for refused in ("fmt", "dark_quiet", "rotation", "power_mode", "mat_inset_pct",
                    "nonsense"):
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
    assert "wants to connect" in card
    assert f'data-frame-action="add" data-frame-id="{COLOUR["X-Device-Id"]}"' in card
    assert f'data-frame-action="ignore" data-frame-id="{COLOUR["X-Device-Id"]}"' in card
    # There is no current frame, so there is nothing to replace.
    assert "Replace the current frame" not in card
    assert 'data-frame-action="switch"' not in card


def test_an_ignored_frame_folds_at_the_bottom_with_add_and_forget(client):
    client.get("/api/frame", headers=GRAY)
    client.get("/api/frame", headers=COLOUR)
    client.post("/api/frames", data={"id": GRAY["X-Device-Id"], "action": "add"})
    client.post("/api/frames", data={"id": COLOUR["X-Device-Id"], "action": "ignore"})
    card = _card(client)
    assert "Ignored (1)" in card and "wants to connect" not in card
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


def test_save_sits_on_the_right_of_a_row_and_remove_on_the_left(client):
    """As the household form's own Save does."""
    _populate(client)
    actions = _row(_card(client), GRAY["X-Device-Id"]).split('class="fr-actions"')[1]
    assert actions.index('data-fr-action="forget"') < actions.index('data-fr-action="save"')


def test_the_frames_list_is_flush_in_its_card(client):
    """No padding of the card's own above the first row or below the last: a
    row's hover background reaches the card's edge, clipped to its radius."""
    css = client.get("/").text.split("</style>")[0]
    assert "#frames-card { overflow:hidden; }" in css
    assert "#frames-card section.sec { padding:0 26px; }" in css
    assert "#frames-card section.sec { padding:0 16px; }" in css   # at 375 px


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


def test_the_preview_is_upright_and_fills_its_box_for_every_frame(client):
    """The preview is the picture as that frame draws it — never the device's
    canvas shape or its rotation, which would letterbox the portrait sheet."""
    from io import BytesIO
    from PIL import Image
    _populate(client)

    def size(frame_id):
        r = client.get(f"/api/frames/{frame_id}/preview.png")
        assert r.status_code == 200, frame_id
        return Image.open(BytesIO(r.content)).size

    for frame_id in (GRAY["X-Device-Id"], COLOUR["X-Device-Id"], TRMNL_X["ID"],
                     KOBO["ID"], "PAGE-TEST"):
        w, h = size(frame_id)
        assert h > w, frame_id                       # upright, every one of them
    # A TRMNL X hangs on its side; its preview is that glass stood up.
    assert size(TRMNL_X["ID"]) == (1404, 1872)
    # A tablet's window has no shape worth previewing: the sheet at 3:4.
    assert size("PAGE-TEST") == (1200, 1600)


def test_the_battery_endpoint_is_per_frame(client):
    svc = _populate(client)
    fid = GRAY["X-Device-Id"]
    body = client.get(f"/api/battery?hours=24&frame={fid}").json()
    assert body["hours"] == 24 and body["items"]
    assert body["items"][-1]["voltage"] == pytest.approx(3.95)
    # The colour kit reported no voltage, so it has no line of its own.
    assert client.get(f"/api/battery?frame={COLOUR['X-Device-Id']}").json()["items"] == []
    assert svc.db.battery_history("2000-01-01", fid)


def test_the_row_carries_that_frames_health(client):
    """The Health card is gone: one device-list row per frame, with its dot,
    its badges and its three readings, opening onto the same Details."""
    _populate(client)
    html = client.get("/").text
    assert 'id="health-card"' not in html and "hl-list" not in html
    card = _card(client)
    gray = _row(card, GRAY["X-Device-Id"])
    # collapsed: the dot, the readings
    assert '<span class="d good" data-h="dot">' in gray
    # …on USB, so its battery column is empty — the reading is a battery's
    assert 'data-h="batt-wrap" data-spark-host tabindex="0" ' \
           'aria-label="Battery" hidden>' in gray and "72%" not in gray
    assert 'data-h="wifi-wrap"' in gray and "Good · -61 dBm" in gray
    assert 'data-h="seen-text">just now<' in gray
    # open: Details is what this frame reported about itself, and only that —
    # the battery and the Wi-Fi are already on the row above.
    assert "2026.09.20" in gray and GRAY["X-Board"] in gray and ">Frame ID<" in gray
    for repeated in ('data-h="spark"', 'class="trend"', 'class="vitals"',
                     "<span>Power</span>", "<span>Wi-Fi</span>"):
        assert repeated not in gray, repeated
    # a screen that reports no Wi-Fi leaves that column empty, and says so
    kobo = _row(card, KOBO["ID"])
    assert 'data-h="wifi-wrap" hidden' in kobo
    # and no setting is anywhere near the metadata
    for field in FRAME_FIELDS:
        assert f'name="{field}"' not in card


def test_an_overdue_or_flat_frame_wears_a_badge_not_a_banner(client):
    from datetime import timedelta
    svc = _populate(client)
    svc._clock = (lambda real=svc._clock: lambda: real() + timedelta(days=1))()
    gray = _row(_card(client), GRAY["X-Device-Id"])
    assert '<span class="badge warn" data-h="overdue" >Overdue</span>' in gray
    assert '<span class="d warn" data-h="dot">' in gray
    assert 'id="batt-banners"' not in client.get("/").text


def test_a_tab_that_is_not_open_is_grey_not_green(client):
    from datetime import timedelta
    svc = _populate(client)
    assert _listed(svc, "PAGE-TEST")["card"]["state"] == "good"
    svc._clock = (lambda real=svc._clock: lambda: real() + timedelta(minutes=5))()
    page = _listed(svc, "PAGE-TEST")["card"]
    assert page["overdue"] is False and page["state"] == "off"


# -- the words on the page ----------------------------------------------------
GONE = ("Shows", "Check every", "Wake interval", "Turned", "Look",
        "Dark in quiet hours", "updates from here too",
        "Any screen can be set to show it", "a few hours apart suits it",
        "0 shows every species")


def test_the_copy_that_was_cut_is_nowhere_on_the_page(client):
    _populate(client)
    html = client.get("/").text
    for gone in GONE:
        assert gone not in html, gone


def test_a_frames_settings_are_named_plainly(client):
    _populate(client)
    gray = _row(_card(client), GRAY["X-Device-Id"])
    for label in (">Name<", ">Content<", ">Rotation<", ">Power<", ">Update interval<",
                  ">Mat inset (%)<", ">Mat offset (px)<", ">Reset to defaults<"):
        assert label in gray, label
    assert ">Individual detections<" in gray and ">Collage<" in gray
    assert ">USB<" in gray and ">Battery<" in gray
    assert ">Every 3 seconds<" in gray and ">Every 15 minutes<" in gray
    # A name explains itself; only the mat and a screen with no reported size
    # carry a hint at all.
    assert gray.count('class="hint"') == 2
    kobo = _row(_card(client), KOBO["ID"])
    assert ">Screen size<" in kobo
    assert "This device doesn\u2019t report its screen size." in kobo or \
           "This device doesn't report its screen size. Enter it in pixels for " \
           "a sharper image." in kobo


def test_one_update_interval_saves_the_field_its_power_model_uses(client):
    """The options swap with Power; only the one in use is offered."""
    svc = _populate(client)
    fid = GRAY["X-Device-Id"]
    row = _row(_card(client), fid)
    assert row.count(">Update interval<") == 1
    assert '<span data-fr-swap="awake" >' in row and '<span data-fr-swap="sleep" hidden>' in row
    assert client.post(f"/api/frames/{fid}",
                       json={"power_mode": "sleep", "wake_interval_minutes": 30}).json()["ok"]
    own = svc.frames.get(fid)["set"]
    assert own["wake_interval_minutes"] == 30 and "device_poll_seconds" not in own
    row = _row(_card(client), fid)
    assert '<span data-fr-swap="sleep" >' in row and '<span data-fr-swap="awake" hidden>' in row


# -- the household's Collage section ------------------------------------------
def _sections(client) -> list:
    import re
    return re.findall(r'<h2 class="sec-head">([^<]*)', client.get("/").text)


def test_the_household_sections_read_in_order(client):
    _populate(client)
    names = [s.strip() for s in _sections(client)]
    assert "Frames" not in names                      # the card is just the list
    assert names[:4] == ["Detection source", "Image generation",
                         "Individual detections", "Collage"]
    assert "Quiet hours" not in names                 # it is a row in Collage now


def test_the_collage_section_carries_quiet_hours_and_no_preamble(client):
    _populate(client)
    sec = client.get("/").text.split('<h2 class="sec-head">Collage</h2>')[1].split("</section>")[0]
    assert 'class="intro"' not in sec
    assert ">Update interval<" in sec and ">Species limit<" in sec
    assert "How often the collage is redrawn during the day" in sec
    assert "The most species shown in one collage" in sec
    # The interval is a dropdown of the intervals an owner picks.
    every = sec.split('name="collage_interval_hours"')[1].split("</select>")[0]
    assert [">Every hour<", ">Every 4 hours<", ">Every 6 hours<",
            ">Every 12 hours<", ">Every 24 hours<"] == [o for o in
            (">Every hour<", ">Every 4 hours<", ">Every 6 hours<",
             ">Every 12 hours<", ">Every 24 hours<") if o in every]
    # Quiet hours is the overnight collage, so it lives here — as the mode and
    # its custom window, and nothing else to switch on.
    assert ">Quiet hours<" in sec and "Every frame shows the collage overnight." in sec
    assert 'name="quiet_hours_mode"' in sec and 'name="quiet_hours_start"' in sec
    assert 'name="quiet_hours_render_collage"' not in client.get("/").text
    # The AI collage is here, always on offer.
    assert 'name="collage_generated"' in sec
    assert ">Generate menagerie-style collages" in sec
    ig = client.get("/").text.split('<h2 class="sec-head">Image generation')[1].split("</section>")[0]
    assert 'name="collage_generated"' not in ig and 'name="imagegen_enabled"' not in ig


def test_a_stored_interval_the_menu_does_not_offer_is_still_shown(client):
    """Nothing is silently changed under an owner who set an odd number."""
    svc = _populate(client)
    svc.config.collage_interval_hours = 3
    every = (client.get("/").text.split('name="collage_interval_hours"')[1]
             .split("</select>")[0])
    assert '<option value="3" selected>Every 3 hours</option>' in every


def test_an_empty_species_limit_is_no_limit(client):
    svc = _populate(client)
    r = client.post("/settings", data={"collage_species_max": "",
                                       "collage_interval_hours": "8"},
                    follow_redirects=False)
    assert r.status_code == 303 and svc.config.collage_species_max == 0
    field = client.get("/").text.split('id="f-review-max"')[1].split(">")[0]
    assert 'placeholder="No limit"' in field and 'value=""' in field
    client.post("/settings", data={"collage_species_max": "12",
                                   "collage_interval_hours": "8"}, follow_redirects=False)
    assert svc.config.collage_species_max == 12
    assert 'value="12"' in client.get("/").text.split('id="f-review-max"')[1].split(">")[0]


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
