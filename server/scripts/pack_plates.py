#!/usr/bin/env python3
"""Pack the cached Havell plates into release assets (W-764).

Needs the whole edition on disk (`fetch_plates.py --all`). Writes one tar per
hundred plates, data.json, plates-manifest.json and SHA256SUMS to --out; the
output is byte-reproducible, so a re-pack of the same scans changes nothing.

Usage:
    python scripts/pack_plates.py                 # -> ../dist/plates
    python scripts/pack_plates.py --publish       # pack, then upload to the GitHub release

`--publish` needs the `gh` CLI, signed in. It creates the `plates-v1` release
if it isn't there and replaces its assets otherwise.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from featherframe import paths, plate_release  # noqa: E402

NOTES = """The complete Havell edition of Audubon's *Birds of America* (435 plates), packed \
for Featherframe installs. `scripts/fetch_plates.py` restores from these assets and \
verifies each part against `plates-manifest.json`; `SHA256SUMS` is there for checking by hand.

The scans are in the public domain. Courtesy of the John James Audubon Center at Mill \
Grove, Montgomery County Audubon Collection, and Zebra Publishing.
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Pack the Havell plates into release assets.")
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).resolve().parents[2] / "dist" / "plates")
    ap.add_argument("--publish", action="store_true",
                    help=f"upload to the {plate_release.RELEASE_TAG} GitHub release (needs gh)")
    args = ap.parse_args()

    raw = json.loads((paths.plates_dir() / "data.json").read_text())
    catalog = {int(e["plate"]): e for e in raw}
    manifest = plate_release.pack(catalog, paths.plate_images_dir(), args.out)
    for part in manifest["parts"]:
        print(f"  ✓  {part['name']}: {len(part['plates'])} plates, {part['bytes'] / 1e6:.0f} MB")
    print(f"Wrote {args.out}")

    if args.publish:
        tag = plate_release.RELEASE_TAG
        assets = sorted(str(p) for p in args.out.iterdir())
        exists = subprocess.run(["gh", "release", "view", tag], capture_output=True).returncode == 0
        if exists:
            subprocess.run(["gh", "release", "upload", tag, "--clobber", *assets], check=True)
        else:
            subprocess.run(["gh", "release", "create", tag, "--latest=false",
                            "--title", "Plates v1: the Havell edition",
                            "--notes", NOTES, *assets], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
