"""Detection sources: the swappable seam for where the birds come from."""
from .base import Detection, DetectionSource
from .factory import make_source

__all__ = ["Detection", "DetectionSource", "make_source"]
