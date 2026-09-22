# Firmware updates and USB flashing — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use `- [ ]`.

**Goal:** official releases reach frames from the page (per frame or automatic), and a new/wiped board becomes a frame over USB.
**Architecture:** CI publishes per-kit parts + `firmware-manifest.json` on a `v*` tag; the server checks the latest release, verifies and caches images, and gates `/api/firmware` per frame; the page flashes over Web Serial (esp-web-tools) with Improv for Wi-Fi.
**Tech stack:** GitHub Actions, PlatformIO (pioarduino), FastAPI, requests, esp-web-tools, Improv Serial v1.
**Spec:** `docs/superpowers/specs/2026-09-22-firmware-updates-design.md`

## Global constraints

- Kits: `ee03` → board `XIAO-ESP32S3 EE03` (env `release`); `ee02` → `XIAO-ESP32S3 EE02` (env `release_ee02`). Chip `ESP32-S3`.
- USB parts, never NVS: bootloader @ 0x0, partitions @ 0x8000, boot_app0 @ 0xe000, app @ 0x10000. `firmware.factory.bin` is NOT shipped (it pads NVS with 0xFF).
- Official version = `MAJOR.MINOR.PATCH`; anything else is a dev build and is never auto-replaced.
- Python 3.9 floor; server soft-fails every network step.
- The frame shows nothing about updates. Copy: terse, plain nouns.
- Dev servers run with `FEATHERFRAME_NO_MDNS=1`.

## Task 1 (W-837): release assets + workflow

**Files:** create `firmware/tools/release_assets.py`, `.github/workflows/release.yml`, `server/tests/test_release_assets.py`.

**Produces:** `release_assets.py --version V --out DIR --kit ee03=<build dir> --kit ee02=<build dir> --boot-app0 PATH` writes
`DIR/featherframe-<kit>-<V>.bin` (app), `DIR/featherframe-<kit>-<V>-{bootloader,partitions,boot_app0}.bin`,
`DIR/firmware-manifest.json` (spec §1 shape: `version`, `tag`, `repo`, `kits[]` with `kit`, `board`, `chip`, `app{name,size,sha256}`, `parts[{name,offset,size,sha256}]`),
and `DIR/manifests/<kit>.json` (esp-web-tools: `name`, `version`, `new_install_prompt_erase: true`, `builds: [{chipFamily: "ESP32-S3", parts: [{path, offset}]}]`, paths relative `../<file>`).
Refuses an app image without 0xE9 magic or without the kit's board string.

- [ ] Test: fake build dirs (0xE9 + board string), run `build()`; assert file names, SHA-256s, offsets, esp-web-tools manifest; assert wrong board string raises.
- [ ] Implement; tests pass.
- [ ] Workflow: on `push: tags: v*` and on PRs touching it: matrix-free job builds `release` + `release_ee02`, runs `release_assets.py`; on a tag only, checks the tag equals `v<FF_FW_VERSION>`, `gh release create` with the assets, and deploys Pages (`flasher/` + the assets) via `actions/upload-pages-artifact` + `actions/deploy-pages`.
- [ ] Commit, PR.

## Task 2 (W-838): server release check + per-frame gating + row UI

**Files:** create `server/featherframe/firmware_release.py`, `server/tests/test_firmware_release.py`; modify `config.py` (`firmware_auto_update: bool = False`), `service.py` (daily check in `tick`, `firmware_view(row)`, `update_frame` takes `update_firmware`, `frame_view["firmware"]`, `firmware_for_frame(frame_id, board)`), `app.py` (`/api/firmware` gate, settings form field), `templates/index.html` (row line + Update button, household switch).

**Produces:**
- `firmware_release.parse_version(s) -> Optional[tuple]`; `is_newer(a, b) -> bool`.
- `ReleaseStore(db, data_dir, fetch=requests)`: `.check(now, force=False) -> Optional[dict]` (manifest), `.manifest() -> Optional[dict]`, `.build_for(board) -> Optional[dict]`, `.image_for(board) -> Optional[Path]` (download + verify, cached).
- `service.firmware_view(row) -> dict`: `{"running", "latest", "available", "pending", "auto"}`.
- `/api/firmware`: pending + verified image → serve it; else existing path.

- [ ] Tests (mocked fetch): parse/compare; check stores manifest, soft-fails; image_for rejects SHA mismatch/bad magic/wrong board; firmware_view states; auto rule skips dev builds; pending clears when reported version matches; `/api/firmware` per-frame gating; `POST /api/frames/<id>` `{"update_firmware": true}`.
- [ ] Implement; full suite passes.
- [ ] Page: row line + button; household switch. Verify in browser pane.
- [ ] Commit, PR, deploy.

## Task 3 (W-839): Improv Serial in firmware

**Files:** create `firmware/src/ff_improv.h` / `ff_improv.cpp`; modify `firmware/src/main.cpp` (portal non-blocking + `improvPoll()`; provisioned answer after join).

- [ ] Improv v1 framing (`IMPROV` header, version 1, type, length, data, checksum = sum of bytes), RPC commands 0x01 WIFI_SETTINGS, 0x02 GET_CURRENT_STATE, 0x03 GET_DEVICE_INFO, 0x04 GET_WIFI_NETWORKS; states 0x02 READY, 0x03 PROVISIONING, 0x04 PROVISIONED; errors 0x01 invalid RPC, 0x02 unknown RPC, 0x03 unable to connect.
- [ ] Portal: `wm.setConfigPortalBlocking(false)`; loop `wm.process(); improvPoll();` until connected or timeout; also answer Improv for a short window after a normal join so a no-erase flash is reported PROVISIONED.
- [ ] `pio run -e release` and `-e release_ee02` build; bench test when a kit is free.
- [ ] Commit, PR.

## Task 4 (W-840): USB flasher

**Files:** create `flasher/index.html`, `server/static/flash/` (vendored esp-web-tools `install-button.js` + chunks, pinned), `server/static/flash/flasher.js`; modify `app.py` (`/api/flash/{kit}/manifest.json`, `/api/flash/{kit}/{name}`), `firmware_release.py` (`part_for(kit, name)`), `templates/index.html` (*Add a frame over USB* dialog).

- [ ] Tests: manifest endpoint 404 without a release, serves parts with right offsets once downloaded, rejects unknown names.
- [ ] Dialog: kit choice → `<esp-web-install-button manifest=...>` when `window.isSecureContext && 'serial' in navigator`; else a link to `https://wr.github.io/featherframe/flash/?kit=<kit>`.
- [ ] Pages flasher `flasher/index.html` uses the same `flasher.js`.
- [ ] Verify dialog in browser pane; commit, PR.
