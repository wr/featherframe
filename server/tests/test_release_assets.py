"""W-837: the official release's assets. The tool lives with the firmware
(firmware/tools/release_assets.py) and is what the server trusts, so it is
tested here."""
import hashlib
import importlib.util
import json
import os

import pytest

TOOL = os.path.join(os.path.dirname(__file__), "..", "..", "firmware", "tools",
                    "release_assets.py")


@pytest.fixture(scope="module")
def ra():
    spec = importlib.util.spec_from_file_location("release_assets", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _build(tmp_path, name, board):
    d = tmp_path / name
    d.mkdir()
    (d / "firmware.bin").write_bytes(b"\xe9" + b"\0" * 64 + board.encode() + b"\0" * 64)
    (d / "bootloader.bin").write_bytes(b"\xe9boot")
    (d / "partitions.bin").write_bytes(b"\xaa\x50parts")
    return d


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_writes_parts_and_manifest(ra, tmp_path):
    ee03 = _build(tmp_path, "release", "XIAO-ESP32S3 EE03")
    ee02 = _build(tmp_path, "release_ee02", "XIAO-ESP32S3 EE02")
    boot = tmp_path / "boot_app0.bin"
    boot.write_bytes(b"\xff" * 16)
    out = tmp_path / "dist"
    manifest = ra.build("1.3.0", out, {"ee03": ee03, "ee02": ee02}, boot)

    assert manifest["version"] == "1.3.0" and manifest["tag"] == "v1.3.0"
    assert manifest["repo"] == "wr/featherframe"
    assert json.loads((out / "firmware-manifest.json").read_text()) == manifest
    kits = {k["kit"]: k for k in manifest["kits"]}
    assert set(kits) == {"ee03", "ee02"}

    k = kits["ee03"]
    assert k["board"] == "XIAO-ESP32S3 EE03" and k["chip"] == "ESP32-S3"
    app = out / "featherframe-ee03-1.3.0.bin"
    assert k["app"] == {"name": app.name, "size": app.stat().st_size, "sha256": _sha(app)}
    offsets = [(p["name"], p["offset"]) for p in k["parts"]]
    assert offsets == [("featherframe-ee03-1.3.0-bootloader.bin", 0x0),
                       ("featherframe-ee03-1.3.0-partitions.bin", 0x8000),
                       ("featherframe-ee03-1.3.0-boot_app0.bin", 0xE000),
                       ("featherframe-ee03-1.3.0.bin", 0x10000)]
    for p in k["parts"]:
        assert p["sha256"] == _sha(out / p["name"])
        # Never NVS (0x9000-0xdfff): the board keeps its Wi-Fi.
        assert not (0x9000 <= p["offset"] < 0xE000)

    web = json.loads((out / "manifests" / "ee03.json").read_text())
    assert web["version"] == "1.3.0" and web["new_install_prompt_erase"] is True
    assert web["builds"] == [{"chipFamily": "ESP32-S3", "parts": [
        {"path": f"../{name}", "offset": off} for name, off in offsets]}]


def test_refuses_the_other_boards_image(ra, tmp_path):
    wrong = _build(tmp_path, "release", "XIAO-ESP32S3 EE02")
    boot = tmp_path / "boot_app0.bin"
    boot.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="EE03"):
        ra.build("1.3.0", tmp_path / "dist", {"ee03": wrong}, boot)


def test_refuses_a_non_esp_image(ra, tmp_path):
    d = _build(tmp_path, "release", "XIAO-ESP32S3 EE03")
    (d / "firmware.bin").write_bytes(b"MZ XIAO-ESP32S3 EE03")
    boot = tmp_path / "boot_app0.bin"
    boot.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="ESP image"):
        ra.build("1.3.0", tmp_path / "dist", {"ee03": d}, boot)


def test_refuses_a_dev_version(ra, tmp_path):
    d = _build(tmp_path, "release", "XIAO-ESP32S3 EE03")
    boot = tmp_path / "boot_app0.bin"
    boot.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="version"):
        ra.build("2026.09.22+abc1234", tmp_path / "dist", {"ee03": d}, boot)
