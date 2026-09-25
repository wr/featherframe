"""The factory picks the right DetectionSource from config, and falls back safely."""
from __future__ import annotations

from featherframe.config import Config
from featherframe.sources import make_source
from featherframe.birdnet import BirdNetDB
from featherframe.sources.pushed import PushedSource


def test_default_is_birdnet_pi():
    src = make_source(Config())
    assert isinstance(src, BirdNetDB)


def test_birdnet_go_is_its_webhook():
    # W-865: BirdNET-Go pushes; a stored config from the URL poll keeps its
    # backend and becomes the webhook.
    src = make_source(Config.from_dict({"detection_backend": "birdnet_go",
                                        "birdnet_go_url": "http://x:8080"}))
    assert isinstance(src, PushedSource) and src.kind == "birdnet_go"


def test_unknown_backend_falls_back_to_custom():
    cfg = Config(detection_backend="nonsense")
    assert cfg.detection_backend == "custom"  # sanitized
    assert isinstance(make_source(cfg), BirdNetDB)


def test_legacy_birdnet_pi_migrates_to_custom():
    cfg = Config(detection_backend="birdnet_pi")
    assert cfg.detection_backend == "custom"          # the raw DB reader is now "custom"
    assert isinstance(make_source(cfg), BirdNetDB)


def test_birdweather_selected():
    from featherframe.sources.birdweather import BirdWeatherSource
    src = make_source(Config(detection_backend="birdweather", birdweather_station_id="abc123"))
    assert isinstance(src, BirdWeatherSource)
    assert src.station_id == "abc123"


def test_apprise_selected():
    src = make_source(Config(detection_backend="apprise"))
    assert isinstance(src, PushedSource) and src.kind == "apprise"


def test_config_roundtrips_new_fields():
    restored = Config.from_dict(Config(detection_backend="birdnet_go").to_dict())
    assert restored.detection_backend == "birdnet_go"


def test_mode_auto_migrates_to_single():
    # Legacy "auto" becomes plain Single mode. The overnight collage is no
    # longer a flag of its own: quiet hours being on IS the whole of it.
    c = Config(mode="auto")
    assert c.mode == "single"
    assert c.quiet_hours_render_collage is (c.quiet_hours_mode != "off")


def test_ingest_token_defaults_to_generated_secret():
    a, b = Config(), Config()
    assert a.ingest_token and len(a.ingest_token) >= 8
    assert a.ingest_token != b.ingest_token  # a fresh secret per new config
    # An explicitly-empty token is preserved (accept-any LAN posture).
    assert Config.from_dict({"ingest_token": ""}).ingest_token == ""


def test_apprise_token_becomes_the_ingest_token():
    # W-865: BirdNET-Pi's secret is now both push sources'; a BirdNET-Pi's
    # Apprise URL keeps working.
    assert Config.from_dict({"apprise_token": "s3cret"}).ingest_token == "s3cret"
    assert Config.from_dict({"apprise_token": ""}).ingest_token == ""


def test_birdweather_station_url_parsed_to_id():
    full = Config(detection_backend="birdweather",
                  birdweather_station_id="https://app.birdweather.com/stations/12345/")
    assert full.birdweather_station_id == "12345"
    bare = Config(detection_backend="birdweather", birdweather_station_id="12345")
    assert bare.birdweather_station_id == "12345"
