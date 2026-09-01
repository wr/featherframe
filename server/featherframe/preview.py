"""End-to-end preview with no hardware and no birds.

    python -m featherframe.preview                     # fake Northern Cardinal
    python -m featherframe.preview --species "Blue Jay"
    python -m featherframe.preview --fallback          # typographic fallback plate
    python -m featherframe.preview --all               # one PNG per curated species

Writes a PNG (exactly what the panel will show) and the packed .fff framebuffer
to test_output/, so compositions can be reviewed by eye. `make preview` calls
this.
"""
from __future__ import annotations

import argparse
from datetime import datetime

from . import paths
from .config import Config
from .names import SpeciesIndex
from .render import pipeline
from .render.compose import SingleSpec
from .render.genart import GeneratedArtProvider
from .render.provider import AudubonProvider, ChainedProvider


def _now() -> datetime:
    # A fixed, pleasant timestamp keeps previews reproducible.
    return datetime(2026, 5, 17, 8, 14)


def main() -> int:
    ap = argparse.ArgumentParser(description="Render a Featherframe preview.")
    ap.add_argument("--species", default="Northern Cardinal")
    ap.add_argument("--scientific", default=None)
    ap.add_argument("--fallback", action="store_true",
                    help="force the typographic fallback (unknown species)")
    ap.add_argument("--all", action="store_true", help="render every curated species")
    ap.add_argument("--collage", type=int, default=0, metavar="N",
                    help="render a daily collage of N species (2-6)")
    ap.add_argument("--dither", default=None, choices=["bluenoise", "stucki", "none"])
    ap.add_argument("--gray", default=None, choices=["16", "1"])
    ap.add_argument("--mat-inset", type=float, default=None,
                    help="override mat inset %% per edge (0 = full-bleed, no mat allowance)")
    ap.add_argument("--plate-number", type=int, default=42)
    args = ap.parse_args()

    config = Config()
    if args.dither:
        config.dither = args.dither
    if args.gray:
        config.gray_mode = args.gray
    if args.mat_inset is not None:
        config.mat_inset_pct = args.mat_inset

    index = SpeciesIndex.load()
    # Cache-only generated link (no API key in previews): a species with a
    # cached AI plate previews exactly as the server would render it.
    provider = ChainedProvider([AudubonProvider(index), GeneratedArtProvider(None)])
    out = paths.test_output_dir()

    if args.collage:
        from datetime import date
        from .render import collage as collage_mod
        data = _load_index_species()
        counts = [37, 24, 19, 12, 8, 5]
        cells = [collage_mod.CollageCell(sp["common"], sp["scientific"], counts[i])
                 for i, sp in enumerate(data[:max(2, min(args.collage, 6))])]
        img = collage_mod.render_collage(cells, provider, when=date(2026, 5, 17),
                                         total_detections=sum(c.count for c in cells))
        result = pipeline.render_image(img, config, "collage", f"{len(cells)} species")
        png, fff = result.save(out, f"collage_{len(cells)}")
        print(f"Rendered collage ({result.levels} levels, etag {result.etag})")
        print(f"  PNG: {png}")
        return 0

    if args.all:
        data = _load_index_species()
        for i, sp in enumerate(data, start=1):
            spec = SingleSpec(common_name=sp["common"], scientific_name=sp["scientific"],
                              when=_now(), plate_number=i, first_seen="2026-05-17")
            result = pipeline.render_single(spec, provider, config)
            name = sp["common"].lower().replace(" ", "_")
            png, _ = result.save(out, name)
            print(f"  {sp['common']:26} -> {png.name}")
        print(f"\nWrote {len(data)} previews to {out}")
        return 0

    if args.fallback:
        common = args.species if args.species != "Northern Cardinal" else "Painted Bunting"
        spec = SingleSpec(common_name=common,
                          scientific_name=args.scientific or "Passerina ciris",
                          when=_now(), plate_number=args.plate_number, first_seen="2026-05-17")
        # Force fallback by not matching: render_fallback directly.
        from .render import compose
        img = compose.render_fallback(spec, show_plate_number=True)
        result = pipeline.render_image(img, config, "fallback", common)
    else:
        sci = args.scientific or _guess_scientific(index, args.species)
        spec = SingleSpec(common_name=args.species, scientific_name=sci, when=_now(),
                          plate_number=args.plate_number, first_seen="2026-05-17")
        result = pipeline.render_single(spec, provider, config)

    name = (args.species if not args.fallback else "fallback").lower().replace(" ", "_")
    png, fff = result.save(out, name)
    print(f"Rendered {result.label} ({result.mode}, {result.levels} levels, "
          f"etag {result.etag})")
    print(f"  PNG:   {png}")
    print(f"  Frame: {fff}  ({len(result.frame):,} bytes)")
    return 0


def _load_index_species() -> list[dict]:
    import json
    idx = json.loads(paths.plate_index_path().read_text())
    return [s for s in idx.get("species", []) if s.get("plate") not in (None, "none")]


def _guess_scientific(index: SpeciesIndex, common: str) -> str:
    entry = index._by_common.get(common.strip().lower())  # noqa: SLF001 (internal ok here)
    return entry.get("scientific", "") if entry else ""


if __name__ == "__main__":
    raise SystemExit(main())
