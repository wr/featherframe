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
import random
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
from .collage import CollageCell
from .provider import ArtProvider, Artwork

log = logging.getLogger("featherframe.genart")

# One generation at a time, process-wide. Module-level on purpose: config
# changes rebuild the provider instance, and a per-instance lock would let an
# old and a new provider generate (and write the same cache file) concurrently.
_GEN_LOCK = threading.Lock()

# Bump when the style prompt changes materially. Cached plates keep serving
# regardless — the version is recorded in the sidecar so a manual regenerate
# picks up the current prompt.
PROMPT_VERSION = 8

# Portrait, matching the plates' aspect closely enough for the content crop.
GEN_SIZE = "1024x1536"

# v8: regenerates must LOOK regenerated. The image model has no memory — the
# sameness came from us sending a near-identical packet each time. Three
# changes: a forced regenerate excludes the previous draw's plant, tableau,
# foliage, figure count, and armature; the style references are shuffled
# within the eligible pool (the species-group ref keeps slot one); and the
# composition skeleton (figure count + armature form) moved out of the fixed
# clause into sampled axes.
# v7: the brief also names the botany. The image model paints generic
# deciduous-with-catkins when it doesn't know a species' real associates, so
# the text model now returns a POOL of real host/associate plants in season
# (flowering ones included when truthful — Audubon drew a lot of flowers) and
# each generation samples ONE: regenerates vary, species don't converge on
# the same stock foliage, and the plant is a named fact, not a prior.
# v6: the naturalist's brief + tableau/dressing axes. The image model knows
# what things look like, not what names mean (an obscure leaf-winged katydid
# came back as a moth), so a text model that DOES know taxonomy briefs the
# illustrator first — cached per species, injected as fact, asserting what the
# subject IS rather than what it isn't. New sampled axes: tableau (nest with
# young, feeding, a juvenile, a dispute — all Audubon subjects; nest/juvenile
# bird-gated) and surface dressing (lichen was a commanded constant and grew
# on everything; now it and moss are uncommon draws).
# v5: subject identity + sampled art direction. The figure must be the very
# animal the names denote (the detector hands us katydids, cicadas, and bats;
# they were coming back birdified), with anatomy fidelity generalized past
# feathers. The always-on "incidental imperfections" clause is gone — the
# model obeyed it on every sheet (tattered leaves ~100%) — replaced by
# per-sheet SAMPLED direction axes weighted toward restraint (see
# _DIRECTION_AXES), recorded in the sidecar. Blind re-comparison pending.
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
_P_OPEN = (
    "A hand-colored copperplate engraving with aquatint in the exact style of John James "
    "Audubon's 'The Birds of America' (Havell edition, 1827-1838), depicting {subject} "
    "life-size, drawn with a naturalist's accuracy. The figure is exactly the animal "
    "those names denote — its true kind, scale, and anatomy — never translated into a "
    "bird or any other creature; a species outside the folio's birds is presented as "
    "the period's natural-history engravers would have drawn it, in this same plate "
    "manner. The background is bright, near-white "
    "wove paper left completely untouched — no sepia tint, no cream wash, no aging, no "
    "vignette, no border.\n\n"
)
_P_PROCESS = (
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
)
_P_COLOR = (
    "Color as the colorists actually worked: bodies are low-chroma umber, olive, and gray "
    "built from engraved line — but where the species wears color, the washes are "
    "CONFIDENTLY SATURATED: one or two vivid accents (scarlet, cobalt, chrome yellow, "
    "blood-red bill) that blaze against the drab bulk. A genuinely drab species stays "
    "near-monochrome, its chroma delegated to plant, berries, bare parts, or eye — "
    "drabness is correct, timidity is not. Foliage greens are muted sage-olive, never "
    "grass-green. Whites are reserved bare paper with gray modeling, never opaque paint. "
    "Black plumage is glazed with blue-violet iridescence, never flat gray.\n\n"
)
_P_COMPOSE = (
    "Compose as Audubon composed. A second or third figure of the species earns its "
    "place when sexes or ages differ or another view adds information — one at rest in "
    "clean profile against one displaying its full extent as its body allows, or dorsal "
    "against ventral — poses never repeating, at least one figure holding a "
    "characteristic living attitude. Behavior must be true to this particular species as a naturalist knows "
    "it: any voice, gape, or display goes only as far as the real animal's does, "
    "posture and energy match its living temperament, and any contortion follows "
    "Audubon's theatrical grammar rather than generic distortion. And the sheet exists to EXHIBIT the animal: however dramatic "
    "the moment, the subject stays conspicuous with its diagnostic features displayed "
    "— a pose that conceals or camouflages the subject defeats the plate's purpose. "
    "Multiple figures share one continuous armature — a branch, stem, or bank — at "
    "staggered heights, facing opposite directions; a very large species is instead "
    "bent in the period manner to fit the sheet life-size. Half to two-thirds of the sheet stays bare "
    "paper, asymmetrically. Beside each figure on a multi-figure sheet sits only a "
    "tiny engraved italic numeral (1., 2.) in the period manner.\n\n"
)
_P_SETTING = (
    "The setting is specific and nameable, never generic filler: a foliage dweller gets "
    "ONE identifiable host plant tied to its real diet or season, drawn to "
    "botanical-plate standard with individually veined leaves, chosen fresh from that "
    "species' own world rather than from a painter's stock of favorites; "
    "a trunk forager gets dead weathered wood, no leaves; a ground dweller gets a "
    "painted ground band of moss, rocks, and particular grasses in the lower third only, "
    "its edge cut hard so it floats on the paper, bare-paper sky above; a waterbird or "
    "wader gets a specific muted shore or marsh with a low horizon, the distance receding "
    "by desaturation into gray; an aerial species flies on open paper.\n\n"
)
_P_ANATOMY = (
    "Anatomy must survive a naturalist's magnifying glass. A bird's feet and claws are "
    "exactly those of the living species at honest scale — songbirds with short stout "
    "toes and short modestly curved claws, never sickle talons, every claw attached to "
    "its own toe, nothing tangled or extra — and its wings read as true feather tracts "
    "— graded covert rows, then secondaries, then primaries crossing at their own "
    "angle, each flight feather with its shaft — never a uniform stack of nested "
    "crescents. Any other kind of animal receives the same fidelity in its own terms: "
    "limb count, segmentation, venation, membrane, and surface exactly the species' "
    "own, at honest scale, nothing invented and nothing borrowed from birds.\n\n"
)
_P_FOOTER = (
    "No text beyond the tiny figure numerals: no title, no names, no lettering, no "
    "signature, no border, no frame line."
)

_STYLE_PROMPT = _P_OPEN + _P_PROCESS + _P_COLOR + _P_COMPOSE + _P_SETTING + _P_ANATOMY + _P_FOOTER

# The folio's late composite ("totem") plates — several species sharing one
# sheet — are the model for the nightly day-in-review.
_P_COMPOSITE_TEMPLATE = (
    "A hand-colored copperplate engraving with aquatint in the exact style of John James "
    "Audubon's 'The Birds of America' (Havell edition, 1827-1838). This sheet is one of "
    "the folio's late COMPOSITE plates, presenting {n} different species together as one "
    "specimen sheet: {subjects}. The background is bright, near-white wove paper left "
    "completely untouched — no sepia tint, no cream wash, no aging, no vignette, no "
    "border.\n\n"
    "One shared armature — a single bare, branching bough entering from the sheet edge "
    "and cut off flush — carries every figure. Each species holds its own station at a "
    "staggered height, drawn in TRUE RELATIVE SCALE to the others (a large species "
    "dwarfs a small one, as in life), in its own characteristic pose and direction, the "
    "figures never interacting. Each figure is exactly the species its names denote — "
    "its true kind and anatomy, never translated into another creature. The first-listed species takes the most commanding "
    "station; each later one a quieter perch. Beside each figure sits its tiny engraved "
    "italic numeral in the listed order (1., 2., 3., ...) and nothing else. The bough "
    "stays botanically simple — a few sprigs at most — so the figures carry the sheet, "
    "and at least a third of the sheet stays bare paper, asymmetrically.\n\n"
)


def build_composite_prompt(subjects: list[tuple[str, str]],
                           briefs: Optional[dict] = None) -> str:
    """Prompt for the day-in-review sheet: the day's species as one composite
    plate. `subjects` is (common, scientific) in prominence order; `briefs`
    maps a scientific name to a naturalist's one-line description so the
    model draws katydids as katydids."""
    def line(i, common, sci):
        s = f"{i}. {common} ({sci})" if sci else f"{i}. {common}"
        brief = (briefs or {}).get(sci or common, "")
        if brief:
            s += " — " + brief.split(". ")[0].rstrip(".") + "."
        return s
    listed = "; ".join(
        line(i, common, sci)
        for i, (common, sci) in enumerate(subjects, start=1))
    opener = _P_COMPOSITE_TEMPLATE.format(n=len(subjects), subjects=listed)
    return opener + _P_PROCESS + _P_COLOR + _P_ANATOMY + _P_FOOTER


# Real composite plates to hand the model as references, preference order.
_PREFERRED_COMPOSITE_REFS = [
    "Dryobates villosus",       # plate 416 — five woodpecker species, one snag
    "Poecile atricapillus",     # plate 353 — the titmouse composite
    "Haemorhous mexicanus",     # plate 424 — the finch/bunting totem
]


def pick_composite_reference_plates(k: int = 3) -> list[Path]:
    """Composite plates on disk, preferred totems first."""
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

    def _try(entry) -> None:
        if not entry or len(chosen) >= k or not entry.get("composite"):
            return
        image = entry.get("image")
        if not image:
            return
        p = images_dir / image
        if p.exists() and p not in chosen:
            chosen.append(p)

    for sci in _PREFERRED_COMPOSITE_REFS:
        _try(by_sci.get(sci))
    for entry in species:
        if isinstance(entry, dict):
            _try(entry)
    return chosen[:k]

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


# Art direction is SAMPLED, never fixed: a constant clause reads as a command
# and the model obeys it on every sheet (the always-on "incidental
# imperfections" line came back as tattered leaves on ~every plate). Each axis
# contributes one principled, example-free sentence, weighted hard toward
# restraint, so variety emerges across the cache and every regeneration is a
# fresh draw. The picks land in the sidecar for the audit trail.
_DIRECTION_AXES = [
    ("condition", (
        (0.70, "All plant material on the sheet is fresh and whole."),
        (0.25, "The botany is healthy overall; at most one modest, natural sign of "
               "field wear may appear, and the rest stays whole."),
        (0.05, "The botany may carry honest visible weathering, truthful to a "
               "gathered specimen and never decorative."),
    )),
    ("foliage", (
        (0.50, "Keep the setting spare: the fewest botanical elements that still "
               "identify the plant."),
        (0.35, "Give the setting moderate fullness, the bare paper still clearly "
               "dominant."),
        (0.15, "Let the plant take the folio's exuberant showcase treatment for "
               "once, the subject still commanding the sheet."),
    )),
    ("energy", (
        (0.40, "Hold the sheet's temper to composed stillness; even the animated "
               "figure moves gently."),
        (0.40, "Pitch the sheet's temper at quiet activity — characteristic "
               "behavior caught mid-motion, nothing forced."),
        (0.20, "Allow the sheet a full theatrical moment in the folio's dramatic "
               "manner."),
    )),
    ("dressing", (
        (0.70, "Perches and surfaces stay plain: clean bark, stone, or stem, "
               "undressed."),
        (0.15, "Moss may dress the perch or ground, sparingly."),
        (0.15, "Lichen may crust the older wood, sparingly."),
    )),
]

# The tableau axis chooses what the sheet is ABOUT — all subjects Audubon
# himself painted. Nest and juvenile draws only make sense for birds; the
# text model's is_bird verdict gates them.
_TABLEAU_BIRD = (
    (0.55, "The sheet is a straightforward exhibit of the species itself."),
    (0.15, "Build the moment around the species feeding in its true manner, "
           "prey or forage rendered honestly."),
    (0.12, "Build the sheet around the species' own nest — its real "
           "architecture and site — with eggs or dependent young as the season "
           "allows, a parent attending."),
    (0.10, "Set a juvenile beside the adult, its immature plumage honestly "
           "distinct."),
    (0.08, "Stage a true-to-life dispute — rivalry, theft, or defense — in the "
           "folio's dramatic manner."),
)
_FIGURES = (
    (0.40, "Let one figure carry the sheet alone, caught in a telling attitude."),
    (0.40, "Set two figures on the sheet, their poses and directions never "
           "repeating."),
    (0.20, "Set three figures at staggered stations, each on its own errand."),
)
_ARMATURE = (
    (0.35, "Build the sheet on one long diagonal armature entering from the "
           "sheet edge and cut off flush."),
    (0.30, "Build the sheet on a sinuous S-curve armature entering from the "
           "sheet edge and cut off flush."),
    (0.20, "Build the sheet on a single upright stem rising through it, cut "
           "off flush at the edge."),
    (0.15, "Give the sheet open air: the least support the species allows, "
           "the figures commanding bare paper."),
)
_TABLEAU_OTHER = (
    (0.70, "The sheet is a straightforward exhibit of the species itself."),
    (0.20, "Build the moment around the species feeding in its true manner, "
           "prey or forage rendered honestly."),
    (0.10, "Stage a true-to-life defensive or rival encounter in the folio's "
           "dramatic manner."),
)


def _pick(rng: random.Random, choices) -> str:
    total = sum(w for w, _ in choices)
    roll, acc = rng.random() * total, 0.0
    for weight, sentence in choices:
        acc += weight
        if roll <= acc:
            return sentence
    return choices[-1][1]


def _sample_direction(rng: random.Random, is_bird: bool = True,
                      avoid: Optional[dict] = None) -> tuple[str, dict]:
    """One directive per axis. `avoid` maps an axis to the sentence the LAST
    sheet drew — a forced regenerate passes it so the repaint is guaranteed
    to change subject-matter, not just brushstrokes."""
    avoid = avoid or {}

    def choose(axis, choices):
        kept = tuple(c for c in choices if c[1] != avoid.get(axis)) or choices
        return _pick(rng, kept)

    picks = {"tableau": choose("tableau",
                               _TABLEAU_BIRD if is_bird else _TABLEAU_OTHER)}
    fig_choices = _FIGURES[1:] if "juvenile" in picks["tableau"] else _FIGURES
    picks["figures"] = choose("figures", fig_choices)
    picks["armature"] = choose("armature", _ARMATURE)
    for axis, choices in _DIRECTION_AXES:
        if "dispute" in picks["tableau"] or "encounter" in picks["tableau"]:
            if axis == "energy":
                # A staged dispute cannot sit inside composed stillness.
                picks[axis] = _pick(rng, choices[1:])
                continue
        picks[axis] = choose(axis, choices)
    return " ".join(picks.values()), picks


def build_prompt(common_name: str, scientific_name: str, direction: str = "",
                 description: str = "", plant: Optional[dict] = None) -> str:
    subject = common_name if not scientific_name else f"{common_name} ({scientific_name})"
    # Only the constant template goes through .format — the brief and the
    # plant line are model-authored text, and a brace in them must be a
    # brace, not a format field.
    parts = [_P_OPEN.format(subject=subject)]
    if description:
        parts.append("The subject, precisely, as a naturalist briefs an "
                     "illustrator who has never seen one: " + description + "\n\n")
    parts += [_P_PROCESS, _P_COLOR, _P_COMPOSE, _P_SETTING]
    if plant:
        parts.append("For this sheet the one plant is "
                     + str(plant.get("name", "")) + ": "
                     + str(plant.get("look", "")).rstrip(".")
                     + ". Draw it true to that species and that season.\n\n")
    if direction:
        parts.append("Art direction for this sheet, chosen for it alone: "
                     + direction + "\n\n")
    parts += [_P_ANATOMY, _P_FOOTER]
    return "".join(parts)


class GenerationError(RuntimeError):
    pass


# descriptions.json is a whole-file read-modify-write reached from the regen
# worker, the scheduler tick (composite briefs), and request threads — held
# across the text call so a concurrent buyer can't overwrite a paid brief.
_DESC_LOCK = threading.Lock()


# The image model knows what things look like, not what names mean; the brief
# asserts what the subject IS, in drawable terms, from a model that knows.
DESCRIBE_PROMPT = (
    "Brief an illustrator who has never seen {subject} and cannot look it up. "
    "First, in at most 80 words of plain prose: its taxonomic group in "
    "everyday terms, overall body plan and true size, the diagnostic features "
    "that separate it from similar-looking groups, and its characteristic "
    "posture or carriage. Then list 4 to 6 REAL plants genuinely tied to this "
    "species — food, host, nest site, or a plant of its true habitat — each "
    "with a one-line drawable description of how that plant looks in the "
    "season this species is most active (leaf shape, growth habit, and its "
    "flower or fruit state then). Make the list botanically varied, and "
    "include flowering or fruiting associates whenever that is truthful. "
    "State only established fact. Reply as JSON: "
    '{{"is_bird": true or false, "description": "...", '
    '"plants": [{{"name": "...", "look": "..."}}]}}'
)


class TextModel(ABC):
    """One text-completion backend for the naturalist's brief."""

    name: str = "text"

    @abstractmethod
    def complete_json(self, prompt: str) -> dict:
        """Return the model's JSON reply as a dict. May raise."""


class OpenAITextModel(TextModel):
    API_BASE = "https://api.openai.com/v1"

    def __init__(self, api_key: str, model: str = "gpt-5.6-luna",
                 timeout_s: float = 60.0) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self.name = model

    def complete_json(self, prompt: str) -> dict:
        r = requests.post(
            f"{self.API_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model,
                  "messages": [{"role": "user", "content": prompt}],
                  "response_format": {"type": "json_object"}},
            timeout=self.timeout_s)
        r.raise_for_status()
        out = json.loads(r.json()["choices"][0]["message"]["content"])
        if not isinstance(out, dict):
            raise GenerationError("text model returned non-object JSON")
        return out


class GeminiTextModel(TextModel):
    """Gemini writes the naturalist's brief (JSON mode) for Gemini image users."""

    API_BASE = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash",
                 timeout_s: float = 60.0) -> None:
        self.api_key = api_key
        # The shared imagegen_text_model default is an OpenAI id; fall back to a
        # real Gemini text model unless the user set a gemini-* one.
        self.model = model if model.startswith("gemini") else "gemini-2.5-flash"
        self.timeout_s = timeout_s
        self.name = self.model

    def complete_json(self, prompt: str) -> dict:
        r = requests.post(
            f"{self.API_BASE}/models/{self.model}:generateContent",
            headers={"x-goog-api-key": self.api_key},
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"responseMimeType": "application/json"}},
            timeout=self.timeout_s)
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        out = json.loads(text)
        if not isinstance(out, dict):
            raise GenerationError("text model returned non-object JSON")
        return out


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


def _ref_jpeg_b64(path: Path) -> str:
    """A downscaled reference plate as base64 JPEG, for JSON image APIs."""
    return base64.b64encode(OpenAIImageModel._ref_bytes(path)).decode()  # noqa: SLF001


class GeminiImageModel(ImageModel):
    """Google Gemini image generation (the "Nano Banana" line) via
    generateContent, with reference plates passed as inline images."""

    API_BASE = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash-image",
                 timeout_s: float = 240.0) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self.name = model

    def generate(self, prompt: str, size: str, refs: list[Path]) -> bytes:
        parts: list[dict] = [{"text": prompt}]
        for p in refs:
            parts.append({"inline_data": {"mime_type": "image/jpeg",
                                          "data": _ref_jpeg_b64(p)}})
        resp = requests.post(
            f"{self.API_BASE}/models/{self.model}:generateContent",
            headers={"x-goog-api-key": self.api_key},
            json={"contents": [{"parts": parts}],
                  "generationConfig": {"responseModalities": ["IMAGE"]}},
            timeout=self.timeout_s)
        if resp.status_code != 200:
            raise GenerationError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            for part in resp.json()["candidates"][0]["content"]["parts"]:
                blob = part.get("inline_data") or part.get("inlineData") or {}
                if blob.get("data"):
                    return base64.b64decode(blob["data"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise GenerationError(f"unexpected response shape: {exc}") from exc
        raise GenerationError("no image in Gemini response")


class ReplicateImageModel(ImageModel):
    """Replicate — one key, many models (default FLUX.1 Kontext). Creates a
    prediction, polls to completion, downloads the resulting image."""

    API_BASE = "https://api.replicate.com/v1"

    def __init__(self, api_key: str, model: str = "black-forest-labs/flux-kontext-pro",
                 timeout_s: float = 240.0) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self.name = model

    def _headers(self) -> dict:
        return {"Authorization": f"Token {self.api_key}", "Content-Type": "application/json"}

    def generate(self, prompt: str, size: str, refs: list[Path]) -> bytes:
        inp: dict = {"prompt": prompt}
        if refs:
            inp["input_image"] = f"data:image/jpeg;base64,{_ref_jpeg_b64(refs[0])}"
        # "owner/name" -> the model's own endpoint; a bare version hash -> predictions.
        if "/" in self.model and ":" not in self.model:
            url, body = f"{self.API_BASE}/models/{self.model}/predictions", {"input": inp}
        else:
            url, body = f"{self.API_BASE}/predictions", {"version": self.model, "input": inp}
        resp = requests.post(url, headers=self._headers(), json=body, timeout=self.timeout_s)
        if resp.status_code not in (200, 201):
            raise GenerationError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        pred = resp.json()
        get_url = (pred.get("urls") or {}).get("get")
        for _ in range(60):  # ~2 min at 2s
            status = pred.get("status")
            if status == "succeeded":
                break
            if status in ("failed", "canceled"):
                raise GenerationError(f"replicate {status}: {pred.get('error')}")
            if not get_url:
                break
            time.sleep(2)
            pr = requests.get(get_url, headers=self._headers(), timeout=self.timeout_s)
            pr.raise_for_status()
            pred = pr.json()
        out = pred.get("output")
        img_url = out[-1] if isinstance(out, list) and out else (out if isinstance(out, str) else None)
        if not img_url:
            raise GenerationError(f"replicate: no output (status {pred.get('status')})")
        ir = requests.get(img_url, timeout=self.timeout_s)
        ir.raise_for_status()
        return ir.content


class A1111ImageModel(ImageModel):
    """A self-hosted AUTOMATIC1111 / ComfyUI-compatible endpoint (/sdapi/v1).
    No API key — the base URL points at the user's own GPU box."""

    def __init__(self, base_url: str, model: str = "", timeout_s: float = 240.0) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.name = "a1111"

    def generate(self, prompt: str, size: str, refs: list[Path]) -> bytes:
        try:
            w, h = (int(x) for x in str(size).lower().split("x"))
        except (ValueError, TypeError):
            w, h = 1024, 1024
        body: dict = {"prompt": prompt, "width": w, "height": h, "steps": 30}
        if refs:
            body["init_images"] = [_ref_jpeg_b64(refs[0])]
            body["denoising_strength"] = 0.75
            path = "/sdapi/v1/img2img"
        else:
            path = "/sdapi/v1/txt2img"
        resp = requests.post(self.base_url + path, json=body, timeout=self.timeout_s)
        if resp.status_code != 200:
            raise GenerationError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            return base64.b64decode(resp.json()["images"][0])
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise GenerationError(f"unexpected response shape: {exc}") from exc


def make_text_model(config) -> Optional[TextModel]:
    """The naturalist's-brief backend. Follows the image provider and its key;
    only OpenAI and Gemini offer a brief model, so replicate/a1111 return None
    (plates still generate, just brief-less)."""
    if not getattr(config, "imagegen_enabled", False):
        return None
    provider = getattr(config, "imagegen_provider", "openai")
    key = (getattr(config, "imagegen_api_key", "") or "").strip()
    text_model = getattr(config, "imagegen_text_model", "gpt-5.6-luna")
    if provider == "openai" and key:
        return OpenAITextModel(key, model=text_model)
    if provider == "gemini" and key:
        return GeminiTextModel(key, model=text_model)
    return None


def make_image_model(config) -> Optional[ImageModel]:
    """Build the configured image model, or None when generation can't run
    (disabled, missing key/URL, unknown provider). None means cache-only."""
    if not getattr(config, "imagegen_enabled", False):
        return None
    provider = getattr(config, "imagegen_provider", "openai")
    key = (getattr(config, "imagegen_api_key", "") or "").strip()
    model = getattr(config, "imagegen_model", "") or ""
    if provider == "openai":
        # Guard a model id left over from another provider (the field carries
        # across a provider switch) so OpenAI never gets e.g. a gemini id.
        m = model if model.startswith("gpt-image") else "gpt-image-2"
        return OpenAIImageModel(key, model=m, quality=config.imagegen_quality) if key else None
    if provider == "gemini":
        m = model if model.startswith("gemini") else "gemini-2.5-flash-image"
        return GeminiImageModel(key, model=m) if key else None
    if provider == "replicate":
        m = model if ("/" in model or ":" in model) else "black-forest-labs/flux-kontext-pro"
        return ReplicateImageModel(key, model=m) if key else None
    if provider == "a1111":
        base = (getattr(config, "imagegen_base_url", "") or "").strip()
        return A1111ImageModel(base, model=model) if base else None
    return None


def pick_reference_plates(common_name: str = "", k: int = 3,
                          rng: Optional[random.Random] = None) -> list[Path]:
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
    # Fill the remaining slots. Without an rng this is the deterministic
    # preferred-first order; with one, the fill is shuffled so consecutive
    # generations of a species don't anchor to the very same sheets.
    fill = [by_sci.get(sci) for sci in _PREFERRED_REFS]
    fill += [e for e in species if isinstance(e, dict)]
    if rng is not None:
        fill = list(fill)
        rng.shuffle(fill)
    for entry in fill:
        _try(entry)
    return chosen[:k]


class GeneratedArtProvider(ArtProvider):
    """Serve AI-generated plates from the disk cache; generate on first miss
    when a model is configured. Never raises."""

    name = "generated"

    def __init__(self, model: Optional[ImageModel],
                 cache_dir: Optional[Path] = None,
                 refs: Optional[list[Path]] = None,
                 cooldown_s: float = 900.0,
                 text_model: Optional[TextModel] = None) -> None:
        self._model = model
        self._text_model = text_model
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

    def _descriptions_path(self) -> Path:
        # Lives beside (not inside) the plate cache: cached_species globs the
        # cache dir's *.json and must not list the briefs as a phantom plate.
        return self._dir().parent / "descriptions.json"

    def _describe(self, common_name: str,
                  scientific_name: str) -> tuple[str, bool, list]:
        """The cached naturalist's brief, the is-it-a-bird verdict, and the
        pool of real associate plants. Bought once per species (an old entry
        without plants is re-bought once to upgrade it); soft-fails to
        ("", True, []) so a text-model outage never blocks a plate."""
        key = slugify(scientific_name or common_name)
        path = self._descriptions_path()
        with _DESC_LOCK:
            return self._describe_locked(key, path, common_name, scientific_name)

    def _describe_locked(self, key: str, path: Path, common_name: str,
                         scientific_name: str) -> tuple[str, bool, list]:
        try:
            cache = json.loads(path.read_text())
        except (OSError, ValueError):
            cache = {}
        hit = cache.get(key)
        if isinstance(hit, dict) and hit.get("description") and (
                "plants" in hit or self._text_model is None):
            return (str(hit["description"]), bool(hit.get("is_bird", True)),
                    list(hit.get("plants") or []))
        if self._text_model is None:
            return "", True, []
        subject = (f"{common_name} ({scientific_name})"
                   if scientific_name else common_name)
        try:
            out = self._text_model.complete_json(
                DESCRIBE_PROMPT.format(subject=subject))
            description = str(out.get("description", "")).strip()
            is_bird = bool(out.get("is_bird", True))
            plants = [p for p in (out.get("plants") or [])
                      if isinstance(p, dict) and p.get("name") and p.get("look")]
        except Exception as exc:
            log.warning("describe failed for %s (%s): %s", subject,
                        getattr(self._text_model, "name", "?"), exc)
            return "", True, []
        if description:
            cache[key] = {
                "description": description,
                "is_bird": is_bird,
                "plants": plants,
                "model": getattr(self._text_model, "name", "unknown"),
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            self._write_atomic(path, json.dumps(cache, indent=2).encode())
        return description, is_bird, plants

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

    # -- the nightly day-in-review composite --------------------------------
    _KEEP_SHEETS = 60  # pruned oldest-first; the SD card is finite

    def day_composite(self, cells, when, force: bool = False):
        """One generated composite sheet for the day's top species, in the
        manner of the folio's late totem plates. Bought at most once per date.
        Returns (art, cells_as_painted) — on a cache hit the cells come from
        the sidecar, so the key under the sheet always names the figures that
        were actually painted — or None (caller falls back to the grid).
        Never raises."""
        day = when.isoformat()
        png = paths.collages_dir() / f"{day}.png"
        sidecar = paths.collages_dir() / f"{day}.json"
        key = f"collage-{day}"
        try:
            if png.exists() and not force:
                return self._read_sheet(png, sidecar, cells)
            if self._model is None:
                return self._read_sheet(png, sidecar, cells) if png.exists() else None
            if not force and self._in_cooldown(key):
                return None
            subjects = [(c.common_name, c.scientific_name) for c in cells]
            briefs = {sci or common: self._describe(common, sci)[0]
                      for common, sci in subjects}  # description only
            prompt = build_composite_prompt(subjects, briefs)
            refs = self._refs if self._refs is not None else pick_composite_reference_plates()
            with _GEN_LOCK:
                if png.exists():
                    if not force:
                        return self._read_sheet(png, sidecar, cells, locked=True)
                    # Repaint debounce: two racing repaints (double-click, two
                    # tabs) must not both bill. A sheet younger than 3 minutes
                    # IS the repaint the second caller asked for.
                    if self._sheet_age_s(sidecar) < 180:
                        return self._read_sheet(png, sidecar, cells, locked=True)
                if not force and self._in_cooldown(key):
                    return None
                started = time.time()
                try:
                    png_bytes = self._model.generate(prompt, GEN_SIZE, refs)
                    Image.open(io.BytesIO(png_bytes)).verify()
                except Exception as exc:
                    self._failed_at[key] = time.time()
                    log.warning("day composite failed for %s (%s): %s", day,
                                getattr(self._model, "name", "?"), exc)
                    # A failed repaint keeps showing the good sheet it meant
                    # to replace, rather than falling to the grid.
                    if png.exists():
                        return self._read_sheet(png, sidecar, cells, locked=True)
                    return None
                payload = json.dumps({
                    "date": day,
                    "cells": [{"common": c.common_name, "scientific": c.scientific_name,
                               "count": c.count} for c in cells],
                    "model": getattr(self._model, "name", "unknown"),
                    "quality": getattr(self._model, "quality", None),
                    "prompt_version": PROMPT_VERSION,
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "created_ts": round(time.time(), 1),
                    "elapsed_s": round(time.time() - started, 1),
                }, indent=2)
                try:
                    self._write_atomic(sidecar, payload.encode())
                    self._write_atomic(png, png_bytes)
                except OSError as exc:
                    self._failed_at[key] = time.time()
                    log.warning("day-composite cache write failed for %s after a "
                                "paid generation: %s", day, exc)
                    try:
                        sidecar.unlink(missing_ok=True)
                    except OSError:
                        pass
                    return None
                self._failed_at.pop(key, None)
                self._prune_sheets()
                log.info("generated day composite for %s (%d species) in %.1fs",
                         day, len(cells), time.time() - started)
                return self._read_sheet(png, sidecar, cells, locked=True)
        except Exception:
            log.exception("day composite failed for %s", when)
            return None

    @staticmethod
    def _sheet_age_s(sidecar: Path) -> float:
        try:
            meta = json.loads(sidecar.read_text())
            return max(0.0, time.time() - float(meta.get("created_ts") or 0))
        except (OSError, ValueError, TypeError):
            return float("inf")

    def _read_sheet(self, png: Path, sidecar: Path, fallback_cells,
                    locked: bool = False):
        """Read a cached sheet plus the cells it was painted from. Self-heals a
        torn file — re-verified before deleting, and without re-acquiring the
        (non-reentrant) generation lock when the caller already holds it."""
        def _attempt():
            return plate.extract_generated(png)

        try:
            art = _attempt()
        except (OSError, ValueError):
            if not locked:
                with _GEN_LOCK:
                    art = self._retry_or_heal(_attempt, png, sidecar)
            else:
                art = self._retry_or_heal(_attempt, png, sidecar)
            if art is None:
                return None
        return art, self._sheet_cells(sidecar, fallback_cells)

    def _retry_or_heal(self, attempt, png: Path, sidecar: Path):
        try:
            return attempt()
        except (OSError, ValueError) as exc:
            log.warning("corrupt cached sheet %s (%s) — removing", png.name, exc)
            try:
                png.unlink(missing_ok=True)
                sidecar.unlink(missing_ok=True)
            except OSError:
                pass
            return None

    @staticmethod
    def _sheet_cells(sidecar: Path, fallback_cells) -> list[CollageCell]:
        try:
            meta = json.loads(sidecar.read_text())
            cells = meta.get("cells") if isinstance(meta, dict) else None
            if isinstance(cells, list) and cells:
                return [CollageCell(str(c.get("common") or ""),
                                    str(c.get("scientific") or ""),
                                    int(c.get("count") or 0))
                        for c in cells if isinstance(c, dict)]
        except (OSError, ValueError, TypeError):
            pass
        return list(fallback_cells)

    def _prune_sheets(self) -> None:
        """The per-day cache is unbounded by nature; keep the newest N."""
        try:
            sheets = sorted(paths.collages_dir().glob("????-??-??.png"))
            for old in sheets[:-self._KEEP_SHEETS]:
                old.unlink(missing_ok=True)
                old.with_suffix(".json").unlink(missing_ok=True)
        except OSError:
            pass

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
            description, is_bird, plants = self._describe(common_name, scientific_name)
            rng = random.Random()
            # A forced regenerate must not repeat the last sheet's draw.
            avoid, prev_plant = None, None
            if force:
                try:
                    prev = json.loads(self._sidecar(slug).read_text())
                    prev_dir = prev.get("art_direction") or {}
                    avoid = {k: prev_dir.get(k)
                             for k in ("tableau", "foliage", "figures", "armature")}
                    prev_plant = (prev.get("plant") or {}).get("name")
                except (OSError, ValueError):
                    pass
            pool = [p for p in plants if p.get("name") != prev_plant] or plants
            plant = rng.choice(pool) if pool else None
            direction, picks = _sample_direction(rng, is_bird, avoid)
            prompt = build_prompt(common_name, scientific_name, direction,
                                  description, plant)
            refs = (self._refs if self._refs is not None
                    else pick_reference_plates(common_name, rng=rng))
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
                "art_direction": picks,
                "description": description,
                "plant": plant,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "elapsed_s": round(time.time() - started, 1),
                "reference_plates": [p.name for p in refs],
            }, indent=2)
            # On a forced regenerate a good plate already exists; keep its
            # sidecar bytes so a failed write restores it instead of orphaning
            # the surviving PNG forever.
            try:
                old_sidecar = self._sidecar(slug).read_bytes()
            except OSError:
                old_sidecar = None
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
                    if old_sidecar is not None:
                        self._write_atomic(self._sidecar(slug), old_sidecar)
                    else:
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
