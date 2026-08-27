"""AI-generated plates for species Audubon never painted.

The provider chain asks Audubon first; only a species with no real plate reaches
this module. A generated plate is bought once and cached forever in
``data/generated/`` — the only path that replaces it is an explicit regenerate
from the config page. Cached PNGs go through the exact same ``plate.extract``
treatment as a real scan (margin trim, content crop, paper normalize) so the
result sits in the frame with the same tone and framing as the genuine plates.

``ImageModel`` is the swap seam for the actual image API. ``OpenAIImageModel``
talks to OpenAI's Images API with plain ``requests`` (no SDK dependency); other
vendors slot in beside it via ``make_image_model``.

Every failure path returns None to the caller — the frame then falls back to
the typographic plate. A failed species is not retried until ``cooldown_s``
passes, so a dead API key can't be hammered by the 20-second scheduler tick.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from PIL import Image

from .. import paths
from . import plate
from .provider import ArtProvider, Artwork

log = logging.getLogger("featherframe.genart")

# Bump when the style prompt changes materially. Cached plates keep serving
# regardless — the version is recorded in the sidecar so a manual regenerate
# picks up the current prompt.
PROMPT_VERSION = 1

# Portrait, matching the plates' aspect closely enough for the content crop.
GEN_SIZE = "1024x1536"

_STYLE_PROMPT = (
    "A 19th-century hand-colored aquatint engraving in the exact style of John James "
    "Audubon's 'The Birds of America' (Havell edition, 1827-1838). The subject is a "
    "life-size {subject}, rendered with ornithological accuracy in a "
    "characteristic natural pose on a botanically accurate perch or ground element of "
    "its native habitat, with fine engraved linework under delicate watercolor washes, "
    "a muted natural palette, and soft aquatint tonal gradations. Plain aged cream "
    "wove-paper background with generous empty margins around the subject, faint paper "
    "grain, subtle uneven hand-coloring. Absolutely no text, no lettering, no numbers, "
    "no signature, no border, no frame line."
)

# Style references for the edits endpoint: iconic, single-species plates that are
# in the curated set, in preference order. Only files present on disk are used.
_PREFERRED_REFS = [
    "Cardinalis cardinalis",   # plate 159 — bird + branch, classic composition
    "Cyanocitta cristata",     # plate 102 — several birds, rich foliage
    "Turdus migratorius",      # plate 131 — bird + fruiting branch
]
_REF_MAX_SIDE = 1024


def slugify(name: str) -> str:
    s = (name or "").strip().lower().replace("'", "")
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def build_prompt(common_name: str, scientific_name: str) -> str:
    subject = common_name if not scientific_name else f"{common_name} ({scientific_name})"
    return _STYLE_PROMPT.format(subject=subject)


class GenerationError(RuntimeError):
    pass


class ImageModel(ABC):
    """One image-generation backend. ``generate`` returns raw PNG bytes."""

    name: str = "model"

    @abstractmethod
    def generate(self, prompt: str, size: str, refs: list[Path]) -> bytes:
        """Generate one image. Raises GenerationError (or any Exception) on
        failure — the provider catches and soft-fails."""
        raise NotImplementedError


class OpenAIImageModel(ImageModel):
    """OpenAI Images API. Uses /v1/images/edits with real plates as style
    references when available, else /v1/images/generations."""

    API_BASE = "https://api.openai.com/v1"

    def __init__(self, api_key: str, model: str = "gpt-image-2",
                 quality: str = "high", timeout_s: float = 240.0) -> None:
        self.api_key = api_key
        self.model = model
        self.quality = quality
        self.timeout_s = timeout_s
        self.name = model

    # -- HTTP --------------------------------------------------------------
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    @staticmethod
    def _ref_bytes(path: Path) -> bytes:
        """Downscale a plate scan to a reasonable reference size."""
        img = plate.load_gray(path, max_side=_REF_MAX_SIDE).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=90)
        return buf.getvalue()

    def generate(self, prompt: str, size: str, refs: list[Path]) -> bytes:
        if refs:
            files = [("image[]", (f"ref{i}.jpg", self._ref_bytes(p), "image/jpeg"))
                     for i, p in enumerate(refs)]
            data = {"model": self.model, "prompt": prompt, "size": size,
                    "quality": self.quality, "n": "1", "output_format": "png"}
            # input_fidelity applies to gpt-image-1/1.5 only; gpt-image-2
            # always processes references at high fidelity and rejects it.
            if self.model.startswith(("gpt-image-1", "gpt-image-1.5")):
                data["input_fidelity"] = "high"
            resp = requests.post(f"{self.API_BASE}/images/edits",
                                 headers=self._headers(), data=data, files=files,
                                 timeout=self.timeout_s)
        else:
            body = {"model": self.model, "prompt": prompt, "size": size,
                    "quality": self.quality, "n": 1, "output_format": "png",
                    "moderation": "low"}
            resp = requests.post(f"{self.API_BASE}/images/generations",
                                 headers=self._headers(), json=body,
                                 timeout=self.timeout_s)

        if resp.status_code != 200:
            raise GenerationError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            b64 = resp.json()["data"][0]["b64_json"]
            return base64.b64decode(b64)
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise GenerationError(f"unexpected response shape: {exc}") from exc


def make_image_model(config) -> Optional[ImageModel]:
    """Build the configured image model, or None when generation can't run
    (disabled, no key, unknown provider). None means cache-only."""
    if not getattr(config, "imagegen_enabled", False):
        return None
    key = (getattr(config, "imagegen_api_key", "") or "").strip()
    if not key:
        return None
    if config.imagegen_provider == "openai":
        return OpenAIImageModel(key, model=config.imagegen_model,
                                quality=config.imagegen_quality)
    return None


def pick_reference_plates(k: int = 3) -> list[Path]:
    """Real plates to hand the model as style references: preferred icons first,
    then any single-species plate on disk. Empty list if none exist yet."""
    try:
        idx = json.loads(paths.plate_index_path().read_text())
    except (OSError, ValueError):
        return []
    images_dir = Path(idx.get("images_dir", paths.plate_images_dir()))
    by_sci = {e.get("scientific"): e for e in idx.get("species", [])}

    chosen: list[Path] = []

    def _try(entry) -> None:
        if not entry or len(chosen) >= k or entry.get("composite"):
            return
        image = entry.get("image")
        if not image:
            return
        p = images_dir / image
        if p.exists() and p not in chosen:
            chosen.append(p)

    for sci in _PREFERRED_REFS:
        _try(by_sci.get(sci))
    for entry in idx.get("species", []):
        _try(entry)
    return chosen[:k]


class GeneratedArtProvider(ArtProvider):
    """Serve AI-generated plates from the disk cache; generate on first miss
    when a model is configured. Never raises."""

    name = "generated"

    def __init__(self, model: Optional[ImageModel],
                 cache_dir: Optional[Path] = None,
                 refs: Optional[list[Path]] = None,
                 cooldown_s: float = 900.0) -> None:
        self._model = model
        self._cache_dir = Path(cache_dir) if cache_dir else None
        self._refs = refs
        self._cooldown_s = cooldown_s
        self._failed_at: dict[str, float] = {}
        # One generation at a time: the API rate limit is per-minute anyway,
        # and it keeps a tick and a manual regenerate from racing on a species.
        self._gen_lock = threading.Lock()

    # -- paths -------------------------------------------------------------
    def _dir(self) -> Path:
        return self._cache_dir if self._cache_dir else paths.generated_dir()

    def _png(self, slug: str) -> Path:
        return self._dir() / f"{slug}.png"

    def _sidecar(self, slug: str) -> Path:
        return self._dir() / f"{slug}.json"

    @staticmethod
    def _slug_for(common_name: str, scientific_name: str) -> str:
        return slugify(scientific_name or common_name)

    # -- the provider contract --------------------------------------------
    def artwork(self, common_name: str, scientific_name: str) -> Optional[Artwork]:
        slug = self._slug_for(common_name, scientific_name)
        if not slug:
            return None
        try:
            if self._png(slug).exists():
                return self._from_cache(slug)
            if self._model is None:
                return None
            if self._in_cooldown(slug):
                return None
            if not self._generate_to_cache(slug, common_name, scientific_name):
                return None
            return self._from_cache(slug)
        except Exception:
            log.exception("generated artwork failed for %s", scientific_name)
            return None

    # -- cache management (config page) ------------------------------------
    def regenerate(self, common_name: str, scientific_name: str) -> bool:
        """Explicit user request: buy a fresh plate. Keeps the old one on
        failure."""
        slug = self._slug_for(common_name, scientific_name)
        if not slug or self._model is None:
            return False
        self._failed_at.pop(slug, None)
        return self._generate_to_cache(slug, common_name, scientific_name)

    def delete(self, scientific_name: str) -> bool:
        slug = slugify(scientific_name)
        png, sidecar = self._png(slug), self._sidecar(slug)
        if not png.exists():
            return False
        png.unlink(missing_ok=True)
        sidecar.unlink(missing_ok=True)
        return True

    def cached_species(self) -> list[dict]:
        """Sidecar metadata for every cached plate, newest first."""
        out = []
        for sidecar in sorted(self._dir().glob("*.json")):
            try:
                meta = json.loads(sidecar.read_text())
            except (OSError, ValueError):
                continue
            if self._png(meta.get("slug", sidecar.stem)).exists():
                out.append(meta)
        out.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return out

    # -- internals ----------------------------------------------------------
    def _from_cache(self, slug: str) -> Optional[Artwork]:
        img = plate.extract(self._png(slug), composite=False)
        return Artwork(image=img, audubon_plate=None, composite=False, generated=True)

    def _in_cooldown(self, slug: str) -> bool:
        failed = self._failed_at.get(slug)
        return failed is not None and (time.time() - failed) < self._cooldown_s

    def _generate_to_cache(self, slug: str, common_name: str,
                           scientific_name: str) -> bool:
        with self._gen_lock:
            prompt = build_prompt(common_name, scientific_name)
            refs = self._refs if self._refs is not None else pick_reference_plates()
            started = time.time()
            try:
                png_bytes = self._model.generate(prompt, GEN_SIZE, refs)
                # Validate before caching: a corrupt cache would wedge forever.
                Image.open(io.BytesIO(png_bytes)).verify()
            except Exception as exc:
                self._failed_at[slug] = time.time()
                log.warning("generation failed for %s (%s): %s",
                            scientific_name, getattr(self._model, "name", "?"), exc)
                return False

            self._dir().mkdir(parents=True, exist_ok=True)
            self._png(slug).write_bytes(png_bytes)
            self._sidecar(slug).write_text(json.dumps({
                "slug": slug,
                "common": common_name,
                "scientific": scientific_name,
                "model": getattr(self._model, "name", "unknown"),
                "quality": getattr(self._model, "quality", None),
                "prompt_version": PROMPT_VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "elapsed_s": round(time.time() - started, 1),
                "reference_plates": [p.name for p in refs],
            }, indent=2))
            self._failed_at.pop(slug, None)
            log.info("generated plate for %s in %.1fs", scientific_name,
                     time.time() - started)
            return True
