"""Pick a DetectionSource from config. One place that knows the backends."""
from __future__ import annotations

import logging

from .base import DetectionSource

log = logging.getLogger("featherframe.sources")


def make_source(config, db=None) -> DetectionSource:
    """Build the detection source named by ``config.detection_backend``.
    Unknown backends fall back to the local SQLite reader ("custom"). ``db`` is
    the kv store, needed only by the push sources to persist their queue."""
    backend = getattr(config, "detection_backend", "custom")
    if backend in ("apprise", "birdnet_go"):
        from .pushed import PushedSource
        return PushedSource(backend, db=db)
    if backend == "birdweather":
        from .birdweather import BirdWeatherSource
        return BirdWeatherSource(getattr(config, "birdweather_station_id", ""))
    if backend not in ("custom", "birdnet_pi"):
        log.warning("unknown detection_backend %r, using custom (local SQLite)", backend)
    from ..birdnet import BirdNetDB
    return BirdNetDB(config.birdnet_db_path)
