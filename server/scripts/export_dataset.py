"""Export the folios as the open dataset, github.com/wr/historical-bird-plates (W-868).

The folios in scripts/folios/ are Featherframe's source of truth for what is
pinned; the dataset is the public record of every plate, pinned or not, with
modern identifiers a reader outside Featherframe can use. This script writes a
folio's two tables into the dataset repo, checks the repo against the folios,
and builds a scanned folio's release images.

    export_dataset.py export  DATASET_DIR   # write havell/ and gould-{europe,australia,britain}/ tables
    export_dataset.py check   DATASET_DIR   # every pin is in species.csv, and back
    export_dataset.py assets  OUT_DIR --dataset DIR [--folio gould-australia]  # cleaned sheets + crops + manifest
    export_dataset.py assets  OUT_DIR --folio havell   # Havell: audubon.org's scans + lettering-free crops
    export_dataset.py thumbs  OUT_DIR --dataset DIR [--folio …]  # contact sheets of the crops, for the README

`export` asks three outside sources once and caches them (--cache): the eBird
taxonomy (the dataset's modern names and codes), BirdNET's V2.4 labels (only
to write the exact label; the file is CC BY-NC-SA and is never copied into the
dataset) and Wikidata (QID, GBIF and Avibase ids, looked up by eBird code).
--offline skips them; the pins and the check need none of them.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from typing import NamedTuple, Optional

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from featherframe import legends as legends_mod  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parents[1]
FOLIOS = SCRIPTS / "folios"
USER_AGENT = "historical-bird-plates export (+https://github.com/wr/historical-bird-plates)"

EBIRD_VERSION = "2025"
TAXONOMY = f"eBird/Clements {EBIRD_VERSION}"
EBIRD_URL = "https://api.ebird.org/v2/ref/taxonomy/ebird?fmt=csv&version=" + EBIRD_VERSION
BIRDNET_LABELS_URL = ("https://raw.githubusercontent.com/joeweiss/birdnetlib/main/src/birdnetlib/"
                      "models/analyzer/BirdNET_GLOBAL_6K_V2.4_Labels.txt")
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
WIKIDATA_QUERY = """SELECT ?item ?ebird ?avibase ?gbif WHERE {
  ?item wdt:P3444 ?ebird .
  OPTIONAL { ?item wdt:P2026 ?avibase }
  OPTIONAL { ?item wdt:P846 ?gbif }
}"""

# BirdNET V2.4 names a species as an older eBird year did. Where eBird 2025
# names it otherwise, the pin's binomial is carried to eBird's here, by folio
# when a split sends the two folios' birds to different daughters.
EBIRD_NAMES = {
    "Dryobates villosus": "Leuconotopicus villosus",
    "Dryobates borealis": "Leuconotopicus borealis",
    "Accipiter cooperii": "Astur cooperii",
    "Ixobrychus exilis": "Botaurus exilis",
    "Ixobrychus minutus": "Botaurus minutus",
    "Calocitta colliei": "Cyanocorax colliei",
    "Charadrius wilsonia": "Anarhynchus wilsonia",
    "Charadrius montanus": "Anarhynchus montanus",
    "Charadrius alexandrinus": "Anarhynchus alexandrinus",
    "Charadrius morinellus": "Eudromias morinellus",
    "Charadrius dubius": "Thinornis dubius",
    "Coccothraustes vespertinus": "Hesperiphona vespertina",
    "Phalacrocorax penicillatus": "Urile penicillatus",
    "Phalacrocorax pelagicus": "Urile pelagicus",
    "Apus melba": "Tachymarptis melba",
    "Corvus monedula": "Coloeus monedula",
    "Anthropoides virgo": "Grus virgo",
    "Bubulcus ibis": "Ardea ibis",
    # Lumped into Redpoll in 2025; the plate is the Lesser Redpoll form.
    "Acanthis cabaret": "Acanthis flammea",
    # Australia's crosswalk writes today's names as BirdNET and the checklists
    # of 2024 did; eBird 2025 has moved these.
    "Haliaeetus leucogaster": "Icthyophaga leucogaster",
    "Accipiter novaehollandiae": "Tachyspiza novaehollandiae",
    "Accipiter fasciatus": "Tachyspiza fasciata",
    "Accipiter cirrocephalus": "Tachyspiza cirrocephala",
    "Cracticus quoyi": "Melloria quoyi",
    "Sericornis citreogularis": "Neosericornis citreogularis",
    "Neochmia ruficauda": "Emblema ruficauda",
    "Neochmia modesta": "Emblema modestum",
    "Heteralocha acusirostris": "Heteralocha acutirostris",
    "Cacomantis pallidus": "Heteroscenes pallidus",
    "Chrysococcyx osculans": "Chalcites osculans",
    "Chrysococcyx lucidus": "Chalcites lucidus",
    "Chrysococcyx minutillus": "Chalcites minutillus",
    "Climacteris rufa": "Climacteris rufus",
    "Lophochroa leadbeateri": "Cacatua leadbeateri",
    "Calyptorhynchus funereus": "Zanda funerea",
    "Calyptorhynchus baudinii": "Zanda baudinii",
    "Psephotus varius": "Psephotellus varius",
    "Psephotus chrysopterygius": "Psephotellus chrysopterygius",
    "Glossopsitta concinna": "Trichoglossus concinnus",
    "Parvipsitta porphyrocephala": "Psitteuteles porphyrocephalus",
    "Parvipsitta pusilla": "Psitteuteles pusillus",
    "Ptilinopus magnificus": "Megaloprepia magnifica",
    "Charadrius veredus": "Anarhynchus veredus",
    "Charadrius bicinctus": "Anarhynchus bicinctus",
    "Charadrius ruficapillus": "Anarhynchus ruficapillus",
    "Charadrius mongolus": "Anarhynchus mongolus",
    "Elseyornis melanops": "Thinornis melanops",
    "Stiltia isabella": "Glareola isabella",
    "Ixobrychus flavicollis": "Botaurus flavicollis",
    "Ixobrychus dubius": "Botaurus dubius",
    "Eulabeornis castaneoventris": "Gallirallus castaneoventris",
    "Sceloglaux albifacies": "Ninox albifacies",
    "Tregellasia capito": "Eopsaltria capito",
    "Peneoenanthe pulverulenta": "Melanodryas pulverulenta",
    "Strigops habroptila": "Strigops habroptilus",
}
EBIRD_NAMES_BY_FOLIO = {
    # Splits since BirdNET's year that keep the old binomial on the Old World
    # daughter: Audubon's bird is the American one, Gould's the European.
    ("havell", "Accipiter gentilis"): "Astur atricapillus",
    ("gould_europe", "Accipiter gentilis"): "Astur gentilis",
    ("gould_britain", "Accipiter gentilis"): "Astur gentilis",
    ("havell", "Setophaga petechia"): "Setophaga aestiva",
    ("havell", "Tyto alba"): "Tyto furcata",
    ("havell", "Numenius phaeopus"): "Numenius hudsonicus",
    ("havell", "Larus argentatus"): "Larus smithsonianus",
    # Red-rumped Swallow, split in 2025: the binomial stays with the eastern bird.
    ("gould_europe", "Cecropis daurica"): "Cecropis rufula",
}
EBIRD_NOTES = {
    "Acanthis cabaret": "eBird 2025 lumps the redpolls; this is the Lesser Redpoll form (ISSF lesred1)",
    "Accipiter gentilis": "BirdNET's Northern Goshawk is split by eBird into American and Eurasian Goshawk",
    "Setophaga petechia": "eBird 2025 splits Yellow Warbler; the binomial stays with Mangrove Yellow Warbler",
    "Tyto alba": "eBird splits Barn Owl; the binomial stays with Western Barn Owl",
    "Numenius phaeopus": "eBird splits Whimbrel; the binomial stays with Eurasian Whimbrel",
    "Larus argentatus": "eBird splits Herring Gull; the binomial stays with European Herring Gull",
    "Cecropis daurica": "eBird 2025 splits Red-rumped Swallow; the binomial stays with Eastern Red-rumped Swallow",
}

SPECIES_COLUMNS = ["plate", "figure", "printed_name", "printed_latin", "scientific", "common",
                   "ebird_code", "taxonomy", "wikidata", "gbif", "avibase", "birdnet_label",
                   "form", "confidence", "caption_checked", "reason", "sources"]
GOULD_PLATE_COLUMNS = ["plate", "list_name", "list_latin", "caption_name", "caption_latin",
                       "volume", "bhl_barcode", "bhl_item", "leaf", "bhl_page", "orientation",
                       "rotate", "scan_url", "page_url", "sheet_asset", "crop_asset", "notes"]
HAVELL_PLATE_COLUMNS = ["plate", "title", "legend", "image_url", "sheet_asset", "crop_asset", "notes"]

# Per-volume folios (W-874) number plates within each volume: their tables
# lead with the volume, and a plate is keyed by the two together.
VOLUME_PLATE_COLUMNS = ["volume", "plate", "list_name", "list_latin", "caption_latin",
                        "bhl_barcode", "bhl_item", "leaf", "bhl_page", "orientation", "rotate",
                        "scan_url", "page_url", "sheet_asset", "crop_asset", "notes"]
VOLUME_SPECIES_COLUMNS = ["volume"] + SPECIES_COLUMNS
KU_COLUMNS = ["plate", "kind", "ku_id", "ku_name", "ku_scientific", "scientific", "common", "note"]

# BHL's ItemID for each volume scanned (data/item.txt.gz on the open-data bucket).
BHL_ITEMS = {
    "birdsEuropeIGoul": 132863, "birdsEuropeIIGoul": 132861, "birdsEuropeIIIGoul": 133913,
    "birdsEuropeIVGoul": 132862, "birdsEuropeVGoul": 133915,
    "birdsAustraliav1Goul": 186988, "birdsAustraliav2Goul": 187062, "birdsAustraliav3Goul": 187975,
    "birdsAustraliav4Goul": 191229, "birdsAustraliav5Goul": 188478, "birdsAustraliav6Goul": 188477,
    "birdsAustraliav7Goul": 189241, "birdsAustraliasSuppGoul": 189274,
    "birdsgreatbrita1goul": 221495, "birdsgreatbrita2goul": 221554, "birdsgreatbrita3goul": 221726,
    "birdsgreatbrita4goul": 221609, "birdsgreatbrita5goul": 222497,
}
BHL_BUCKET = "https://bhl-open-data.s3.us-east-2.amazonaws.com"
GOULD_CONFIDENCE = {"high": "high", "decided": "judged", "medium": "medium", "low": "low", "": "none"}
ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII"}


class Gould(NamedTuple):
    """One of Gould's folios in the dataset: its folio file, its working record
    and whether its plates are numbered per volume."""
    name: str
    docs: Path
    per_volume: bool

    @property
    def folder(self) -> str:
        return self.name.replace("_", "-")


GOULD = {g.folder: g for g in (
    Gould("gould_europe", REPO / "docs" / "gould-europe", False),
    Gould("gould_australia", REPO / "docs" / "gould-australia", True),
    # Only its seven gap-filling pins are checked; the rest is the survey's draft.
    Gould("gould_britain", REPO / "docs" / "gould-survey", True),
)}


def volume_label(v) -> str:
    """A per-volume folio's volume as its record writes it: I-VII, or Supp."""
    s = str(v).rstrip(".")
    return ROMAN[int(s)] if s.isdigit() else s


def plate_key(g: Gould, r: dict) -> tuple:
    """A row's (or a pin's) plate: (volume, number) in a per-volume folio."""
    if g.per_volume:
        return (volume_label(r.get("volume_no", r.get("volume"))), int(r["plate"]))
    return (int(r["plate"]),)


def plate_label(key: tuple) -> str:
    return ".".join(str(k) for k in key)


def scan_columns(tax: "Taxonomy", bc: str, leaf: int, orient: str, rot: int) -> dict:
    """Where a leaf is on BHL, and its two release images."""
    item = BHL_ITEMS[bc]
    page = tax.pages.get((item, leaf), "")
    return {"bhl_barcode": bc, "bhl_item": item, "leaf": leaf, "bhl_page": page,
            "page_url": f"https://www.biodiversitylibrary.org/page/{page}" if page else "",
            "orientation": orient, "rotate": rot,
            "scan_url": f"{BHL_BUCKET}/images/{bc}/{bc}_{leaf:04d}.jp2",
            "sheet_asset": sheet_name(bc, leaf), "crop_asset": crop_name(bc, leaf)}


def barcode(volume: str) -> str:
    return f"birdsEurope{volume}Goul"


def sheet_name(bc: str, leaf: int) -> str:
    return f"sheet-{bc}-{leaf:04d}.jpg"


def crop_name(bc: str, leaf: int) -> str:
    return f"crop-{bc}-{leaf:04d}.jpg"


def load_folio(name: str) -> dict:
    return yaml.safe_load((FOLIOS / f"{name}.yaml").read_text())


def pinned(folio: dict) -> list[dict]:
    return [e for e in folio["species"] if e.get("plate") not in (None, "none", False)]


def read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({c: "" if r.get(c) is None else r.get(c) for c in columns})


# --- outside sources -------------------------------------------------------

def _cached(cache: Path, name: str, fetch) -> Path:
    p = cache / name
    if not p.exists():
        cache.mkdir(parents=True, exist_ok=True)
        p.write_bytes(fetch())
    return p


def _get(url: str, **kw) -> bytes:
    r = requests.get(url, headers={"User-Agent": USER_AGENT, **kw.pop("headers", {})}, timeout=120, **kw)
    r.raise_for_status()
    return r.content


class Taxonomy:
    """eBird names and codes, BirdNET labels and Wikidata ids; empty offline."""

    def __init__(self, cache: Optional[Path]) -> None:
        self.ebird: dict[str, dict] = {}
        self.labels: dict[str, str] = {}
        self.wikidata: dict[str, dict] = {}
        self.pages: dict[tuple[int, int], str] = {}
        if cache is None:
            return
        # BHL PageIDs: the bucket's OCR files are named item-<item>-<page>-<leaf>.txt.
        for item in BHL_ITEMS.values():
            keys = _cached(cache, f"bhl-ocr-{item}.xml", lambda item=item: _get(
                f"{BHL_BUCKET}/", params={"list-type": "2", "prefix": f"ocr/item-{item}/"}))
            for m in re.finditer(r"<Key>ocr/item-\d+/item-\d+-(\d+)-(\d+)\.txt</Key>", keys.read_text()):
                self.pages[(item, int(m.group(2)))] = str(int(m.group(1)))
        eb = _cached(cache, f"ebird-{EBIRD_VERSION}.csv", lambda: _get(EBIRD_URL))
        self.ebird = {r["SCIENTIFIC_NAME"]: r for r in read_csv(eb)}
        lb = _cached(cache, "BirdNET_GLOBAL_6K_V2.4_Labels.txt", lambda: _get(BIRDNET_LABELS_URL))
        for line in lb.read_text(encoding="utf-8").splitlines():
            if "_" in line:
                self.labels[line.split("_", 1)[0]] = line.strip()
        wd = _cached(cache, "wikidata-ebird.csv", lambda: _get(
            WIKIDATA_SPARQL, params={"query": WIKIDATA_QUERY}, headers={"Accept": "text/csv"}))
        for r in read_csv(wd):
            rec = self.wikidata.setdefault(r["ebird"], {"qids": set(), "avibase": set(), "gbif": set()})
            rec["qids"].add(r["item"].rsplit("/", 1)[-1])
            if r["avibase"]:
                rec["avibase"].add(r["avibase"])
            if r["gbif"]:
                rec["gbif"].add(r["gbif"])

    def ids(self, folio: str, sci: str, synonyms=(), birdnet: Optional[str] = None) -> dict:
        """The dataset's modern columns for a pin's (BirdNET) binomial, or for
        a modern binomial and the BirdNET label it falls under."""
        out = {"birdnet_label": self.labels.get(sci if birdnet is None else birdnet, "")}
        if not sci or not self.ebird:
            return out
        name = EBIRD_NAMES_BY_FOLIO.get((folio, sci)) or EBIRD_NAMES.get(sci) or sci
        row = self.ebird.get(name) or next((self.ebird[s] for s in synonyms if s in self.ebird), None)
        if row is None:
            return out
        out.update(scientific=row["SCIENTIFIC_NAME"], common=row["COMMON_NAME"],
                   ebird_code=row["SPECIES_CODE"], taxonomy=TAXONOMY)
        wd = self.wikidata.get(row["SPECIES_CODE"])
        # One id each or none: an eBird code on several items is left for a human.
        if wd:
            for col, key in (("wikidata", "qids"), ("gbif", "gbif"), ("avibase", "avibase")):
                if len(wd[key]) == 1:
                    out[col] = next(iter(wd[key]))
        return out


def modern(tax: Taxonomy, folio: str, sci: str, common: str, synonyms=(),
           birdnet: Optional[str] = None) -> dict:
    """Modern columns, falling back to the pin's own names when eBird has none."""
    out = {"scientific": sci, "common": common}
    out.update(tax.ids(folio, sci, synonyms, birdnet))
    note = EBIRD_NOTES.get(sci)
    return out | ({"_note": note} if note else {})


# --- Havell ----------------------------------------------------------------

# Left out of havell.yaml on purpose (see its "Full Havell edition" note).
HAVELL_DISPUTED = {
    11: "Bird of Washington: not a valid species",
    55: "Cuvier's Kinglet: not a valid species",
    60: "Carbonated Warbler: not a valid species",
    164: "Tawny Thrush: traditionally the Veery, disputed (Halley 2018)",
    184: "Mangrove Humming Bird: disputed",
    338: "Bemaculated Duck: a hybrid",
    400: "Townsend's Bunting: not a valid species",
    407: "Dusky Albatros: Light-mantled (Pitt, NYHS) or Sooty (Commons), unsettled",
    434: "Small-headed Flycatcher and Blue Mountain Warbler: not valid species",
}

def havell_titles(catalog: list[dict], folio: dict) -> dict[int, str]:
    """Each plate's title. The mirror's `name` runs one plate late from 361
    to 399 (its fileName, and so its image, is right): 361-398 take the next
    plate's name, and 399, whose own name is lost, takes the folio's title."""
    names = {int(c["plate"]): c.get("name", "") for c in catalog}
    titles = dict(names)
    for n in range(361, 399):
        titles[n] = names.get(n + 1, "")
    t399 = next((str(e.get("audubon_title", "")) for e in pinned(folio) if int(e["plate"]) == 399), "")
    titles[399] = t399.replace("(composite)", "").strip()
    return titles


def havell_tables(tax: Taxonomy, catalog: list[dict]) -> tuple[list[dict], list[dict]]:
    folio = load_folio("havell")
    names = havell_titles(catalog, folio)
    legends = legends_mod.load()
    by_plate: dict[int, list[dict]] = {}
    for e in pinned(folio):
        by_plate.setdefault(int(e["plate"]), []).append(e)
    titles = {int(c["plate"]): c for c in catalog}
    plates, species = [], []
    for n in range(1, 436):
        c = titles.get(n, {})
        leg = legends.get(n, {"lines": [], "composite": False})
        plates.append({"plate": n, "title": names.get(n, ""), "legend": " | ".join(leg["lines"]),
                       "image_url": c.get("download", ""),
                       "sheet_asset": f"sheet-{n:03d}.jpg", "crop_asset": f"crop-{n:03d}.jpg"})
        entries = by_plate.get(n)
        if not entries:
            species.append({"plate": n, "printed_name": names.get(n, ""), "confidence": "none",
                            "reason": HAVELL_DISPUTED.get(n) or "not identified here yet: "
                            "Featherframe pins one plate per species, and this is likely a second plate of one"})
            continue
        for e in entries:
            title = str(e.get("audubon_title", "")).replace("(composite)", "").strip()
            m = modern(tax, "havell", e["scientific"], e["common"], e.get("sci_synonyms") or ())
            figure = ""
            if leg["composite"]:
                lines = legends_mod.resolve(e.get("audubon_title", ""), True, leg["lines"])
                figure = lines[0] if lines and lines[0] not in leg["lines"] else ""
            reason = f"Audubon's “{title}” is today's {m['common']}" if title and title != m["common"] else ""
            if m.get("_note"):
                reason = "; ".join(x for x in (reason, m["_note"]) if x)
            species.append({"plate": n, "figure": figure, "printed_name": title, **m,
                            "confidence": "high", "reason": reason,
                            "sources": "Featherframe havell.yaml; plate title from audubon.org"})
    return plates, species


# --- Gould's folios --------------------------------------------------------

# The Birds of Europe: plates numbered by the General List, found by the
# copy's pencilled numbers.

def gould_leaves(folio: dict) -> dict[int, list[tuple[str, int]]]:
    """Plate -> its leaves: the pins' own, else the copy's pencilled number on
    a leaf no pin claims for another plate."""
    by_plate: dict[int, list[tuple[str, int]]] = {}
    claimed = set()
    for e in pinned(folio):
        vol = str(e["volume"]).removeprefix("birdsEurope").removesuffix("Goul")
        key = (vol, int(e["leaf"]))
        claimed.add(key)
        if key not in by_plate.setdefault(int(e["plate"]), []):
            by_plate[int(e["plate"])].append(key)
    for r in read_csv(GOULD["gould-europe"].docs / "plate-leaves.csv"):
        if r["pencil"].isdigit():
            key = (r["volume"], int(r["leaf"]))
            n = int(r["pencil"])
            if key not in claimed and n not in by_plate:
                by_plate[n] = [key]
    return by_plate


def europe_tables(tax: Taxonomy) -> tuple[list[dict], list[dict]]:
    folio = load_folio("gould_europe")
    header = folio["folio"]
    listed: dict[int, list[dict]] = {}
    for r in read_csv(GOULD["gould-europe"].docs / "general-list.csv"):
        listed.setdefault(int(r["plate"]), []).append(r)
    leaves = {(r["volume"], int(r["leaf"])): r for r in read_csv(GOULD["gould-europe"].docs / "plate-leaves.csv")}
    rotate = {}
    for e in pinned(folio):
        vol = str(e["volume"]).removeprefix("birdsEurope").removesuffix("Goul")
        rotate[(vol, int(e["leaf"]))] = int(e.get("rotate") or 0)
    by_leaf = gould_leaves(folio)
    pins = {(int(e["plate"]), e["scientific"]): e for e in pinned(folio)}

    plates = []
    for n in sorted(listed):
        rows = listed[n]
        base = {"plate": n, "list_name": " / ".join(r["english"] for r in rows),
                "list_latin": " / ".join(r["latin"] for r in rows)}
        found = by_leaf.get(n) or []
        if not found:
            plates.append(base | {"notes": "no leaf found for this number in the Smithsonian copy"})
        for vol, leaf in found:
            lr = leaves.get((vol, leaf), {})
            bc = barcode(vol)
            orient = lr.get("orientation", "")
            rot = rotate.get((vol, leaf), 270 if orient == "landscape" else 0)
            notes = [x for x in [lr.get("note", "")] if x]
            if (vol, leaf) not in rotate:
                notes.append("leaf found by its pencilled number; no pin checked its caption")
            plates.append(base | {
                "caption_name": lr.get("caption_english", ""), "caption_latin": lr.get("caption_latin", ""),
                "volume": vol, **scan_columns(tax, bc, leaf, orient, rot),
                "notes": "; ".join(notes)})

    species, seen = [], set()
    for r in read_csv(GOULD["gould-europe"].docs / "crosswalk.csv"):
        n, sci = int(r["plate"]), r["birdnet_sci"]
        pin = pins.get((n, sci))
        syn = [s for s in (r.get("sci_synonyms") or "").split(";") if s]
        row = {"plate": n, "printed_name": r["gould_english"], "printed_latin": r["gould_latin"],
               "form": r["form"], "confidence": GOULD_CONFIDENCE.get(r["confidence"], r["confidence"]),
               "reason": r["reason"], "sources": "General List (vol. I); crosswalk"}
        if sci:
            m = modern(tax, "gould_europe", sci, r["birdnet_common"], syn)
            if m.get("_note"):
                row["reason"] = "; ".join(x for x in (row["reason"], m["_note"]) if x)
            row.update({k: v for k, v in m.items() if not k.startswith("_")})
        if pin:
            seen.add((n, sci))
            row["caption_checked"] = "yes"
            row["figure"] = " ".join(pin.get("legend") or [])
            row["sources"] = "General List (vol. I); engraved caption on the leaf; crosswalk"
        else:
            row["caption_checked"] = "no"
        species.append(row)
    # A pin the crosswalk has no row for (a plate pinned to two daughters).
    for (n, sci), e in pins.items():
        if (n, sci) in seen:
            continue
        m = modern(tax, "gould_europe", sci, e["common"], e.get("sci_synonyms") or ())
        species.append({"plate": n, "figure": " ".join(e.get("legend") or []),
                        "printed_name": e.get("gould_title", ""), **{k: v for k, v in m.items() if not k.startswith("_")},
                        "confidence": "judged", "caption_checked": "yes",
                        "reason": "pinned beside the crosswalk's row for this plate",
                        "sources": "engraved caption on the leaf; gould_europe.yaml"})
    species.sort(key=lambda r: (int(r["plate"]), r.get("figure") or "", r.get("scientific") or ""))
    return plates, species


# What each disagreement with the Kansas catalogue is, read from the plate
# (docs/gould-europe/README.md has the reasoning for the errors and crossings).
KU_KINDS = {
    **{n: ("error", "KU names another species; the caption and the bird agree with ours")
       for n in (14, 50, 65, 67, 75, 86, 108, 137, 149, 205, 360, 442)},
    63: ("error", "KU names an Australian monarch"),
    130: ("error", "KU names the Dunlin on the Wren's plate"),
    219: ("crossed", "Gould's Latin has since moved to the other chough; the plate shows the long red bill"),
    391: ("crossed", "Gould's Latin has since moved to the other grebe; the plate shows the black neck and fanned ear plumes"),
    273: ("typo", "KU's genus Aquila for Ardea; same species"),
    274: ("typo", "KU's genus Aquila for Ardea; same species"),
    276: ("typo", "KU's genus Aquila for Ardea; same species"),
    247: ("typo", "a space inside KU's binomial; same species"),
    371: ("typo", "a space inside KU's binomial; same species"),
    194: ("old name", "an older binomial for the same species"),
    259: ("old name", "Gould's own binomial for the same species"),
    321: ("old name", "Gould's name for the dark morph of the Common Snipe"),
    217: ("pre-split", "KU gives the parent species before the split"),
    287: ("pre-split", "KU gives the parent species before the split"),
    151: ("composite", "KU's English and Latin name different figures; the sheet shows both"),
}


def ku_disagreements(species: list[dict], tax: Taxonomy) -> list[dict]:
    """Where the Kansas catalogue names a different species for a checked
    plate: not a synonym, a subspecies or an older name of the same eBird
    species. (A pre-split parent KU gives is a disagreement of name only and
    is listed; the README says which rows those are.)"""
    ku = {int(r["plate"]): r for r in read_csv(GOULD["gould-europe"].docs / "ku-catalogue.csv")}
    syn: dict[tuple[int, str], set] = {}
    for r in read_csv(GOULD["gould-europe"].docs / "crosswalk.csv"):
        syn.setdefault((int(r["plate"]), r["birdnet_sci"]), set()).update(
            s for s in (r.get("sci_synonyms") or "").split(";") if s)
    for e in pinned(load_folio("gould_europe")):
        syn.setdefault((int(e["plate"]), e["scientific"]), set()).update(e.get("sci_synonyms") or [])
    checked: dict[int, list[dict]] = {}
    for r in species:
        if r.get("caption_checked") == "yes":
            checked.setdefault(int(r["plate"]), []).append(r)
    out = []
    for n, rows in sorted(checked.items()):
        k = ku.get(n)
        if not k or not k["ku_sci"]:
            continue
        ours, codes = set(), {r["ebird_code"] for r in rows if r.get("ebird_code")}
        for r in rows:
            bn = r["birdnet_label"].split("_")[0] if r.get("birdnet_label") else r["scientific"]
            ours |= {r["scientific"], bn} | syn.get((n, bn), set())
        binomial = " ".join(k["ku_sci"].split()[:2])
        ku_code = tax.ids("gould_europe", binomial).get("ebird_code")
        if binomial not in ours and not (ku_code and ku_code in codes):
            kind, note = KU_KINDS.get(n, ("", ""))
            out.append({"plate": n, "kind": kind, "note": note, "ku_id": k["ku_id"], "ku_name": k["ku_english"],
                        "ku_scientific": k["ku_sci"],
                        "scientific": " / ".join(r["scientific"] for r in rows),
                        "common": " / ".join(r["common"] for r in rows)})
    return out


# The Birds of Australia and its Supplement: plates numbered per volume, every
# leaf read against its engraved caption (docs/gould-australia/README.md).

# Unpinned sideways plates whose caption runs up the left edge, not down the
# right: stood up the other way (the cassowaries, seen on the contact sheets).
AUSTRALIA_ROTATE_90 = {("Supp", 71), ("Supp", 73), ("Supp", 75)}


def australia_tables(tax: Taxonomy) -> tuple[list[dict], list[dict]]:
    g = GOULD["gould-australia"]
    pins = {plate_key(g, e): e for e in pinned(load_folio(g.name))}
    leaves = {plate_key(g, r): r for r in read_csv(g.docs / "plate-leaves.csv")}
    ku_check = {plate_key(g, r): r for r in read_csv(g.docs / "ku-check.csv")}
    cross = read_csv(g.docs / "crosswalk.csv")
    listed = {plate_key(g, r): r for r in cross}

    plates = []
    for key, r in leaves.items():
        pin = pins.get(key)
        orient = r["orientation"]
        rot = int(pin.get("rotate") or 0) if pin else (
            90 if key in AUSTRALIA_ROTATE_90 else 270 if orient == "landscape" else 0)
        notes = []
        if r["fold_out"] == "yes":
            notes.append("a fold-out, bound folded")
        if r["agrees"] != "yes":
            notes.append(r["note"])
        if not pin:
            notes.append("not pinned in Featherframe: stood up and cut by the folio's rules, the crop not reviewed")
        x = listed[key]
        plates.append({"volume": key[0], "plate": key[1], "list_name": x["gould_english"],
                       "list_latin": x["gould_latin"], "caption_latin": r["caption_latin"],
                       **scan_columns(tax, r["ia_id"], int(r["leaf"]), orient, rot),
                       "notes": "; ".join(notes)})

    species = []
    for x in cross:
        key = plate_key(g, x)
        leaf, pin, k = leaves[key], pins.get(key), ku_check.get(key)
        sources = [f"List of Plates (vol. {key[0]})", "Gould's text and synonymy", "crosswalk"]
        if leaf["agrees"] == "yes":
            sources.insert(1, "engraved caption on the leaf")
        row = {"volume": key[0], "plate": key[1], "printed_name": x["gould_english"],
               "printed_latin": x["gould_latin"],
               "confidence": GOULD_CONFIDENCE.get(x["confidence"], x["confidence"]),
               "caption_checked": "yes" if leaf["agrees"] == "yes" else "no",
               "reason": x["reason"], "sources": "; ".join(sources)}
        if k and k["verdict"] == "crosswalk-error":
            row["reason"] = "; ".join((row["reason"], "the Kansas cross-check: " + k["reason"]))
        # A species, not "Cisticola sp." or a hybrid (which keep their reason).
        if re.fullmatch(r"[A-Z][a-z]+ [a-z]+", x["modern_sci"]):
            m = modern(tax, g.name, x["modern_sci"], x["birdnet_common"], birdnet=x["birdnet_sci"] or "")
            if m.get("_note"):
                row["reason"] = "; ".join(v for v in (row["reason"], m["_note"]) if v)
            row.update({c: v for c, v in m.items() if not c.startswith("_")})
        if pin:
            row["sources"] += f"; pinned in Featherframe ({g.name}.yaml)"
        species.append(row)
    return plates, species


def australia_ku(species: list[dict]) -> list[dict]:
    """The Kansas catalogue's disagreements, each settled (ku-check.csv)."""
    g = GOULD["gould-australia"]
    ku = {plate_key(g, r): r for r in read_csv(g.docs / "ku-catalogue.csv")}
    ours = {(r["volume"], int(r["plate"])): r for r in species}
    out = []
    for r in read_csv(g.docs / "ku-check.csv"):
        key = plate_key(g, r)
        k, o = ku.get(key, {}), ours.get(key, {})
        out.append({"volume": key[0], "plate": key[1], "kind": r["verdict"], "ku_id": k.get("ku_id", ""),
                    "ku_name": r["ku_english"], "ku_scientific": r["ku_sci"],
                    "scientific": o.get("scientific", ""), "common": o.get("common", ""), "note": r["reason"]})
    return out


# The Birds of Great Britain: seven gap-filling pins, each checked against its
# caption; every other plate is the survey's draft (docs/plate-sources.md),
# published as an open question.
SURVEY_REASON = "a draft from the survey of the folio: the List's name carried to a modern one, not yet reviewed"


def britain_tables(tax: Taxonomy, europe: list[dict]) -> tuple[list[dict], list[dict]]:
    g = GOULD["gould-britain"]
    pins = {plate_key(g, e): e for e in pinned(load_folio(g.name))}
    europe_sci = {}
    for r in europe:
        if r.get("scientific") and r.get("caption_checked") == "yes":
            europe_sci.setdefault(int(r["plate"]), set()).add(r["scientific"])
    plates, species = [], []
    for r in read_csv(g.docs / "britain-plates.csv"):
        key = plate_key(g, r)
        pin = pins.get(key)
        rot = int(pin.get("rotate") or 0) if pin else int(r["rotate"] or 0)
        caption = pin["gould_title"].split(", ", 1)[-1] if pin else ""
        plates.append({"volume": key[0], "plate": key[1], "list_name": r["printed_english"],
                       "list_latin": r["printed_latin"], "caption_latin": caption,
                       **scan_columns(tax, r["ia_id"], int(r["leaf"]), r["orientation"], rot),
                       "notes": "" if pin else "leaf paired with the List by the binding's order and OCR; "
                                               "its caption not read"})
        row = {"volume": key[0], "plate": key[1], "printed_name": r["printed_english"],
               "printed_latin": r["printed_latin"]}
        if pin:
            m = modern(tax, g.name, pin["scientific"], pin["common"], pin.get("sci_synonyms") or ())
            row |= {c: v for c, v in m.items() if not c.startswith("_")} | {
                "confidence": "high", "caption_checked": "yes", "reason": m.get("_note", ""),
                "sources": f"engraved caption on the leaf; pinned in Featherframe ({g.name}.yaml)"}
        else:
            reason = [SURVEY_REASON]
            if r["modern_sci"] and r["europe_plate"] and r["modern_sci"] in europe_sci.get(int(r["europe_plate"]), ()):
                reason.append(f"agrees with The Birds of Europe, plate {r['europe_plate']}")
            if r["note"]:
                reason.append(r["note"])
            row |= {"confidence": "low" if r["confidence"] == "low" else "medium",
                    "caption_checked": "no", "sources": "survey (sources/survey.csv)"}
            if r["modern_sci"]:
                m = modern(tax, g.name, r["modern_sci"], r["birdnet_common"],
                           birdnet=r["modern_sci"] if r["in_birdnet"] == "True" else "")
                if m.get("_note"):
                    reason.append(m["_note"])
                row |= {c: v for c, v in m.items() if not c.startswith("_")}
            row["reason"] = "; ".join(reason)
        species.append(row)
    return plates, species


# --- commands --------------------------------------------------------------

def havell_catalog(plates_dir: Optional[Path]) -> list[dict]:
    if plates_dir and (plates_dir / "data.json").exists():
        return json.loads((plates_dir / "data.json").read_text())
    return json.loads(_get("https://raw.githubusercontent.com/nathanbuchar/audubon-bird-plates/master/data.json"))


def export(out: Path, tax: Taxonomy, plates_dir: Optional[Path]) -> None:
    hp, hs = havell_tables(tax, havell_catalog(plates_dir))
    write_csv(out / "havell" / "plates.csv", HAVELL_PLATE_COLUMNS, hp)
    write_csv(out / "havell" / "species.csv", SPECIES_COLUMNS, hs)
    print(f"havell: {len(hp)} plates, {sum(1 for r in hs if r.get('scientific'))} identifications")
    ep, es = europe_tables(tax)
    ap, as_ = australia_tables(tax)
    bp, bs = britain_tables(tax, es)
    tables = {"gould-europe": (ep, es, ku_disagreements(es, tax),
                               ("general-list.csv", "plate-leaves.csv", "crosswalk.csv", "ku-catalogue.csv")),
              "gould-australia": (ap, as_, australia_ku(as_),
                                  ("plate-leaves.csv", "crosswalk.csv", "ku-catalogue.csv", "ku-check.csv",
                                   "review.csv", "doubtful-decisions.csv")),
              "gould-britain": (bp, bs, None, ())}
    for folder, (plates, species, ku, sources) in tables.items():
        g = GOULD[folder]
        vol = ["volume"] if g.per_volume else []
        write_csv(out / folder / "plates.csv", VOLUME_PLATE_COLUMNS if g.per_volume else GOULD_PLATE_COLUMNS, plates)
        write_csv(out / folder / "species.csv", VOLUME_SPECIES_COLUMNS if g.per_volume else SPECIES_COLUMNS, species)
        if ku is not None:
            write_csv(out / folder / "ku-disagreements.csv", vol + KU_COLUMNS, ku)
        src = out / folder / "sources"
        src.mkdir(parents=True, exist_ok=True)
        for name in sources:
            shutil.copyfile(g.docs / name, src / name)
        if folder == "gould-britain":
            shutil.copyfile(g.docs / "britain-plates.csv", src / "survey.csv")
        print(f"{folder}: {len(plates)} plate rows, {sum(1 for r in species if r.get('scientific'))} identifications, "
              f"{sum(1 for r in species if r.get('caption_checked') == 'yes')} caption-checked"
              + (f", {len(ku)} Kansas disagreements" if ku is not None else ""))


def check(out: Path) -> list[str]:
    """Every pin in the folios is a row in species.csv. Names are compared as
    the pin wrote them: the BirdNET label's binomial, or the dataset's own
    when it has none (and Australia's modern name for its BirdNET label).
    Where only the pins were read against their captions (Europe, Britain),
    every caption-checked row is also a pin."""
    errors = []
    folios = [("havell", "havell", None)] + [(g.name, g.folder, g) for g in GOULD.values()]
    for folio_name, folder, g in folios:
        rows = read_csv(out / folder / "species.csv")

        def key(r):
            return plate_key(g, r) if g else (int(r["plate"]),)

        def names(r):
            return {r["birdnet_label"].split("_")[0]} if r["birdnet_label"] else {r["scientific"]}

        have = {(key(r), n) for r in rows if r["scientific"] for n in names(r)}
        aliases = {}
        if folio_name == "gould_australia":
            for x in read_csv(g.docs / "crosswalk.csv"):
                aliases[(plate_key(g, x), x["birdnet_sci"])] = x["modern_sci"]
        pins = pinned(load_folio(folio_name))
        for e in pins:
            sci, k = e["scientific"], key(e)
            options = {sci, EBIRD_NAMES.get(sci), EBIRD_NAMES_BY_FOLIO.get((folio_name, sci)),
                       aliases.get((k, sci))} - {None}
            if not any((k, s) in have for s in options):
                errors.append(f"{folder}: pin {e['common']} ({sci}) on plate {plate_label(k)} is not in species.csv")
        if folio_name in ("gould_europe", "gould_britain"):
            pinset = {(key(e), e["scientific"]) for e in pins}
            for r in rows:
                if r["caption_checked"] == "yes" and not any((key(r), n) in pinset for n in names(r)):
                    errors.append(f"{folder}: plate {plate_label(key(r))} {r['scientific']} "
                                  "is caption-checked but not pinned")
    return errors


# Plates whose tight crop loses the bird, cut whole instead (caption kept).
WHOLE_CROPS = {
    ("gould-europe", "436"): "the Ivory Gull, white on white paper: the tight box finds only its perch",
}


def unpinned_margins(g: Gould, header: dict) -> dict:
    """The margins for a leaf no pin has cut, by (barcode, leaf): an upright
    Australia plate stops above its caption and clear of the binding line,
    as its pins do (docs/gould-australia/README.md); the rest take the
    folio's own."""
    out = {}
    if g.folder == "gould-australia":
        for r in read_csv(g.docs / "plate-leaves.csv"):
            if r["orientation"] == "portrait" and r["caption_top"]:
                out[(r["ia_id"], int(r["leaf"]))] = [0.02, 0.02, 0.955, round(float(r["caption_top"]) - 0.006, 3)]
    return out


def assets(out: Path, dataset: Path, plates_dir: Path, work: Path, folder: str = "gould-europe") -> None:
    """Every leaf of a Gould folio's plates.csv as a cleaned sheet (what
    fetch_plates stores) and an art crop (the plate library's colour cut),
    plus a sha256 manifest. A pinned sheet already fetched is taken from
    plates_dir; the rest are fetched from BHL into work/."""
    import fetch_plates
    from featherframe.plate_library import _raw_color_crop
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    g = GOULD[folder]
    folio = load_folio(g.name)
    header = folio["folio"]
    params = {}
    for e in pinned(folio):
        params.setdefault((str(e["volume"]), int(e["leaf"])), e)
    fallback = unpinned_margins(g, header)
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    manifest = {}
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    for r in read_csv(dataset / folder / "plates.csv"):
        if not r["leaf"] or r["sheet_asset"] in manifest:
            continue
        leaf, bc = int(r["leaf"]), r["bhl_barcode"]
        label = f"{r['volume']}.{r['plate']}" if g.per_volume else r["plate"]
        e = params.get((bc, leaf), {})
        sheet, crop = out / r["sheet_asset"], out / r["crop_asset"]
        if not sheet.exists():
            have = plates_dir / "img" / g.name / f"{bc}-{leaf:04d}.jpg"
            # Only a pin's own sheet: an unpinned leaf in the cache may have been stood up otherwise.
            if not (e and have.exists()):
                have = work / f"{bc}-{leaf:04d}.jpg"
                if not have.exists():
                    raw = have.with_suffix(".jp2")
                    if not fetch_plates._fetch_to(session, r["scan_url"], raw):
                        print(f"  !  could not fetch {r['scan_url']}")
                        continue
                    fetch_plates.store_scan(raw, have, int(r["rotate"] or 0), True)
                    raw.unlink(missing_ok=True)
            shutil.copyfile(have, sheet)
        if not crop.exists():
            # An unpinned sheet is cut as one vignette: the tight box stops at
            # the paper gap above its caption.
            composite = bool(e.get("composite")) or (folder, r["plate"]) in WHOLE_CROPS
            margins = (e.get("margins") or fallback.get((bc, leaf))
                       or (header.get("volume_margins") or {}).get(bc) or header.get("margins"))
            img = _raw_color_crop(sheet, composite, e.get("crop_box"), margins, bool(header.get("tight")))
            img.convert("RGB").save(crop, format="JPEG", quality=95)
        for p in (sheet, crop):
            manifest[p.name] = {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                                "bytes": p.stat().st_size, "plate": label if g.per_volume else int(label)}
        print(f"  ✓  plate {label}: {sheet.name}")
    write_manifest(out, folder, manifest)


def write_manifest(out: Path, folio: str, manifest: dict) -> None:
    (out / "manifest.json").write_text(json.dumps({"folio": folio, "files": manifest},
                                                  indent=1, sort_keys=True) + "\n")
    (out / "SHA256SUMS").write_text("".join(f"{v['sha256']}  {k}\n" for k, v in sorted(manifest.items())))
    print(f"{len(manifest)} files in {out}")


def havell_assets(out: Path, dataset: Path, plates_dir: Path) -> None:
    """Every Havell plate as the scan Featherframe fetches (audubon.org's
    file, unaltered) and a crop with the plate's lettering trimmed away:
    the whole engraving inside the number line and the caption, never one
    bird of a sheet."""
    from featherframe.plate_library import _raw_color_crop
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    catalog = {int(c["plate"]): c for c in havell_catalog(plates_dir)}
    out.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for r in read_csv(dataset / "havell" / "plates.csv"):
        n = int(r["plate"])
        sheet, crop = out / r["sheet_asset"], out / r["crop_asset"]
        if not sheet.exists():
            shutil.copyfile(plates_dir / "img" / catalog[n]["fileName"], sheet)
        if not crop.exists():
            _raw_color_crop(sheet, True, None).convert("RGB").save(crop, format="JPEG", quality=92)
        for p in (sheet, crop):
            manifest[p.name] = {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                                "bytes": p.stat().st_size, "plate": n}
    write_manifest(out, "havell", manifest)


THUMB_CELL = (240, 300)          # one plate's cell in a contact sheet, label included
THUMB_COLS, THUMB_PER_SHEET = 10, 50


def thumbs(dataset: Path, assets_dir: Path, folder: str = "gould-europe") -> list[str]:
    """Contact sheets of a folio's crops, 50 plates each, for its README:
    <folder>/img/plates-<first>-<last>.jpg, or plates-<volume>-<first>-<last>.jpg
    for a folio numbered per volume. Returns the files written."""
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(str(SCRIPTS.parent / "featherframe" / "fonts" / "Inter-Medium.otf"), 15)
    per_volume = folder in GOULD and GOULD[folder].per_volume
    rows = [r for r in read_csv(dataset / folder / "plates.csv") if r["crop_asset"]]
    seen, groups = set(), {}
    for r in rows:                      # one cell per plate: its first leaf
        key = (r["volume"], r["plate"]) if per_volume else r["plate"]
        if key not in seen:
            seen.add(key)
            groups.setdefault(r["volume"] if per_volume else "", []).append(r)
    out_dir = dataset / folder / "img"
    out_dir.mkdir(parents=True, exist_ok=True)
    cw, ch = THUMB_CELL
    label_h = 44
    written = []
    for vol, plates in groups.items():
        # A volume's plates split evenly, none over THUMB_PER_SHEET: no sheet of four.
        n = -(-len(plates) // THUMB_PER_SHEET)
        size = -(-len(plates) // n)
        for i in range(0, len(plates), size if vol else THUMB_PER_SHEET):
            group = plates[i:i + (size if vol else THUMB_PER_SHEET)]
            nrows = (len(group) + THUMB_COLS - 1) // THUMB_COLS
            sheet = Image.new("RGB", (THUMB_COLS * cw, nrows * ch), "white")
            draw = ImageDraw.Draw(sheet)
            for j, r in enumerate(group):
                x, y = (j % THUMB_COLS) * cw, (j // THUMB_COLS) * ch
                with Image.open(assets_dir / r["crop_asset"]) as im:
                    im = im.convert("RGB")
                    im.thumbnail((cw - 16, ch - label_h - 12), Image.LANCZOS)
                    sheet.paste(im, (x + (cw - im.width) // 2, y + 8 + (ch - label_h - 12 - im.height) // 2))
                num = f"{vol}.{r['plate']}" if vol else r["plate"]
                name = r.get("caption_name") or r.get("list_name") or r.get("title") or ""
                while draw.textlength(f"{num}. {name}", font=font) > cw - 12 and len(name) > 4:
                    name = name[:-2].rstrip() + "…" if not name.endswith("…") else name[:-2].rstrip() + "…"
                draw.text((x + cw // 2, y + ch - label_h + 10), f"{num}. {name}", font=font,
                          fill=(60, 60, 60), anchor="mt")
            prefix = f"plates-{vol.lower()}-" if vol else "plates-"
            path = out_dir / f"{prefix}{int(group[0]['plate']):03d}-{int(group[-1]['plate']):03d}.jpg"
            sheet.save(path, format="JPEG", quality=82, optimize=True, progressive=True)
            written.append(path.name)
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["export", "check", "assets", "thumbs"])
    ap.add_argument("dir", type=Path, help="the dataset repo (export, check) or the assets folder")
    ap.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "historical-bird-plates")
    ap.add_argument("--offline", action="store_true", help="export without the outside sources")
    ap.add_argument("--dataset", type=Path, help="assets: the dataset repo whose plates.csv to follow")
    ap.add_argument("--plates-dir", type=Path, help="a Featherframe plates dir with sheets already fetched")
    ap.add_argument("--folio", default="gould-europe", choices=[*GOULD, "havell"],
                    help="assets, thumbs: which folio")
    args = ap.parse_args()
    if args.command == "export":
        export(args.dir, Taxonomy(None if args.offline else args.cache), args.plates_dir)
    if args.command in ("export", "check"):
        errors = check(args.dir)
        for e in errors:
            print("  ✗ ", e)
        print("check: ok" if not errors else f"check: {len(errors)} problems")
        return 1 if errors else 0
    if args.command == "thumbs":
        if not args.dataset:
            ap.error("thumbs needs --dataset (and the assets folder as DIR)")
        for name in thumbs(args.dataset, args.dir, args.folio):
            print(name)
    if args.command == "assets":
        if not args.dataset:
            ap.error("assets needs --dataset")
        if args.folio == "havell":
            if not args.plates_dir:
                ap.error("havell assets need --plates-dir (the scans fetch_plates --all cached)")
            havell_assets(args.dir, args.dataset, args.plates_dir)
        else:
            assets(args.dir, args.dataset, args.plates_dir or Path("/nonexistent"),
                   args.dir.parent / (args.dir.name + "-work"), args.folio)
    return 0


if __name__ == "__main__":
    sys.exit(main())
