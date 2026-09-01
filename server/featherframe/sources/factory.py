"""Pick a DetectionSource from config. One place that knows the backends."""
from __future__ import annotations

import logging

from .base import DetectionSource

log = logging.getLogger("featherframe.sources")


def make_source(config, db=None) -> DetectionSource:
    """Build the detection source named by ``config.detection_backend``.
    Unknown backends fall back to the local SQLite reader ("custom"). ``db`` is
    the kv store, needed only by the push (Apprise) source to persist its queue."""
    backend = getattr(config, "detection_backend", "custom")
    if backend == "birdnet_go":
        from .birdnet_go import BirdNetGoSource
        return BirdNetGoSource(
            config.birdnet_go_url,
            defer_confidence=getattr(config, "birdnet_go_defer_confidence", True),
        )
    if backend == "birdweather":
        from .birdweather import BirdWeatherSource
        return BirdWeatherSource(getattr(config, "birdweather_station_id", ""))
    if backend == "apprise":
        from .apprise_push import AppriseSource
        return AppriseSource(db=db)
    if backend not in ("custom", "birdnet_pi"):
        log.warning("unknown detection_backend %r, using custom (local SQLite)", backend)
    from ..birdnet import BirdNetDB
    return BirdNetDB(config.birdnet_db_path)
