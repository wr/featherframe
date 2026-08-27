"""Pick a DetectionSource from config. One place that knows the backends."""
from __future__ import annotations

import logging

from .base import DetectionSource

log = logging.getLogger("featherframe.sources")


def make_source(config) -> DetectionSource:
    """Build the detection source named by ``config.detection_backend``.
    Unknown backends fall back to BirdNET-Pi."""
    backend = getattr(config, "detection_backend", "birdnet_pi")
    if backend == "birdnet_go":
        from .birdnet_go import BirdNetGoSource
        return BirdNetGoSource(
            config.birdnet_go_url,
            defer_confidence=getattr(config, "birdnet_go_defer_confidence", True),
        )
    if backend != "birdnet_pi":
        log.warning("unknown detection_backend %r, using birdnet_pi", backend)
    from ..birdnet import BirdNetDB
    return BirdNetDB(config.birdnet_db_path)
