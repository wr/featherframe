"""The factory picks the right DetectionSource from config, and falls back safely."""
from __future__ import annotations

from featherframe.config import Config
from featherframe.sources import make_source
from featherframe.birdnet import BirdNetDB
from featherframe.sources.birdnet_go import BirdNetGoSource


def test_default_is_birdnet_pi():
    src = make_source(Config())
    assert isinstance(src, BirdNetDB)


def test_birdnet_go_selected():
    src = make_source(Config(detection_backend="birdnet_go", birdnet_go_url="http://x:8080"))
    assert isinstance(src, BirdNetGoSource)
    assert src.base_url == "http://x:8080"


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
    from featherframe.sources.apprise_push import AppriseSource
    assert isinstance(make_source(Config(detection_backend="apprise")), AppriseSource)


def test_config_roundtrips_new_fields():
    cfg = Config(detection_backend="birdnet_go", birdnet_go_url="http://host:9000/")
    restored = Config.from_dict(cfg.to_dict())
    assert restored.detection_backend == "birdnet_go"
    assert restored.birdnet_go_url == "http://host:9000"  # trailing slash trimmed
