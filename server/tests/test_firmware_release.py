"""Official firmware releases, offered per frame or automatically (W-838).

The frame says nothing about an update. The server asks GitHub for the latest
official release, verifies its images against the release's manifest, offers
it on each kit's row, and hands it to a frame through /api/firmware only when
the owner pressed Update — or, with automatic updates on, when the frame is
already on an older official release. A dev build is only ever replaced by
the button."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta

import pytest
from starlette.testclient import TestClient

from featherframe import firmware_release as fr
from tests._frames import add_kit

NOW = datetime(2026, 9, 22, 12, 0, 0)
BOARD = "XIAO-ESP32S3 EE03"
OTHER = "XIAO-ESP32S3 EE02"
FID = "AA:AA:AA:00:00:03"
API = "https://example.test/releases/latest"


def _image(board=BOARD, tag=b"1.3.0"):
    return b"\xe9" + b"\0" * 32 + board.encode() + b"\0" + tag + b"\0" * 32


class _Resp:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return json.loads(self.body)

    def iter_content(self, n):
        for i in range(0, len(self.body), n):
            yield self.body[i:i + n]


class FakeGitHub:
    """releases/latest, its manifest, and its images."""

    def __init__(self, version="1.3.0", image=None, manifest_edit=None, signed=None, **release):
        self.files = {}
        self.calls = []
        self.down = False
        img = image if image is not None else _image()
        self.files[f"featherframe-ee03-{version}.bin"] = img
        entry = lambda name: {"name": name, "size": len(self.files[name]),  # noqa: E731
                              "sha256": hashlib.sha256(self.files[name]).hexdigest()}
        app = f"featherframe-ee03-{version}.bin"
        # The manifest describes `signed` (default: the image served).
        good = signed if signed is not None else img
        man = {"version": version, "tag": f"v{version}", "repo": "wr/featherframe",
               "kits": [{"kit": "ee03", "board": BOARD, "chip": "ESP32-S3",
                         "app": {"name": app, "size": len(good),
                                 "sha256": hashlib.sha256(good).hexdigest()},
                         "parts": [{**entry(app), "offset": 0x10000,
                                    "size": len(good), "sha256": hashlib.sha256(good).hexdigest()}]}]}
        if manifest_edit:
            manifest_edit(man)
        self.files["firmware-manifest.json"] = json.dumps(man).encode()
        self.release = {"tag_name": f"v{version}", "draft": False, "prerelease": False,
                        "assets": [{"name": n, "browser_download_url": f"https://dl.test/{n}"}
                                   for n in self.files], **release}

    def get(self, url, timeout=None, headers=None, stream=False):
        self.calls.append(url)
        if self.down:
            raise ConnectionError("offline")
        if url == API:
            return _Resp(json.dumps(self.release).encode())
        name = url.rsplit("/", 1)[-1]
        if name in self.files:
            return _Resp(self.files[name])
        return _Resp(b"", 404)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    monkeypatch.setenv("FEATHERFRAME_RELEASES_URL", API)
    from featherframe.service import FeatherframeService
    svc = FeatherframeService()
    svc._clock = lambda: NOW
    gh = FakeGitHub()
    svc.releases.http = gh
    return svc, gh


def _kit(svc, version="2026.09.20+abc1234", board=BOARD, **settings):
    return add_kit(svc, FID, reported={"fw_version": version, "board": board}, **settings)


def _fw(svc):
    return svc.firmware_view(svc.frames.get(FID))


# -- versions ----------------------------------------------------------------
def test_versions():
    assert fr.parse_version("1.3.0") == (1, 3, 0)
    for dev in ("2026.09.22+abc", "dev", "", None, "1.3", "v1.3.0"):
        assert fr.parse_version(dev) is None
    assert fr.is_newer("1.10.0", "1.9.9")
    assert not fr.is_newer("1.3.0", "1.3.0")
    assert fr.is_newer("1.0.0", "2026.09.22+abc")      # official beats a dev build
    assert not fr.is_newer("2026.09.22+abc", "1.0.0")  # a dev build never is newer


# -- the check ---------------------------------------------------------------
def test_check_keeps_the_manifest_and_asks_daily(env):
    svc, gh = env
    man = svc.releases.check(NOW)
    assert man["version"] == "1.3.0" and svc.releases.version() == "1.3.0"
    n = len(gh.calls)
    svc.releases.check(NOW + timedelta(hours=23))
    assert len(gh.calls) == n                      # not again within the day
    svc.releases.check(NOW + timedelta(hours=25))
    assert len(gh.calls) > n


def test_an_unreachable_github_keeps_the_last_release(env):
    svc, gh = env
    svc.releases.check(NOW)
    gh.down = True
    assert svc.releases.check(NOW, force=True)["version"] == "1.3.0"
    assert svc.firmware_status()["error"]
    assert svc.firmware_status()["latest"] == "1.3.0"


@pytest.mark.parametrize("edit", [
    lambda m: m.update(tag="v9.9.9"),                         # not this release's
    lambda m: m.update(version="2026.09.22"),                  # not an official version
    lambda m: m["kits"][0]["app"].update(sha256="nope"),
    lambda m: m["kits"][0]["app"].update(name="../../etc/passwd"),
])
def test_a_manifest_that_does_not_match_its_release_is_refused(tmp_path, monkeypatch, edit):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_RELEASES_URL", API)
    from featherframe.service import FeatherframeService
    svc = FeatherframeService()
    svc.releases.http = FakeGitHub(manifest_edit=edit)
    assert svc.releases.check(NOW) is None
    assert svc.releases.version() is None


def test_a_prerelease_is_not_official(env):
    svc, _ = env
    svc.releases.http = FakeGitHub(prerelease=True)
    assert svc.releases.check(NOW) is None


def test_a_release_without_firmware_offers_nothing(env):
    svc, gh = env
    gh.release["assets"] = [a for a in gh.release["assets"]
                            if a["name"] != "firmware-manifest.json"]
    assert svc.releases.check(NOW) is None
    assert svc.firmware_status()["error"] is None


# -- images ------------------------------------------------------------------
def test_an_image_is_kept_only_once_verified(env):
    svc, _ = env
    svc.releases.check(NOW)
    path = svc.releases.app_for_board(BOARD, download=True)
    assert path is not None and path.read_bytes() == _image()
    assert svc.releases.app_for_board(BOARD) == path       # cached
    assert svc.releases.app_for_board(OTHER, download=True) is None


@pytest.mark.parametrize("bad,signed", [
    (_image(tag=b"1.3.1"), _image()),            # not what the manifest says
    (b"MZ" + _image()[1:], None),                # what it says, but not an ESP image
    (_image(board=OTHER), None),                 # what it says, but the other board's
])
def test_a_bad_download_is_never_kept(env, bad, signed):
    svc, _ = env
    svc.releases.http = FakeGitHub(image=bad, signed=signed)
    svc.releases.check(NOW)
    assert svc.releases.app_for_board(BOARD, download=True) is None
    assert not list((svc.releases.root).rglob("*.bin"))


# -- the offer ----------------------------------------------------------------
def test_a_frame_on_an_older_build_is_offered_the_release(env):
    svc, _ = env
    svc.releases.check(NOW)
    _kit(svc)
    fw = _fw(svc)
    assert fw["available"] == "1.3.0" and not fw["pending"]
    assert [f["firmware"] for f in svc.frames_list()][0]["available"] == "1.3.0"


@pytest.mark.parametrize("version", ["1.3.0", "1.4.0"])
def test_nothing_is_offered_to_a_frame_on_it_or_newer(env, version):
    svc, _ = env
    svc.releases.check(NOW)
    _kit(svc, version)
    assert _fw(svc)["available"] is None


def test_nothing_is_offered_to_a_board_with_no_build(env):
    svc, _ = env
    svc.releases.check(NOW)
    _kit(svc, board="my-board")
    assert _fw(svc)["available"] is None


def test_auto_updates_only_frames_on_an_official_release(env):
    svc, _ = env
    svc.releases.check(NOW)
    svc.config.firmware_auto_update = True
    _kit(svc, "1.2.0")
    assert _fw(svc)["pending"] and _fw(svc)["auto"]
    _kit(svc, "2026.09.20+abc1234")
    assert not _fw(svc)["pending"]           # a dev build waits for the button


def test_update_is_pressed_then_cleared_once_the_frame_is_on_it(env):
    svc, _ = env
    svc.releases.check(NOW)
    _kit(svc)
    assert svc.update_frame(FID, {"update_firmware": True})
    assert _fw(svc)["pending"]
    svc._tick_firmware()
    assert svc.releases.app_for_board(BOARD) is not None     # fetched ahead of the ask
    row = svc.frames.get(FID)
    row["reported"]["fw_version"] = "1.3.0"
    svc.frames.save(row)
    svc._tick_firmware()
    assert "update_firmware" not in svc.frames.get(FID)["set"]
    assert _fw(svc) == {"running": "1.3.0", "latest": "1.3.0", "available": None,
                        "pending": False, "auto": False}


# -- /api/firmware -------------------------------------------------------------
@pytest.fixture
def client(env):
    from featherframe.app import app
    svc, gh = env
    app.state.service = svc
    return TestClient(app), svc


def _ask(client, md5="0" * 32, fid=FID, board=BOARD):
    return client.get("/api/firmware", headers={"X-Device-Id": fid, "X-Board": board,
                                                "X-Firmware-MD5": md5})


def test_only_the_frame_that_was_updated_is_served_the_release(client):
    c, svc = client
    svc.releases.check(NOW)
    _kit(svc)
    add_kit(svc, "BB:BB:BB:00:00:04", reported={"fw_version": "2026.09.20+abc", "board": BOARD})
    assert _ask(c).status_code == 404                        # not pressed: nothing hosted
    c.post(f"/api/frames/{FID}", json={"update_firmware": True})
    svc._tick_firmware()
    r = _ask(c)
    assert r.status_code == 200 and r.content == _image()
    assert r.headers["X-MD5"] == hashlib.md5(_image()).hexdigest()
    assert _ask(c, fid="BB:BB:BB:00:00:04").status_code == 404
    assert _ask(c, md5=hashlib.md5(_image()).hexdigest()).status_code == 304


def test_nothing_is_served_before_the_image_is_here(client):
    c, svc = client
    svc.releases.check(NOW)
    _kit(svc, update_firmware=True)
    assert _ask(c).status_code == 404        # the tick fetches it; the request never waits


def test_a_dev_image_hosted_before_the_update_does_not_undo_it(client, tmp_path):
    c, svc = client
    svc.releases.check(NOW)
    dev = tmp_path / "data" / "firmware.bin"
    dev.parent.mkdir(parents=True, exist_ok=True)
    dev.write_bytes(_image(tag=b"dev"))
    old = (NOW - timedelta(days=1)).timestamp()
    os.utime(dev, (old, old))
    _kit(svc, update_firmware=True)
    svc._tick_firmware()
    assert _ask(c).content == _image()                       # the release, not the dev image
    row = svc.frames.get(FID)
    row["reported"]["fw_version"] = "1.3.0"
    svc.frames.save(row)
    svc._tick_firmware()
    assert _ask(c).status_code == 304                        # the older dev image stays put
    new = (NOW + timedelta(minutes=5)).timestamp()
    os.utime(dev, (new, new))                                 # `make ota` after the update
    assert _ask(c).content == _image(tag=b"dev")


def test_the_household_switch_is_on_the_settings_form(client):
    c, svc = client
    from featherframe.config import load_config
    svc.reload_config()
    form = {"detection_backend": "custom", "firmware_auto_update": "on"}
    assert c.post("/settings", data=form, follow_redirects=False).status_code == 303
    assert load_config(svc.db).firmware_auto_update is True


def test_the_row_offers_the_release_and_the_household_the_switch(client):
    c, svc = client
    svc.releases.check(NOW)
    _kit(svc)
    add_kit(svc, "BB:BB:BB:00:00:04", reported={"fw_version": "1.3.0", "board": BOARD})
    page = c.get("/").text
    assert page.count("data-fr-fw>") == 1                    # only the frame not on it
    assert "2026.09.20+abc1234 → 1.3.0" in page
    assert 'name="firmware_auto_update"' in page and "Latest release: 1.3.0" in page
    c.post(f"/api/frames/{FID}", json={"update_firmware": True})
    assert "Updates on its next check-in</span>" in c.get("/").text


# -- USB install (W-840) -----------------------------------------------------------
def test_no_release_yet_is_not_an_error(env):
    svc, gh = env
    gh.get = lambda url, **kw: _Resp(b'{"message": "Not Found"}', 404)
    assert svc.releases.check(NOW) is None
    assert svc.firmware_status()["error"] is None


def test_the_flasher_gets_a_kit_without_nvs(client):
    c, svc = client
    assert c.get("/api/flash/ee03/manifest.json").status_code == 404     # no release yet
    svc.releases.check(NOW)
    man = c.get("/api/flash/ee03/manifest.json").json()
    assert man["version"] == "1.3.0" and man["new_install_prompt_erase"] is True
    parts = man["builds"][0]["parts"]
    assert man["builds"][0]["chipFamily"] == "ESP32-S3"
    assert all(not (0x9000 <= p["offset"] < 0xE000) for p in parts)
    r = c.get(f"/api/flash/ee03/{parts[0]['path']}")              # relative to the manifest
    assert r.status_code == 200 and r.content == _image()
    assert c.get("/api/flash/ee03/..%2F..%2Fdb.sqlite").status_code == 404
    assert c.get("/api/flash/ee02/manifest.json").status_code == 404
    assert svc.firmware_status()["kits"] == ["ee03"]


def test_the_page_offers_usb_install(client):
    c, svc = client
    page = c.get("/").text
    assert 'id="usb-open"' in page and "No release to install yet." in page
    svc.releases.check(NOW)
    page = c.get("/").text
    assert 'name="usb-kit" value="ee03"' in page and "10.3″ gray" in page


def test_the_vendored_flasher_is_served(client):
    c, _ = client
    r = c.get("/static/flash/esp-web-tools/install-button.js")
    assert r.status_code == 200 and b"esp-web-install-button" in r.content


def test_the_update_is_read_before_it_is_pressed(client):
    """The row says "Update available" and opens the release's own notes; the
    frame is only marked once Update is pressed in that dialog."""
    c, svc = client
    svc.releases.http.release.update(
        name="Featherframe 1.3.0 (preview)", html_url="https://github.com/wr/featherframe/releases/tag/v1.3.0",
        body="## What's Changed\n* Boot faster by @wr in https://github.com/wr/featherframe/pull/9\n<script>x</script>")
    svc.releases.check(NOW)
    _kit(svc)
    about = svc.releases.about()
    assert about["name"] == "Featherframe 1.3.0 (preview)" and "Boot faster" in about["notes"]
    page = c.get("/").text
    assert ">Update available</button>" in page and ">Update available…</button>" in page
    assert 'id="fw-dlg"' in page and "Featherframe 1.3.0 (preview)</h2>" in page
    assert "<script>x</script>" not in page              # the notes ride as escaped JSON
    assert "update_firmware" not in svc.frames.get(FID)["set"]


def test_no_dialog_without_a_release(client):
    c, _ = client
    assert 'id="fw-dlg"' not in c.get("/").text


def test_an_official_build_links_to_its_release(client):
    c, svc = client
    assert fr.release_url("1.3.0") == "https://github.com/wr/featherframe/releases/tag/v1.3.0"
    assert fr.release_url("2026.09.20+abc1234") is None
    _kit(svc, "1.3.0")
    add_kit(svc, "BB:BB:BB:00:00:04", reported={"fw_version": "2026.09.20+abc", "board": BOARD})
    rows = {f["id"]: f["details"] for f in svc.frames_list()}
    assert rows[FID]["firmware_url"].endswith("/releases/tag/v1.3.0")
    assert rows["BB:BB:BB:00:00:04"]["firmware_url"] == ""
    page = c.get("/").text
    assert page.count('href="https://github.com/wr/featherframe/releases/tag/v1.3.0"') == 1
