"""W-821: what an owner never tuned is a constant, and old blobs still load."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from featherframe import service as service_mod
from featherframe.config import Config
from featherframe.service import FeatherframeService

REMOVED = {
    "single_show_latest": False, "refresh_debounce_minutes": 45, "dwell_minutes": 5,
    "confidence_threshold": 0.31, "birdnet_go_defer_confidence": False,
    "corroborate_new_species": False, "corroborate_confidence": 0.5,
    "corroborate_window_hours": 2, "corroborate_min_gap_minutes": 1,
    "quiet_alarm_hours": 0, "source_alarm_minutes": 0, "poll_interval_seconds": 120,
    "panel_follow": False, "gray_mode": "1", "dither": "none", "color_saturation": 0.2,
    "birdnet_go_url": "http://go.local:8080",   # W-865: BirdNET-Go pushes now
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    service = FeatherframeService()
    service.source.db_path = str(tmp_path / "missing.db")
    from featherframe.app import app
    app.state.service = service
    return TestClient(app, raise_server_exceptions=False)


def test_an_old_blob_loads_and_saves_without_the_removed_keys():
    cfg = Config.from_dict({**REMOVED, "mode": "collage", "mat_inset_pct": 3.5})
    assert (cfg.mode, cfg.mat_inset_pct) == ("collage", 3.5)
    assert not set(REMOVED) & set(cfg.to_dict())
    assert cfg.bit_depth == 4                       # the 1-bit fallback went with gray_mode


@pytest.mark.parametrize("rebuilds,hours", [(3, 8), (1, 24), (24, 1), (5, 5), ("nan", 8)])
def test_rebuilds_per_day_migrates_to_an_interval(rebuilds, hours):
    assert Config.from_dict({"collage_rebuilds_per_day": rebuilds}).collage_interval_hours == hours
    assert "collage_rebuilds_per_day" not in Config.from_dict({"collage_rebuilds_per_day": rebuilds}).to_dict()


def test_a_stored_interval_wins_over_the_legacy_key():
    cfg = Config.from_dict({"collage_rebuilds_per_day": 3, "collage_interval_hours": 2})
    assert cfg.collage_interval_hours == 2
    assert Config(collage_interval_hours=99).collage_interval_hours == 24
    assert Config(collage_interval_hours=0).collage_interval_hours == 1


def test_a_post_carrying_removed_fields_changes_nothing(client):
    svc = client.app.state.service
    before = svc.config.to_dict()
    r = client.post("/settings", data={k: str(v) for k, v in REMOVED.items()},
                    follow_redirects=False)
    assert r.status_code == 303 and "adjusted" not in r.headers["location"]
    after = svc.config.to_dict()
    assert not set(REMOVED) & set(after)
    # Unticked checkboxes read as off, as on any save; nothing else moved.
    toggles = {"imagegen_enabled", "collage_generated"}
    assert {k: v for k, v in after.items() if k not in toggles} == \
           {k: v for k, v in before.items() if k not in toggles}


def test_the_page_carries_none_of_the_removed_controls(client):
    html = client.get("/").text
    for name in REMOVED:
        assert f'name="{name}"' not in html, name
    # The household form keeps its own fields; `mode` went with W-833 (what a
    # screen shows is that screen's, and it says "shows", not "mode").
    assert 'name="collage_interval_hours"' in html
    assert 'name="mode"' not in html


def test_the_overnight_collage_is_quiet_hours_itself(client):
    """It stopped being a toggle: the window IS the overnight collage, and the
    page offers only the mode (with its custom window) inside Collage."""
    svc = client.app.state.service
    html = client.get("/").text
    assert 'name="quiet_hours_render_collage"' not in html
    # Its section is Collage's, and the AI copy points at the docs.
    collage = html.split('id="set-collage"')[1].split('<details class="disc set')[0]
    assert collage.count('name="quiet_hours_mode"') == 3      # Off / sun / custom
    assert "wiki/AI-illustrations" in html and ">Learn</a>" in html
    # Region is a household setting (W-702): North America, Gould's Europe or
    # his Australia (W-870).
    assert '<select class="sel" id="f-region" name="region">' in html
    assert '<option value="europe">Europe · Gould\'s Birds of Europe</option>' in html
    assert '<option value="australia">Australia · Gould\'s Birds of Australia</option>' in html
    assert '<option value="asia">Asia · Gould\'s Birds of Asia</option>' in html
    # Great Britain is no region (W-872): its few extra species are Europe's.
    assert "Great Britain" not in html
    # Saving without the removed field derives it from the mode either way.
    for mode, want in (("off", False), ("custom", True), ("sun", True)):
        r = client.post("/settings", data={"quiet_hours_mode": mode}, follow_redirects=False)
        assert r.status_code == 303
        assert svc.config.quiet_hours_mode == mode
        assert svc.config.quiet_hours_render_collage is want
        assert "quiet_hours_render_collage" not in svc.config.to_dict()


# Copy that has been culled. It must be gone from the page AND from the render
# that used to print it — a string nobody reads is not "removed".
REMOVED_STRINGS = ("A Day in the Garden", "Update interval (hours)",
                   "nightly collage as a single")


def test_the_copy_that_was_culled_is_gone(client):
    from pathlib import Path
    from featherframe.render import collage as collage_mod
    html = client.get("/").text
    src = Path(collage_mod.__file__).read_text()
    for gone in REMOVED_STRINGS:
        assert gone not in html, gone
        assert gone not in src, gone


def test_the_source_is_polled_on_a_constant(client):
    svc = client.app.state.service
    assert svc._effective_poll_seconds() == service_mod.POLL_SECONDS == 5
    svc.config.detection_backend = "birdweather"
    assert svc._effective_poll_seconds() == 60


def test_region_is_saved_and_redraws_the_plate_on_the_next_tick(client, monkeypatch):
    svc = client.app.state.service
    kicked = []
    monkeypatch.setattr(svc, "_start_task", lambda key, fn, *a: kicked.append(key) or True)
    assert svc.config.region == "north-america" and svc.plates.region == "north-america"
    r = client.post("/settings", data={"quiet_hours_mode": "off", "region": "europe"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert svc.config.region == "europe" and svc.plates.region == "europe"
    assert svc._region_redraw is True         # drawn by the tick, not the request
    assert kicked == ["redraw"]               # …which starts at once, off the request
    client.post("/settings", data={"quiet_hours_mode": "off", "region": "asia"},
                follow_redirects=False)
    assert svc.config.region == "asia" and svc.plates.region == "asia"      # W-871
    client.post("/settings", data={"quiet_hours_mode": "off", "region": "atlantis"},
                follow_redirects=False)
    assert svc.config.region == "north-america"     # an unknown region is the default


@pytest.mark.parametrize("form", [{"collage_generated": "1"}, {"collage_branch": "bare"},
                                  {"collage_species_max": "4"}])
def test_a_collage_setting_redraws_the_collage_at_once(client, monkeypatch, form):
    svc = client.app.state.service
    kicked = []
    monkeypatch.setattr(svc, "_start_task", lambda key, fn, *a: kicked.append((key, fn)) or True)
    client.post("/settings", data={"quiet_hours_mode": "off", "collage_generated": "0",
                                   "collage_branch": "season", "collage_species_max": "9"})
    svc._collage_redraw, kicked[:] = False, []
    client.post("/settings", data={"quiet_hours_mode": "off", **form}, follow_redirects=False)
    assert svc._collage_redraw is True
    assert kicked == [("collage", svc.tick)]   # not the collage's next interval

    # The tick draws the collage again, of the day it is already of.
    redrawn = []
    monkeypatch.setattr(svc, "_rerender_picture", redrawn.append)
    svc.pictures["collage"].etag = "abc"
    svc._tick_pictures()
    assert redrawn == ["collage"] and svc._collage_redraw is False


def test_a_collage_setting_left_alone_redraws_nothing(client, monkeypatch):
    svc = client.app.state.service
    kicked = []
    monkeypatch.setattr(svc, "_start_task", lambda key, fn, *a: kicked.append(key) or True)
    client.post("/settings", data={"quiet_hours_mode": "off",
                                   "collage_interval_hours": "3"})
    assert kicked == [] and svc._collage_redraw is False
