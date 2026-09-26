"""A species the server's plate index does not carry, drawn from its scan by
the server's own pipeline, so it has the same caption and corner mark as
every other screen (scripts/screens.sh calls this for a scan entry).

    cd <server> && <python> <site>/scripts/scan_screen.py \
        "Green-breasted Mango" "Anthracothorax prevostii" plate-184-mango-hummingbird.jpg 184 ee02

The art is built the way the server's plate provider builds it: the scan's
content-aware crop inside Havell's margins (plate.extract / extract_color), the
Havell plate number for the corner, and the plate's legend lines from
scripts/legends.yaml when it has any. Writes <server>/../test_output/<name>.png,
where preview.py writes its own.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, ".")  # run from the server directory

from featherframe import paths
from featherframe.config import Config
from featherframe.preview import _now
from featherframe.render import pipeline, plate
from featherframe.render.compose import SingleSpec
from featherframe.render.provider import ArtProvider, Artwork


def main() -> int:
    common, latin, scan, number, panel = sys.argv[1:6]
    number = int(number)
    path = paths.plate_images_dir() / scan
    lf = Path("scripts/legends.yaml")
    legends = (yaml.safe_load(lf.read_text()) or {}).get("legends", {}) if lf.exists() else {}
    legend = (legends.get(number) or {}).get("lines") or []

    class ScanProvider(ArtProvider):
        name = "scan"

        def artwork(self, common_name: str, scientific_name: str):
            m = plate.HAVELL_MARGINS
            return Artwork(image=plate.extract(path, margins=m), plate=number, folio="havell",
                           legend=list(legend), color_loader=lambda: plate.extract_color(path, margins=m))

    config = Config()
    config.panel = panel
    config.sanitize()
    config.mat_inset_pct = float(os.environ.get("FF_MAT_INSET", "4"))  # scripts/screens.sh
    spec = SingleSpec(common_name=common, scientific_name=latin, when=_now(), first_seen="2026-05-17")
    result = pipeline.render_single(spec, ScanProvider(), config)
    png, _ = result.save(paths.test_output_dir(), common.lower().replace(" ", "_"))
    print(png)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
