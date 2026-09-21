"""End-to-end preview with no hardware and no birds.

    python -m featherframe.preview                     # fake Northern Cardinal
    python -m featherframe.preview --species "Blue Jay"
    python -m featherframe.preview --fallback          # typographic fallback plate
    python -m featherframe.preview --all               # one PNG per curated species
    python -m featherframe.preview --note "Nothing heard since 11:27 pm"  # gone-quiet footnote
    python -m featherframe.preview --first-ever        # "first recorded today" under the Latin name

Writes a PNG (exactly what the panel will show) and the packed .fff framebuffer
to test_output/, so compositions can be reviewed by eye. `make preview` calls
this.
"""
from __future__ import annotations

import argparse
from datetime import datetime

from . import paths
from .config import Config
from .names import SpeciesIndex, normalize
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
    ap.add_argument("--dither", default=None, choices=["bluenoise", "stucki", "none"],
                    help="bench override; the server always uses the panel's own dither")
    ap.add_argument("--panel", default=None, metavar="KEY",
                    help="ee03 = 10.3\" gray (default), ee02 = 13.3\" Spectra 6 colour, or a "
                         "reported panel as custom:WxH:format:rotations "
                         "(e.g. custom:800x480:gray16:90,270)")
    ap.add_argument("--mat-inset", type=float, default=None,
                    help="override mat inset %% per edge (0 = full-bleed, no mat allowance)")
    ap.add_argument("--note", default=None, metavar="TEXT",
                    help='footnote in the bottom margin, e.g. "Nothing heard since 11:27 pm"')
    ap.add_argument("--views", action="store_true",
                    help="also write the plate as viewers get it (W-822): a TRMNL X, "
                         "a Kobo Clara, a Kindle on its side, a TRMNL OG, a tablet")
    ap.add_argument("--first-ever", action="store_true",
                    help='render as a species never heard before today ("first recorded today")')
    args = ap.parse_args()

    config = Config()
    if args.dither:
        pipeline.DITHER_OVERRIDE = args.dither
    if args.panel:
        config.panel = args.panel
        config.sanitize()
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
        counts = [37, 24, 19, 12, 8, 5] + [3] * 60
        cells = [collage_mod.CollageCell(sp["common"], sp["scientific"], counts[i])
                 for i, sp in enumerate(data[:max(2, args.collage)])]
        img = collage_mod.render_collage(cells, provider, when=date(2026, 5, 17),
                                         total_detections=sum(c.count for c in cells),
                                         note=args.note, color=config.panel_spec.color)
        result = pipeline.render_image(img, config, "collage", f"{len(cells)} species")
        png, fff = result.save(out, f"collage_{len(cells)}")
        print(f"Rendered collage ({result.levels} levels, etag {result.etag})")
        print(f"  PNG: {png}")
        return 0

    if args.all:
        data = _load_index_species()
        for sp in data:
            spec = SingleSpec(common_name=sp["common"], scientific_name=sp["scientific"],
                              when=_now(), first_seen="2026-05-17",
                              note=args.note)
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
                          when=_now(), first_seen="2026-05-17",
                          note=args.note)
        # Force fallback by not matching: render_fallback directly.
        from .render import compose
        img = compose.render_fallback(spec, color=config.panel_spec.color)
        result = pipeline.render_image(img, config, "fallback", common)
    else:
        sci = args.scientific or _guess_scientific(index, args.species)
        spec = SingleSpec(common_name=args.species, scientific_name=sci, when=_now(),
                          first_seen="2026-05-17",
                          note=args.note, first_ever=args.first_ever)
        result = pipeline.render_single(spec, provider, config)

    name = (args.species if not args.fallback else "fallback").lower().replace(" ", "_")
    png, fff = result.save(out, name)
    print(f"Rendered {result.label} ({result.mode}, {result.levels} levels, "
          f"etag {result.etag})")
    print(f"  PNG:   {png}")
    print(f"  Frame: {fff}  ({len(result.frame):,} bytes)")
    if args.views:
        # A gray frame's server composes the colour twin for its colour viewers.
        from .render import compose
        in_color = result.sheet if result.sheet.mode == "RGB" else (
            compose.render_fallback(spec, color=True) if args.fallback
            else compose.render_single(spec, provider, color=True))
        for label, view in _VIEWS:
            target = out / f"{name}_view_{label}.png"
            sheet = in_color if view.fmt == "color" else result.sheet
            pipeline.render_view(sheet, view).save(target)
            print(f"  View:  {target}  ({view.key})")
    return 0


# The screens `--views` draws for: one per kind of viewer.
_VIEWS = (("trmnl_x", pipeline.View(1404, 1872, "gray16")),
          ("kobo_clara", pipeline.View(1072, 1448, "gray256")),
          ("kindle_pw", pipeline.View(1448, 1072, "gray256", rotation=90)),
          ("trmnl_og", pipeline.View(800, 480, "gray2")),
          ("tablet", pipeline.View(1536, 2048, "color")))


def _load_index_species() -> list[dict]:
    import json
    idx = json.loads(paths.plate_index_path().read_text())
    return [s for s in idx.get("species", []) if s.get("plate") not in (None, "none")]


def _guess_scientific(index: SpeciesIndex, common: str) -> str:
    entry = index._by_common.get(normalize(common))  # noqa: SLF001 (internal ok here)
    return entry.get("scientific", "") if entry else ""


if __name__ == "__main__":
    raise SystemExit(main())
