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
import os
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

# One generation at a time, process-wide. Module-level on purpose: config
# changes rebuild the provider instance, and a per-instance lock would let an
# old and a new provider generate (and write the same cache file) concurrently.
_GEN_LOCK = threading.Lock()

# Bump when the style prompt changes materially. Cached plates keep serving
# regardless — the version is recorded in the sidecar so a manual regenerate
# picks up the current prompt.
PROMPT_VERSION = 4

# Portrait, matching the plates' aspect closely enough for the content crop.
GEN_SIZE = "1024x1536"

# v4: v3 plus the second blind sitting: the hand's limits are the signature
# (nothing rendered past what brush and burin can hold), the sheet must
# exhibit its subject (no camouflage or concealment poses), and botany varies
# with the species instead of repeating stock plants.
# v3 was Wells's first blind-test debrief (28/40 against 20 fresh fakes):
# the medium must be discernible (wash or line, never airbrush), saturation is
# pigment not light, no frontal eye contact, bill gape and energy must match
# the species' real voice and character, and showcase-chaotic botany is an
# allowed minority mode. v2 was distilled from a study of all 120 plates
# (sub-style frequencies, multi-figure composition rules, environment rules,
# measured color data) plus five blind-judging rounds against real plates
# (W-582). Every clause removes a tell judges actually used. Change with care
# and re-run a blind comparison before shipping a new version.
_STYLE_PROMPT = (
    "A hand-colored copperplate engraving with aquatint in the exact style of John James "
    "Audubon's 'The Birds of America' (Havell edition, 1827-1838), depicting {subject} "
    "life-size, drawn with a naturalist's accuracy. The background is bright, near-white "
    "wove paper left completely untouched — no sepia tint, no cream wash, no aging, no "
    "vignette, no border.\n\n"
    "The printed process shows everywhere: transparent watercolor washes sit over visible "
    "engraved feather line-work and irregular aquatint grain; washes pool and bleed "
    "slightly at their edges; hand-coloring is gently uneven and no two markings repeat "
    "exactly. Detail resolves only as far as the hand tools allow: every berry, leaf, "
    "and eye is a brush's honest approximation of something observed in the field — "
    "simplified, slightly irregular — never an object rendered past what watercolor "
    "and burin can hold. Edges carry the faint softness of an impression on damp paper — never "
    "digital smoothness, never airbrush gradients. Every passage must declare its "
    "medium: brush-laid wash with granulation and pooling, or engraved line — an "
    "ambiguous airbrushed surface belongs to no 19th-century process. Saturation is "
    "always pigment, never light: nothing glows, nothing has a specular sheen. Eyes "
    "are matte period eyes: a small dark bead with at most one tiny dry fleck of "
    "light, and the subject never makes frontal eye contact with the viewer.\n\n"
    "Color as the colorists actually worked: bodies are low-chroma umber, olive, and gray "
    "built from engraved line — but where the species wears color, the washes are "
    "CONFIDENTLY SATURATED: one or two vivid accents (scarlet, cobalt, chrome yellow, "
    "blood-red bill) that blaze against the drab bulk. A genuinely drab species stays "
    "near-monochrome, its chroma delegated to plant, berries, bare parts, or eye — "
    "drabness is correct, timidity is not. Foliage greens are muted sage-olive, never "
    "grass-green. Whites are reserved bare paper with gray modeling, never opaque paint. "
    "Black plumage is glazed with blue-violet iridescence, never flat gray.\n\n"
    "Compose as Audubon composed. Use two or three figures of the species when sexes or "
    "ages differ or a second view adds information — one clean closed-wing profile plus "
    "one bird with wings and tail fully spread, or dorsal set against ventral — poses "
    "never repeating, at least one figure contorted or animated in a characteristic "
    "behavior (singing, foraging head-down, lunging, banking in flight). Behavior "
    "must be true to this particular species as a naturalist knows it: the bill opens "
    "only as far as its real voice demands, posture and energy match its living "
    "temperament, and any contortion follows Audubon's theatrical grammar rather than "
    "generic distortion. And the sheet exists to EXHIBIT the animal: however dramatic "
    "the moment, the subject stays conspicuous with its diagnostic features displayed "
    "— a pose that conceals or camouflages the subject defeats the plate's purpose. "
    "Arrange the figures on one long diagonal or S-curve armature — a branch, stem, or "
    "bank entering from the sheet edge and cut off flush — at staggered heights, facing "
    "opposite directions; a very large bird is instead bent in the period manner (neck "
    "recurved) to fit the sheet life-size. Half to two-thirds of the sheet stays bare "
    "paper, asymmetrically. A single figure is right when one plumage tells everything — "
    "then catch it mid-action. Beside each bird on a multi-figure sheet sits only a tiny "
    "engraved italic numeral (1., 2.) in the period manner.\n\n"
    "The setting is specific and nameable, never generic filler: a foliage songbird gets "
    "ONE identifiable host plant tied to its real diet or season, drawn to "
    "botanical-plate standard with individually veined leaves that carry the incidental "
    "imperfections of field-gathered specimens — most leaves whole, damage the "
    "exception that proves the specimen real, and chosen fresh from that species' own "
    "world rather than from a painter's stock of favorites; "
    "a trunk forager gets dead lichen-crusted wood, no leaves; a ground bird gets a "
    "painted ground band of moss, rocks, and particular grasses in the lower third only, "
    "its edge cut hard so it floats on the paper, bare-paper sky above; a waterbird or "
    "wader gets a specific muted shore or marsh with a low horizon, the distance receding "
    "by desaturation into gray; an aerial species flies on open paper. Commit to one "
    "botanical register for the sheet: usually sparse, though a showy fruiting plant "
    "may earn the folio's exuberant showcase treatment.\n\n"
    "Anatomy must survive a naturalist's magnifying glass: feet and claws exactly those "
    "of the living species at honest scale — songbirds with short stout toes and short "
    "modestly curved claws, never sickle talons, every claw attached to its own toe, "
    "nothing tangled or extra. Wings read as true feather tracts — graded covert rows, "
    "then secondaries, then primaries crossing at their own angle, each flight feather "
    "with its shaft — never a uniform stack of nested crescents.\n\n"
    "No text beyond the tiny figure numerals: no title, no names, no lettering, no "
    "signature, no border, no frame line."
)

# Style references for the edits endpoint, matched to the subject's group so
# the model sees the right sub-style: Audubon staged waterfowl in water scenes,
# trunk foragers on dead snags, swallows in flight over a low bank — a swan
# prompted with sparrow references drifts to the wrong conventions. Keys are
# regexes over the common name; values are scientific names looked up in the
# curated index (only plates on disk are used).
_REF_GROUPS: list[tuple[str, list[str]]] = [
    (r"swan|goose|duck|teal|loon|grebe|merganser|gadwall|mallard",
     ["Cygnus columbianus", "Branta canadensis", "Anas platyrhynchos"]),
    (r"gull|tern|skimmer|jaeger",
     ["Larus argentatus", "Larus marinus", "Larus delawarensis"]),
    (r"heron|egret|bittern|crane|sandpiper|plover|yellowlegs|godwit|curlew|"
     r"dunlin|woodcock|snipe|killdeer|whimbrel|rail",
     ["Ardea herodias", "Actitis macularius", "Scolopax minor"]),
    (r"hawk|eagle|falcon|kestrel|kite|osprey|harrier",
     ["Buteo lineatus", "Falco sparverius", "Buteo jamaicensis"]),
    (r"owl", ["Strix varia", "Megascops asio", "Bubo virginianus"]),
    (r"woodpecker|sapsucker|flicker|nuthatch|creeper",
     ["Dryocopus pileatus", "Sphyrapicus varius", "Certhia americana"]),
    (r"swallow|martin|swift|nighthawk",
     ["Hirundo rustica", "Riparia riparia", "Tachycineta bicolor"]),
    (r"thrush|veery|robin|bluebird",
     ["Catharus guttatus", "Hylocichla mustelina", "Turdus migratorius"]),
    (r"crow|raven|jay|magpie",
     ["Corvus brachyrhynchos", "Corvus corax", "Cyanocitta cristata"]),
    (r"starling|blackbird|grackle|cowbird|oriole",
     ["Agelaius phoeniceus", "Quiscalus quiscula", "Euphagus carolinus"]),
    (r"pigeon|dove",
     ["Zenaida macroura", "Passerella iliaca", "Pipilo erythrophthalmus"]),
]
# Default: airy single-species songbird plates (dense showcase plates like the
# Cardinal pulled generations toward heavy Victorian foliage in blind testing).
_PREFERRED_REFS = [
    "Melospiza melodia",        # plate 25 — Song Sparrow, sparse flowering sprigs
    "Spizella passerina",       # plate 104 — Chipping Sparrow, minimal ground
    "Zonotrichia albicollis",   # plate 8 — White-throated Sparrow
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
        """Downscale a plate scan to a reasonable reference size. Kept in COLOR:
        the references anchor the palette to real hand-coloring, and the cached
        result stays color so a color panel can use it — grayscale happens only
        at render time, exactly as for a real scan. draft() before any convert
        keeps the 25MP+ scans from being decoded at full resolution (the Pi
        target has 512MB)."""
        with Image.open(path) as img:
            img.draft("RGB", (_REF_MAX_SIDE, _REF_MAX_SIDE))
            ref = img.convert("RGB")
            ref.thumbnail((_REF_MAX_SIDE, _REF_MAX_SIDE), Image.LANCZOS)
        buf = io.BytesIO()
        ref.save(buf, "JPEG", quality=90)
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


def pick_reference_plates(common_name: str = "", k: int = 3) -> list[Path]:
    """Real plates to hand the model as style references: the subject's own
    group first (so a swan sees water scenes, not sparrow sprigs), then the
    airy songbird defaults, then any plate on disk. Empty list if none exist."""
    try:
        idx = json.loads(paths.plate_index_path().read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(idx, dict):
        return []
    species = idx.get("species")
    if not isinstance(species, list):
        species = []
    images_dir = Path(idx.get("images_dir", paths.plate_images_dir()))
    by_sci = {e.get("scientific"): e for e in species if isinstance(e, dict)}

    chosen: list[Path] = []

    def _try(entry, allow_composite: bool = False) -> None:
        if not entry or len(chosen) >= k:
            return
        if entry.get("composite") and not allow_composite:
            return
        image = entry.get("image")
        if not image:
            return
        p = images_dir / image
        if p.exists() and p not in chosen:
            chosen.append(p)

    name = (common_name or "").lower()
    for pattern, scis in _REF_GROUPS:
        # Word boundaries matter: "Southeastern myotis" must not match "tern".
        if re.search(rf"\b(?:{pattern})\b", name):
            for sci in scis:
                _try(by_sci.get(sci), allow_composite=True)
            break
    for sci in _PREFERRED_REFS:
        _try(by_sci.get(sci))
    for entry in species:
        if isinstance(entry, dict):
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
        return self._generate_to_cache(slug, common_name, scientific_name, force=True)

    def delete(self, slug: str) -> bool:
        slug = slugify(slug)
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
            if not isinstance(meta, dict):
                continue  # foreign file in the cache dir; never crash the page
            if self._png(meta.get("slug", sidecar.stem)).exists():
                out.append(meta)
        out.sort(key=lambda m: str(m.get("created_at") or ""), reverse=True)
        return out

    # -- internals ----------------------------------------------------------
    def _from_cache(self, slug: str) -> Optional[Artwork]:
        try:
            img = plate.extract_generated(self._png(slug))
        except (OSError, ValueError):
            # A cached file that no longer decodes (torn write after a power
            # cut, disk-full) would otherwise wedge the species on the
            # fallback forever: exists() gates generation. Self-heal by
            # deleting it — but only after a second read fails under the
            # generation lock, so a transient I/O hiccup (a tired SD card)
            # can't delete a paid plate and a concurrent regenerate can't
            # have its fresh file deleted mid-replace.
            with _GEN_LOCK:
                try:
                    img = plate.extract_generated(self._png(slug))
                except (OSError, ValueError) as exc:
                    log.warning("corrupt generated cache for %s (%s) — removing "
                                "so it can regenerate", slug, exc)
                    try:
                        self._png(slug).unlink(missing_ok=True)
                        self._sidecar(slug).unlink(missing_ok=True)
                    except OSError:
                        pass
                    return None
        return Artwork(image=img, audubon_plate=None, composite=False, generated=True)

    def _in_cooldown(self, slug: str) -> bool:
        failed = self._failed_at.get(slug)
        return failed is not None and (time.time() - failed) < self._cooldown_s

    def _generate_to_cache(self, slug: str, common_name: str,
                           scientific_name: str, force: bool = False) -> bool:
        with _GEN_LOCK:
            # Re-check under the lock: a caller that queued while another
            # thread generated this species must not buy it a second time.
            if not force and self._png(slug).exists():
                return True
            prompt = build_prompt(common_name, scientific_name)
            refs = (self._refs if self._refs is not None
                    else pick_reference_plates(common_name))
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

            sidecar_payload = json.dumps({
                "slug": slug,
                "common": common_name,
                "scientific": scientific_name,
                "model": getattr(self._model, "name", "unknown"),
                "quality": getattr(self._model, "quality", None),
                "prompt_version": PROMPT_VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "elapsed_s": round(time.time() - started, 1),
                "reference_plates": [p.name for p in refs],
            }, indent=2)
            try:
                # Sidecar first: a crash between the two writes then leaves an
                # orphan sidecar (which cached_species skips), never a PNG the
                # frame serves but the gallery can't see or manage.
                self._dir().mkdir(parents=True, exist_ok=True)
                self._write_atomic(self._sidecar(slug), sidecar_payload.encode())
                self._write_atomic(self._png(slug), png_bytes)
            except OSError as exc:
                # The image is already paid for; without the cooldown a full
                # disk would re-bill on every detection until it frees.
                self._failed_at[slug] = time.time()
                log.warning("cache write failed for %s after a paid generation: %s",
                            scientific_name, exc)
                try:
                    self._sidecar(slug).unlink(missing_ok=True)
                except OSError:
                    pass
                return False
            self._failed_at.pop(slug, None)
            log.info("generated plate for %s in %.1fs", scientific_name,
                     time.time() - started)
            return True

    @staticmethod
    def _write_atomic(dest: Path, data: bytes) -> None:
        """Temp file + rename: a power cut mid-write must never leave a
        truncated cache file behind (exists() gates regeneration)."""
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dest)
