# Firmware updates and USB flashing from the dashboard (W-836)

Two owner-facing firmware paths: an official release reaches a frame from the
page, and a new or wiped board becomes a frame from the page over USB.

## 1. Official releases (W-837)

`.github/workflows/release.yml`, on a pushed `v*` tag (and, without publishing,
on PRs that touch the workflow or `firmware/tools/release_assets.py`):

- builds `release` (EE03) and `release_ee02` on a clean checkout of the tag, so
  each reports `FF_FW_VERSION` = the tag without its `v`;
- per kit, keeps the OTA app image (`featherframe-<kit>-<ver>.bin`) and the
  parts a USB install writes: bootloader @ 0x0, partitions @ 0x8000,
  boot_app0 @ 0xe000, the same app @ 0x10000. They ship as separate parts, not
  one merged image, because a merged image would overwrite NVS (0x9000–0xdfff)
  with 0xFF padding and lose the board's Wi-Fi (see §4);
- writes `firmware-manifest.json`:

  ```json
  {"version": "1.3.0", "tag": "v1.3.0", "repo": "wr/featherframe",
   "kits": [{"kit": "ee03", "board": "XIAO-ESP32S3 EE03", "chip": "ESP32-S3",
             "app": {"name": "...", "size": 0, "sha256": "..."},
             "parts": [{"name": "...", "offset": 0, "size": 0, "sha256": "..."}]}]}
  ```
- creates the GitHub release (`gh release create`), not a draft or prerelease;
- deploys `flasher/` plus the parts and a per-kit esp-web-tools manifest to
  GitHub Pages (`https://wr.github.io/featherframe/flash/`).

"Official" = the latest non-draft, non-prerelease release of wr/featherframe
carrying a `firmware-manifest.json`, whose images match its SHA-256s. Nothing
else is ever offered.

## 2. Cloud update (W-838)

`featherframe/firmware_release.py`:

- `check(now)`: at most daily (and on demand), GET
  `api.github.com/repos/wr/featherframe/releases/latest`, fetch the manifest
  asset, keep it in the kv store (`firmware_release`). Soft-fails; a failed
  check keeps the last manifest.
- `image_for(board)`: the verified app image for a board, downloaded on first
  need into `data/firmware/<version>/<kit>.bin` — SHA-256 matches, 0xE9 magic,
  the board string inside. A bad download is deleted and not served.
- `compare(frame_version, release_version)`: official versions are
  `MAJOR.MINOR.PATCH`; anything else (`2026.09.22+abc`, `dev`) is a dev build.

Per frame (`frames_list()` → `firmware` block):

- `available`: the release's version when the frame's board has a build and the
  frame is not already on it (and is not on a *newer* official version);
- `pending`: `set.update_to` — the owner pressed Update; cleared when the frame
  reports that version (or the release moves on);
- auto: household `Config.firmware_auto_update`. A frame on an older *official*
  version is treated as pending. A dev build is never replaced automatically.

`/api/firmware`: resolve the frame from `X-Device-Id`. If it is `on` and pending
(pressed or auto) and the release has a verified image for its board, serve that
image (same `X-MD5` + stream contract as today). Otherwise the existing
dev-hosted path (`make ota`), unchanged. The frame shows nothing about it.

Page: the row's Details shows *Firmware* as today; when `available`, the row
opens onto *Firmware 1.3.0 available* with an **Update** button
(`POST /api/frames/<id>` `{"update_firmware": true}`), and while pending
*Updates on its next check-in*. Household form, a *Firmware* line under the
Frames list is not a setting; the switch *Install updates automatically* sits
in the household form as its own small section.

## 3. Improv over USB (W-839)

First boot today blocks in WiFiManager's portal. It becomes non-blocking
(`setConfigPortalBlocking(false)` + `wm.process()`), and an Improv Serial v1
handler reads the USB CDC port in the same loop:

- `GET_CURRENT_STATE` → `PROVISIONED` once joined, else `READY`;
- `GET_DEVICE_INFO` → `Featherframe`, `FF_FW_VERSION`, `ESP32-S3`, board;
- `GET_WIFI_NETWORKS` → a scan;
- `WIFI_SETTINGS` → `WiFi.begin(ssid, pass)` (persisted as WiFiManager's are),
  on success reply with the server URL if known (else none), then restart.

A board flashed without erase keeps NVS, rejoins by itself and answers
`PROVISIONED`, so esp-web-tools skips its Wi-Fi form.

## 4. USB flasher (W-840)

esp-web-tools, vendored under `server/static/flash/` (pinned, no CDN at
runtime). Both kits are ESP32-S3, so the kit is chosen by the owner, not the
chip. Frames card: *Add a frame over USB* → dialog: kit (10.3″ gray, 13.3″
colour) → Install. Its manifest's `parts` are bootloader, partitions,
boot_app0, app at their offsets — never NVS — and
`new_install_prompt_erase: true` offers *Erase everything* for a truly clean
start.

Served from the server when it has the release: `/api/flash/<kit>/manifest.json`
and `/api/flash/<kit>/<part>`. Web Serial needs a secure context: on HTTPS
(or localhost) the dialog flashes in place; otherwise it links to the Pages
flasher for the same kit. The flashed frame is approved like any other
(*wants to connect*).

## Testing

pytest: manifest parse + SHA/board verification (mocked GitHub), version
compare, auto rule, pending lifecycle, `/api/firmware` gating per frame, flash
manifest endpoints, the row's firmware block. CI: the release workflow builds
on PRs without publishing. Firmware: build in CI; Improv on a bench kit.
