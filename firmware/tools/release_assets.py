#!/usr/bin/env python3
"""The assets of an official firmware release (W-837).

For each kit: the OTA app image the server hands a frame, and the parts a USB
install writes — bootloader @ 0x0, partitions @ 0x8000, boot_app0 @ 0xe000
(otadata: boot the first app slot), the app @ 0x10000. They ship as separate
parts, never PlatformIO's merged `firmware.factory.bin`: that pads NVS
(0x9000–0xdfff) with 0xFF, and a board flashed with it would lose its Wi-Fi.

`firmware-manifest.json` is what a server trusts: the version, each kit's
board string, and every file's size and SHA-256. `manifests/<kit>.json`
is the same kit for esp-web-tools (the Pages flasher).

    release_assets.py --version 1.3.0 --out dist \\
        --kit ee03=firmware/.pio/build/release \\
        --kit ee02=firmware/.pio/build/release_ee02 \\
        --boot-app0 ~/.platformio/packages/framework-arduinoespressif32/tools/partitions/boot_app0.bin
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

REPO = "wr/featherframe"
CHIP = "ESP32-S3"

# kit -> the board string its build carries (FF_BOARD_ID, ff_config.h), which
# is also what a frame sends as X-Board.
BOARDS = {
    "ee03": "XIAO-ESP32S3 EE03",
    "ee02": "XIAO-ESP32S3 EE02",
}

# (part, source file in the build dir or None for boot_app0, offset)
PARTS = (
    ("bootloader", "bootloader.bin", 0x0),
    ("partitions", "partitions.bin", 0x8000),
    ("boot_app0", None, 0xE000),
    ("app", "firmware.bin", 0x10000),
)

_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _entry(path: Path, **extra) -> dict:
    return {"name": path.name, **extra, "size": path.stat().st_size, "sha256": _sha256(path)}


def _check_app(app: Path, kit: str) -> None:
    data = app.read_bytes()
    if not data.startswith(b"\xe9"):
        raise ValueError(f"{app} is not an ESP image (bad magic)")
    if BOARDS[kit].encode() not in data:
        raise ValueError(f"{app} is not a {BOARDS[kit]} build")


def build(version: str, out: Path, kits: dict, boot_app0: Path) -> dict:
    """Write every asset for `kits` ({kit: build dir}) into `out`; return the
    manifest."""
    if not _VERSION.match(version):
        raise ValueError(f"an official version is MAJOR.MINOR.PATCH, not {version!r}")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    entries = []
    for kit, build_dir in kits.items():
        if kit not in BOARDS:
            raise ValueError(f"unknown kit {kit!r}")
        build_dir = Path(build_dir)
        _check_app(build_dir / "firmware.bin", kit)
        parts = []
        app_entry = None
        for part, src, offset in PARTS:
            source = Path(boot_app0) if src is None else build_dir / src
            name = (f"featherframe-{kit}-{version}.bin" if part == "app"
                    else f"featherframe-{kit}-{version}-{part}.bin")
            dest = out / name
            shutil.copyfile(source, dest)
            parts.append({"name": name, "offset": offset,
                          "size": dest.stat().st_size, "sha256": _sha256(dest)})
            if part == "app":
                app_entry = _entry(dest)
        entries.append({"kit": kit, "board": BOARDS[kit], "chip": CHIP,
                        "app": app_entry, "parts": parts})
        web = out / "manifests"
        web.mkdir(parents=True, exist_ok=True)
        (web / f"{kit}.json").write_text(json.dumps({
            "name": f"Featherframe ({kit.upper()})",
            "version": version,
            "new_install_prompt_erase": True,
            "builds": [{"chipFamily": CHIP, "parts": [
                {"path": f"../{p['name']}", "offset": p["offset"]} for p in parts]}],
        }, indent=2) + "\n")
    manifest = {"version": version, "tag": f"v{version}", "repo": REPO, "kits": entries}
    (out / "firmware-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--kit", action="append", required=True, metavar="KIT=BUILD_DIR")
    ap.add_argument("--boot-app0", required=True, type=Path)
    args = ap.parse_args()
    kits = dict(k.split("=", 1) for k in args.kit)
    manifest = build(args.version, args.out, kits, args.boot_app0)
    for k in manifest["kits"]:
        print(f"{k['kit']}: {k['app']['name']} {k['app']['size']} bytes {k['app']['sha256'][:12]}")


if __name__ == "__main__":
    main()
