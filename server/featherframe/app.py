"""FastAPI app: the device endpoint and the LAN config page.

No auth (LAN-only — see the README). No SPA, no build step: one server-rendered
page and a handful of endpoints.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import math
import os
import re
import shutil
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, File, Form, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

from . import __version__, discovery, hosted, panels, paths, viewers
from . import frames as frames_mod
from .config import Config, valid_hhmm
from .names import display_common_name, normalize
from .render import pipeline, typography
from .service import FeatherframeService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("featherframe.app")

templates = Jinja2Templates(directory=str(paths.templates_dir()))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # A hosted household's server (W-844) starts from the household's own data
    # dir, pulled before the database is opened. A pull that fails stops the
    # start: an empty server would push a fresh install over the household.
    link = None
    conf = hosted.config_from_env()
    if conf:
        link = hosted.HostedLink(*conf)
        await run_in_threadpool(link.pull)
    service = FeatherframeService()
    app.state.service = service
    app.state.hosted = link
    if link is not None:
        service.after_tick.append(lambda: link.settle(service))
    service.start()
    # Advertise _featherframe._tcp so a frame with no typed URL finds us
    # (W-763). __main__ exports the bound port; systemd sets it directly.
    advertiser = discovery.Advertiser(
        port=int(os.environ.get("FEATHERFRAME_PORT", "8080")), version=__version__,
        panel=service.mdns_panel())
    app.state.advertiser = advertiser
    await run_in_threadpool(advertiser.start)
    try:
        yield
    finally:
        advertiser.stop()
        service.stop()
        if link is not None:
            # A hosted server is stopped straight after its work (W-847):
            # whatever the last moment changed reaches the front door first.
            await run_in_threadpool(link.settle, service, False)


app = FastAPI(title="Featherframe", version=__version__, lifespan=lifespan)


@app.middleware("http")
async def _hosted_settle(request: Request, call_next):
    """On a hosted household's server, a request that changed something (a
    save, an answer) reaches the front door at once rather than on the next
    tick: the frames it answers see it now."""
    response = await call_next(request)
    link = getattr(request.app.state, "hosted", None)
    if link is not None and request.method in ("POST", "PUT", "DELETE") and response.status_code < 400:
        await run_in_threadpool(link.settle, request.app.state.service, False)
    return response
if paths.static_dir().exists():
    app.mount("/static", StaticFiles(directory=str(paths.static_dir())), name="static")


def _svc(request: Request) -> FeatherframeService:
    return request.app.state.service


def _strip_etag(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return value.strip().strip('"').removeprefix("W/").strip('"')


def _same_origin(request: Request) -> bool:
    """True unless the request carries a foreign Origin header. There is no
    auth on the LAN page, but state-changing POSTs (some of which spend the
    user's API credit) must at least not be triggerable cross-site by any web
    page the user happens to visit. Non-browser clients send no Origin.
    "null" is foreign: sandboxed iframes and file:// pages send exactly that,
    and a hostile page can wrap its POST in a sandboxed iframe."""
    origin = request.headers.get("origin")
    if not origin:
        return True
    if origin == "null":
        return False
    host = request.headers.get("host", "")
    return origin.split("://", 1)[-1].split("/", 1)[0] == host


def _forbidden_cross_origin() -> JSONResponse:
    return JSONResponse({"error": "cross-origin request refused"}, status_code=403)


# -- device endpoint -------------------------------------------------------
def parse_checkin(headers) -> dict:
    """What a kit says about itself on GET /api/frame, from its headers: the
    one reading of them, whether the request reached this server or was
    queued by a hosted household's front door (W-843) and handed over later.
    Telemetry is untrusted input: "nan"/"inf" parse as floats but a NaN
    persisted into device_status makes every /api/status 500 (JSON can't
    carry it), and int(float("inf")) raises. Only plausible, finite values are
    kept; anything else reads as "not reported"."""
    volt = _ranged_float(headers.get("x-battery-voltage"), 0.0, 6.0)
    pct = _ranged_int(headers.get("x-battery-percent"), 0, 100)
    rssi = _ranged_int(headers.get("x-wifi-rssi"), -120, 0)
    wake = _str_header(headers.get("x-wake"))

    # Optional device-reported identity/telemetry (docs/firmware-device-stats.md).
    # Every field is optional on the wire; absent headers leave the row unchanged.
    device_extra = {
        "fw_version": _str_header(headers.get("x-ff-version")),
        "sketch_md5": _str_header(headers.get("x-ff-sketch-md5")),
        "last_wake": wake,
        "wake_detail": _str_header(headers.get("x-wake-detail")),
        "boot_count": _ranged_int(headers.get("x-boot-count"), 0, 2**31),
        "refresh_count": _ranged_int(headers.get("x-refresh-count"), 0, 2**31),
        "panel": _str_header(headers.get("x-panel")),
        "board": _str_header(headers.get("x-board")),
        "push_s": _ranged_int(headers.get("x-ff-push"), 0, 86400),
    }

    # A frame is a frame (W-833): every kit that is on is served the same way,
    # its own picture finished for its own panel, with its own settings on the
    # way out. Any other is parked until the owner answers on the page (403;
    # the firmware shows "Add this frame on the Featherframe page" and keeps
    # asking). The frame describes its panel as facts too (W-813), so a panel
    # this server has never heard of is still drawn for at its own size.
    panel_facts = {"w": _str_header(headers.get("x-panel-width")),
                   "h": _str_header(headers.get("x-panel-height")),
                   "fmt": _str_header(headers.get("x-panel-format")),
                   "rot": _str_header(headers.get("x-panel-rotations"))}
    return {"device_id": _str_header(headers.get("x-device-id")),
            "volt": volt, "pct": pct, "rssi": rssi, "wake": wake,
            "device_extra": device_extra, "panel_facts": panel_facts}


@app.get("/api/frame")
async def api_frame(request: Request, view: Optional[str] = None):
    svc = _svc(request)
    inm = _strip_etag(request.headers.get("if-none-match"))
    c = parse_checkin(request.headers)
    volt, pct, rssi, wake = c["volt"], c["pct"], c["rssi"], c["wake"]
    device_extra, panel_facts = c["device_extra"], c["panel_facts"]
    client_ip = request.client.host if request.client else None
    if wake:
        # An always-awake frame polls every few seconds; the per-poll line is
        # debug, and a paint (200) or a button view is logged below at info.
        log.debug("device wake: %s (view=%s)", wake, view)

    status = svc.admit_frame(_str_header(request.headers.get("x-device-id")),
                             device_extra["panel"], device_extra["board"], client_ip,
                             facts=panel_facts)
    frame_id = (_str_header(request.headers.get("x-device-id")) or "")[:40] or svc.LEGACY_FRAME
    if status != "on":
        headers = {"Cache-Control": "no-store",
                   "X-FF-Frame": "pending" if status == "asking" else "ignored"}
        # If another instance on the LAN draws for this frame's panel, say so:
        # the frame moves there instead of waiting here to be added.
        adv = getattr(request.app.state, "advertiser", None)
        reported = panels.from_report(device_extra["panel"], panel_facts)
        if adv is not None and reported is not None and reported.key != svc.mdns_panel():
            peer = await run_in_threadpool(adv.find_peer, reported.key)
            if peer:
                headers["X-FF-Server"] = peer
        return Response(status_code=403, content=b"this frame has not been added here",
                        headers=headers)

    row = svc.frames.get(frame_id)
    cfg = svc.frame_config(row)
    poll_s, wake_min = svc.frame_intervals(row)
    # The power model, wake interval and rotation ride along on every response —
    # a 304 included (W-456/W-736): the device stores them in NVS, so the page
    # is the one place any of them is set.
    device_headers = {# Dark mode is gone (W-821), but fielded firmware keeps the
                      # last X-FF-Invert it heard in NVS and only updates it
                      # when the header is present: say "0" until every frame
                      # runs firmware that no longer asks.
                      "X-FF-Invert": "0",
                      # Which way up the frame hangs: the firmware turns its
                      # baked boot screens and pills to match the plates.
                      "X-FF-Rotation": str(cfg.panel_rotation),
                      "X-Power-Mode": cfg.power_mode,
                      # On the collage both follow the collage's next redraw.
                      "X-Wake-Minutes": str(wake_min),
                      "X-Poll-Seconds": str(poll_s)}
    telemetry = {**device_extra, "battery_voltage": volt, "battery_percent": pct,
                 "wifi_rssi": rssi, "ip": client_ip,
                 "user_agent": request.headers.get("user-agent", "") or None}

    # On-demand button views: rendered fresh for this frame's panel, never a
    # picture, no 304s. Threadpool: the collage leg walks the provider chain
    # (which may generate art over the network) and a blocking render here
    # would stall every endpoint on the loop.
    if view in ("collage", "status"):
        if view == "collage":
            result = await run_in_threadpool(svc.render_collage_on_demand, cfg)
            if result is None:
                return Response(status_code=404, content=b"not enough birds for a collage")
        else:
            result = await run_in_threadpool(svc.render_status_page, volt, pct, rssi, cfg)
        svc.record_view_checkin(frame_id, view, telemetry)
        log.info("device view: %s (wake=%s)", view, wake)
        return Response(content=result.frame, media_type="application/octet-stream",
                        headers={"ETag": f'"{result.etag}"', "Cache-Control": "no-store",
                                 **device_headers})

    status, body, etag = await run_in_threadpool(svc.get_frame, frame_id, inm, telemetry)
    if status == 503:
        return Response(status_code=503, content=b"no frame yet", headers=device_headers)
    headers = {"ETag": f'"{etag}"', "Cache-Control": "no-cache", **device_headers}
    if status == 304:
        return Response(status_code=304, headers=headers)
    log.info("frame %s fetched %s (wake=%s)", frame_id[-6:], etag, wake)
    return Response(content=body, media_type="application/octet-stream", headers=headers)


# How often a push socket re-checks its frame's message with nothing having
# woken it: a safety net under the tick's own notify, not the push itself.
PUSH_RECHECK_S = 30


async def _until_closed(ws: WebSocket) -> None:
    while (await ws.receive()).get("type") != "websocket.disconnect":
        pass


@app.websocket("/api/frame/push")
async def api_frame_push(ws: WebSocket):
    """Push, don't poll (W-841). A kit on USB holds this socket with the same
    identity headers it sends to /api/frame. It is told its message
    (`service.push_message`) at once and again whenever that changes, and
    answers each one with its usual GET /api/frame. Nothing else crosses: a
    frame that is not on is refused, and keeps polling to be let in."""
    svc = ws.app.state.service
    origin = ws.headers.get("origin")
    if origin and origin.split("://", 1)[-1].split("/", 1)[0] != ws.headers.get("host", ""):
        await ws.close(code=1008)
        return
    fid = (_str_header(ws.headers.get("x-device-id")) or "")[:40]
    msg = await run_in_threadpool(svc.push_message, fid) if fid else None
    if msg is None:
        await ws.close(code=1008)
        return
    await ws.accept()
    event = svc.push.register(fid)
    log.info("frame %s on push", fid[-6:])
    sent = None
    # The frame sends nothing; one read held open the whole time is how a
    # closed socket is noticed (uvicorn's own pings close a dead one).
    reader = asyncio.ensure_future(_until_closed(ws))
    try:
        while msg is not None:
            if msg != sent:
                await ws.send_json(msg)
                sent = msg
            waker = asyncio.ensure_future(event.wait())
            await asyncio.wait({waker, reader}, timeout=PUSH_RECHECK_S,
                               return_when=asyncio.FIRST_COMPLETED)
            waker.cancel()
            if reader.done():
                break
            event.clear()     # before the read, so a wake during it is kept
            msg = await run_in_threadpool(svc.push_message, fid)
        if msg is None:
            await ws.close(code=1008)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if reader.done() and not reader.cancelled():
            reader.exception()    # a disconnect, seen: nothing to report
        reader.cancel()
        svc.push.unregister(fid, event)
        log.info("frame %s off push", fid[-6:])


@app.post("/api/hosted/run")
async def api_hosted_run(request: Request):
    """A hosted household's wake (W-844): one tick, synced, answered when it is
    done — the Container sleeps soon after, so the work has to happen inside
    the request that woke it. Only a hosted server has it."""
    link = getattr(request.app.state, "hosted", None)
    if link is None:
        return JSONResponse({"error": "not hosted"}, status_code=404)
    svc = _svc(request)
    await run_in_threadpool(svc.tick)          # the tick's own hook settles it
    return JSONResponse({"ok": True, "next_wake_at": svc.next_wake_at()})


def _announce_panel(request: Request, svc) -> None:
    adv = getattr(request.app.state, "advertiser", None)
    if adv is not None:
        adv.set_panel(svc.mdns_panel())


# -- firmware OTA ----------------------------------------------------------
# The device offers its running sketch MD5 on every wake. If data/firmware.bin
# exists and differs, it gets the new build; otherwise 304. Deploy = drop a new
# firmware.bin in the data dir (`make ota` does build + copy).
@app.get("/api/firmware")
async def api_firmware(request: Request):
    svc = _svc(request)
    board_hdr = _str_header(request.headers.get("x-board"))
    frame_id = _str_header(request.headers.get("x-device-id"))
    # An official release the owner asked this frame to take (W-838) comes
    # first; otherwise a dev image hosted by `make ota`, unless this frame was
    # handed a release after that image was put there.
    bin_path = svc.release_image_for(frame_id, board_hdr)
    if bin_path is None:
        bin_path = _firmware_for(board_hdr)
        if bin_path is not None and svc.dev_image_superseded(frame_id, bin_path.stat().st_mtime):
            return Response(status_code=304)
    if bin_path is None:
        return Response(status_code=404, content=b"no firmware hosted")
    md5 = _hosted_firmware_md5(bin_path)
    if md5 is None:
        return Response(status_code=404, content=b"no firmware hosted")
    if request.headers.get("x-firmware-md5", "").lower() == md5:
        return Response(status_code=304)
    # A frame names its board (X-Board) and every build carries that string, so
    # an image built for the other board is never handed over: an EE03 image
    # on an EE02 boots, joins Wi-Fi, passes the rollback bar — and drives the
    # wrong panel until someone reflashes it over USB.
    board = (request.headers.get("x-board") or "").strip()
    if board and not _firmware_is_for(bin_path, board):
        log.warning("hosted firmware.bin is not a %r build: not serving it to %s",
                    board, request.client.host if request.client else "?")
        return Response(status_code=404, content=b"hosted firmware is for another board")
    log.info("serving firmware.bin (%d bytes, md5=%s) to %s",
             bin_path.stat().st_size, md5, request.headers.get("user-agent", "?"))
    return FileResponse(bin_path, media_type="application/octet-stream",
                        headers={"X-MD5": md5, "Cache-Control": "no-store"})


@app.post("/api/firmware/check")
async def api_firmware_check(request: Request):
    """Ask GitHub for the latest official release now (the page's Check now)."""
    if not _same_origin(request):
        return _forbidden_cross_origin()
    return JSONResponse(await run_in_threadpool(_svc(request).check_firmware))


# -- USB install (W-840) ---------------------------------------------------
# The page's "Add a frame over USB" flashes the latest official release with
# esp-web-tools (vendored in static/flash/). Web Serial needs a secure page,
# so on plain http the page sends the owner to the same flasher on GitHub
# Pages instead; these serve the in-page one.
@app.get("/api/flash/{kit}/manifest.json")
async def api_flash_manifest(request: Request, kit: str):
    man = _svc(request).releases.flash_manifest(kit)
    if man is None:
        return JSONResponse({"error": "no release for this kit"}, status_code=404)
    return JSONResponse(man, headers={"Cache-Control": "no-store"})


@app.get("/api/flash/{kit}/{name}")
async def api_flash_part(request: Request, kit: str, name: str):
    path = await run_in_threadpool(_svc(request).releases.part, kit, name)
    if path is None:
        return Response(status_code=404, content=b"no such part")
    return FileResponse(path, media_type="application/octet-stream",
                        headers={"Cache-Control": "no-store"})


def _firmware_for(board: Optional[str]):
    """The hosted image for the board that asks. One server may feed two kinds
    of kit: `firmware.bin` and any `firmware-*.bin` in the data dir are
    candidates, and the one that carries the board's own string wins. A frame
    that names no board gets `firmware.bin`, as before."""
    data = paths.data_dir()
    main = data / "firmware.bin"
    candidates = ([main] if main.exists() else []) + sorted(data.glob("firmware-*.bin"))
    if not board:
        return main if main.exists() else None
    for path in candidates:
        if _firmware_is_for(path, board):
            return path
    return main if main.exists() else None   # the board check below refuses it, and says why


_FW_CACHE: dict = {}   # (mtime_ns, size) -> md5
_FW_BOARD_CACHE: dict = {}   # (mtime_ns, size, board) -> bool


def _firmware_is_for(bin_path, board: str) -> bool:
    try:
        st = bin_path.stat()
        key = (str(bin_path), st.st_mtime_ns, st.st_size, board)
        if key not in _FW_BOARD_CACHE:
            if len(_FW_BOARD_CACHE) > 8:
                _FW_BOARD_CACHE.clear()
            _FW_BOARD_CACHE[key] = board.encode("ascii", "ignore") in bin_path.read_bytes()
        return _FW_BOARD_CACHE[key]
    except OSError:
        return False


def _hosted_firmware_md5(bin_path) -> Optional[str]:
    """Digest of the hosted image, recomputed only when the file changes: the
    device asks on every wake and hashing 1.5 MB each time on a Pi Zero is
    wasteful (the bytes themselves are streamed from disk, never pinned in
    memory). Only a file with a valid ESP image header (0xE9 magic) is
    hosted, so a stray file dropped in data/ is not pushed to the frame."""
    try:
        st = bin_path.stat()
    except OSError:
        return None
    key = (str(bin_path), st.st_mtime_ns, st.st_size)
    md5 = _FW_CACHE.get(key)
    if md5 is None:
        h = hashlib.md5()
        with open(bin_path, "rb") as fh:
            first = fh.read(1)
            if first != b"\xe9":
                log.warning("data/firmware.bin is not an ESP image (bad magic); not hosting it")
                return None
            h.update(first)
            for chunk in iter(lambda: fh.read(64 * 1024), b""):
                h.update(chunk)
        md5 = h.hexdigest()
        if len(_FW_CACHE) > 8:
            _FW_CACHE.clear()
        _FW_CACHE[key] = md5
    return md5


# Browsers ask for /favicon.ico regardless of the page's <link>s; a 404 in
# the log on every visit is noise, so serve the ICO from the static dir.
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    ico = paths.static_dir() / "favicon.ico"
    if not ico.is_file():
        return Response(status_code=404)
    return FileResponse(ico, media_type="image/x-icon",
                        headers={"Cache-Control": "max-age=86400"})


# The dashboard's wordmark is set in the plates' script; serve the bundled
# face so the page and the frame share one file. If it is ever missing the
# CSS falls back to Garamond italic, exactly as the plates themselves do.
@app.get("/fonts/script.ttf", include_in_schema=False)
async def script_font():
    if not typography.has_script_font():
        return Response(status_code=404)
    return FileResponse(typography.script_font_path(), media_type="font/ttf",
                        headers={"Cache-Control": "max-age=86400"})


# -- config page -----------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    svc = _svc(request)
    # Threadpool: status() probes the detection source (a 5 s-timeout HTTP
    # call for BirdNET-Go/BirdWeather) and the listing reads the SD card;
    # blocking the loop here would stall the device's /api/frame fetch.
    status = await run_in_threadpool(svc.status)
    generated = await run_in_threadpool(svc.generated_listing) if svc.genart else []
    history = await run_in_threadpool(svc.render_history)
    collage_days = await run_in_threadpool(svc.collage_days)
    # `config` is the household's and only the household's (W-833): what a
    # frame is drawn with lives on that frame's own row, and the Frames card
    # is the only place any of it is set.
    return templates.TemplateResponse(
        request, "index.html",
        {"status": status, "config": svc.config, "version": __version__,
         "generated": generated, "history": history, "collage_days": collage_days,
         "fw_about": {**svc.releases.about(), "version": svc.releases.version()}})


@app.post("/settings")
async def save_settings(request: Request):
    if not _same_origin(request):
        return _forbidden_cross_origin()
    svc = _svc(request)
    try:
        form = await request.form()
    except Exception:  # noqa: BLE001 — a malformed body must land on the page, not a 500
        return RedirectResponse("/?error=" + quote("Could not read the form."), status_code=303)
    # The household's settings and nothing else (W-833). A frame's own —
    # what it shows, its rotation, its mat, its power — are saved per frame,
    # through POST /api/frames/<id>, and are not fields on this form.
    cur = svc.config.to_dict()

    # A multipart file part under a text field's name comes back as an
    # UploadFile, which Config can't sanitize or serialise: only real strings
    # are accepted, anything else keeps the stored value.
    def s(key, default):
        v = form.get(key)
        return v if isinstance(v, str) else default
    def i(key, default): return _to_int(s(key, None), default)
    def b(key): return key in form  # checkbox present -> true
    # An empty limit means no limit, which is stored as 0.
    def limit(key, default):
        raw = s(key, None)
        return 0 if (raw is not None and not raw.strip()) else _to_int(raw, default)
    # A clock time that isn't one ("99:99") keeps the stored value rather
    # than being coerced to some other time or reset to the default.
    def t(key, default): return s(key, default) if valid_hhmm(s(key, None)) else default

    blocklist_raw = s("species_blocklist", "")
    blocklist = [x.strip() for x in blocklist_raw.replace(",", "\n").splitlines() if x.strip()]

    new = Config(
        # Kept as they were. Nothing reads the household's copy of a frame's
        # settings — `frames.frame_config` is where a frame's come from — but
        # `Config` still has the fields, and a rollback would read them.
        **{k: cur[k] for k in
           ("mode", "panel", "panel_rotation", "mat_inset_pct", "mat_offset_x_px",
            "mat_offset_y_px", "mat_guide", "power_mode", "wake_interval_minutes",
            "device_poll_seconds")},
        quiet_hours_mode=s("quiet_hours_mode", cur["quiet_hours_mode"]),
        quiet_hours_start=t("quiet_hours_start", cur["quiet_hours_start"]),
        quiet_hours_end=t("quiet_hours_end", cur["quiet_hours_end"]),
        species_blocklist=blocklist,
        detection_backend=s("detection_backend", cur["detection_backend"]),
        birdnet_db_path=s("birdnet_db_path", cur["birdnet_db_path"]),
        birdnet_go_url=s("birdnet_go_url", cur["birdnet_go_url"]),
        birdweather_station_id=s("birdweather_station_id", cur["birdweather_station_id"]),
        apprise_token=s("apprise_token", cur["apprise_token"]),
        collage_interval_hours=i("collage_interval_hours", cur["collage_interval_hours"]),
        imagegen_enabled=b("imagegen_enabled"),
        collage_generated=b("collage_generated"),
        firmware_auto_update=b("firmware_auto_update"),
        collage_species_max=limit("collage_species_max", cur["collage_species_max"]),
        imagegen_provider=s("imagegen_provider", cur["imagegen_provider"]),
        imagegen_model=s("imagegen_model", cur["imagegen_model"]),
        imagegen_base_url=s("imagegen_base_url", cur["imagegen_base_url"]),
        imagegen_text_model=s("imagegen_text_model", cur["imagegen_text_model"]),
        imagegen_text_provider=s("imagegen_text_provider", cur["imagegen_text_provider"]),
        imagegen_text_base_url=s("imagegen_text_base_url", cur["imagegen_text_base_url"]),
        imagegen_quality=s("imagegen_quality", cur["imagegen_quality"]),
        # A typed key always wins; blank means "keep the stored key" unless the
        # matching clear checkbox is ticked.
        imagegen_api_key=(s("imagegen_api_key", "").strip()
                          or ("" if b("imagegen_clear_key") else cur["imagegen_api_key"])),
        imagegen_text_key=(s("imagegen_text_key", "").strip()
                           or ("" if b("imagegen_text_clear_key") else cur["imagegen_text_key"])),
    )
    try:
        svc.update_config(new)
        _announce_panel(request, svc)
    except Exception as exc:  # noqa: BLE001 — surface it on the page, keep the old config
        log.exception("saving settings failed")
        return RedirectResponse("/?error=" + quote(f"Settings were not saved: {exc}"),
                                status_code=303)
    # Config.sanitize() clamps silently; tell the page which fields it changed
    # so the user isn't left staring at a different number than they typed.
    adjusted = _adjusted_fields(form, svc.config)
    return RedirectResponse("/?saved=1" + ("&adjusted=" + ",".join(adjusted) if adjusted else ""),
                            status_code=303)


_NUMERIC_FORM_FIELDS = ("collage_interval_hours", "collage_species_max")


def _adjusted_fields(form, cfg: Config) -> list[str]:
    """Form fields whose submitted number differs from what sanitize() kept."""
    out = []
    saved = cfg.to_dict()
    for key in _NUMERIC_FORM_FIELDS:
        raw = form.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        want = _to_float(raw, None)
        if want is None:
            out.append(key)
            continue
        try:
            got = float(saved[key])
        except (KeyError, TypeError, ValueError):
            continue
        if abs(want - got) > 1e-9:
            out.append(key)
    return out


@app.post("/api/test-detection")
async def test_detection(request: Request):
    if not _same_origin(request):
        return _forbidden_cross_origin()
    svc = _svc(request)
    form = await request.form()
    # Names become a caption and a cache filename; keep them a sane length.
    common = str(form.get("common", "") or "").strip()[:200] or "Northern Cardinal"
    # The typed name becomes the caption: the index's spelling if it knows
    # the species, else title-cased like a field-guide entry (W-711).
    common = svc.audubon.index.canonical_common(common) or display_common_name(common)
    sci = str(form.get("scientific", "") or "").strip()[:200]
    if sci:
        sci = sci[:1].upper() + sci[1:].lower()   # Genus species
    if not sci:
        sci = _known_scientific(svc, common) or ("Cardinalis cardinalis"
                                                 if common == "Northern Cardinal" else "")
    # Fire-and-forget: a plate-less species may generate art (up to ~2 min),
    # which would 504 a synchronous request. The job runs on a service worker
    # thread and the page polls /api/tasks; this returns immediately.
    svc.start_test_detection(common, sci)
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/", status_code=303)
    return JSONResponse({"ok": True, "running": True})


def _known_scientific(svc, common: str) -> Optional[str]:
    """Best-effort common -> scientific, so a test detection caches under the
    same slug a real detection of that species will use. Tries the curated
    index (normalized: hyphens and apostrophes must not break the lookup),
    then the detection source's own species list."""
    entry = svc.audubon.index._by_common.get(normalize(common))  # noqa: SLF001
    if entry and entry.get("scientific"):
        return entry["scientific"]
    summary = getattr(svc.source, "_species_summary", None)
    if callable(summary):
        want = normalize(common)
        for row in summary():
            if normalize(str(row.get("common_name", ""))) == want:
                return row.get("scientific_name")
    return None


@app.post("/api/collage/now")
async def collage_now(request: Request):
    if not _same_origin(request):
        return _forbidden_cross_origin()
    svc = _svc(request)
    form = await request.form()
    repaint = "repaint" in form
    # Fire-and-forget: a fresh sheet is a ~1-2 minute generation. Same contract
    # as test-detection — run it on a worker thread and let the page poll.
    svc.start_collage(repaint)
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/", status_code=303)
    return JSONResponse({"ok": True, "running": True})


@app.post("/api/refresh")
async def api_refresh(request: Request):
    """Re-render the frame that should be showing right now (per config), so its
    bytes (and ETag) are rebuilt — and so it recovers from a stale held collage.
    The panel is deep-asleep and can't be pushed to: it picks up the result on
    its next scheduled wake, and only redraws if the content actually changed
    (identical pixels hash to the same ETag → a 304, no wasteful refresh)."""
    if not _same_origin(request):
        return _forbidden_cross_origin()
    svc = _svc(request)
    # Threadpool: this is a full render (and possibly a multi-minute art
    # generation) — on the loop it would block every other endpoint,
    # including the device's own frame fetch.
    before = svc.current_etag()
    await run_in_threadpool(svc.refresh_now)
    cur = svc.current_info()
    return JSONResponse({"ok": True, "etag": cur["etag"], "changed": cur["etag"] != before,
                         "rendered_at": cur["rendered_at"]})


@app.post("/api/frames")
async def api_frames(request: Request):
    """The owner's answer about a kit that is not on yet: `action=add` (draw
    for it too), `ignore` (park it), `forget` (drop it, so it asks again).
    There is no "replace": there is no current frame to replace (W-833)."""
    if not _same_origin(request):
        return _forbidden_cross_origin()
    svc = _svc(request)
    form = await request.form()
    frame_id = str(form.get("id", "") or "")[:40]
    action = str(form.get("action", "") or "")
    ok = await run_in_threadpool(svc.answer_frame, frame_id, action)
    if not ok:
        return JSONResponse({"ok": False, "error": "unknown frame or action"}, status_code=400)
    svc.push.notify(frame_id)
    _announce_panel(request, svc)
    return JSONResponse({"ok": True, "frames": svc.frames_list()})


@app.post("/api/frames/{frame_id}")
async def api_frame_settings(request: Request, frame_id: str):
    """One frame's own settings, whatever it is fed over: its name, what it
    shows, which way up it hangs, its mat, its power, how it is drawn.
    Everything else is the household's. Only what this screen HAS can be set
    (`frames.capabilities`) — a page has no panel rotation — and unknown keys
    are ignored. `{"forget": true}` removes it."""
    if not _same_origin(request):
        return _forbidden_cross_origin()
    svc = _svc(request)
    fid = frame_id[:40]
    try:
        fields = await request.json()
    except ValueError:
        fields = None
    if not isinstance(fields, dict):
        return JSONResponse({"error": "a JSON object is required"}, status_code=400)
    if fields.get("forget"):
        ok = await run_in_threadpool(svc.forget_frame, fid)
        if not ok:
            return JSONResponse({"error": "no such frame"}, status_code=404)
        svc.push.notify(fid)
        _announce_panel(request, svc)
        return JSONResponse({"ok": True, "frames": svc.frames_list()})
    try:
        ok = await run_in_threadpool(svc.update_frame, fid, fields)
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": f"not saved: {exc}"[:200]}, status_code=400)
    if not ok:
        return JSONResponse({"error": "no such frame"}, status_code=404)
    svc.push.notify(fid)
    return JSONResponse({"ok": True, "frames": svc.frames_list()})


@app.get("/api/frames/{frame_id}/preview.png")
async def api_frame_preview(request: Request, frame_id: str):
    """What THIS frame is showing, upright and filling the preview box: a kit's
    own output, a viewer's own view drawn the way it draws — but never the
    device's canvas shape or its rotation. A tablet's window is usually
    landscape and a TRMNL hangs on its side; letterboxing the portrait sheet
    inside either only shrinks the plate on the page."""
    svc = _svc(request)
    row = svc.frames.get(frame_id[:40])
    if row is None:
        return Response(status_code=404, content=b"no such frame")
    if frames_mod.transport_of(row) == "kit":
        png = await run_in_threadpool(svc.frame_png_bytes, str(row["id"]))
        if png is None:
            return Response(status_code=404, content=b"nothing drawn for it yet")
        return Response(content=png, media_type="image/png",
                        headers={"Cache-Control": "no-cache"})
    if row.get("status") != frames_mod.ON:
        # Not added yet: what is actually on its glass, and no picture is
        # drawn (or coloured) for a screen nobody has answered for.
        _, png, etag = await run_in_threadpool(svc.waiting_png, _upright(row),
                                               svc.frame_short(str(row["id"])))
        return Response(content=png, media_type="image/png",
                        headers={"ETag": f'"{etag}"', "Cache-Control": "no-cache"})
    # Threadpool: the first ask for a variant dithers a whole sheet.
    status, png, etag = await run_in_threadpool(svc.view_png, _upright(row), None,
                                                viewers.shows_of(row))
    if status != 200 or png is None:
        return Response(status_code=404, content=b"no frame yet")
    return Response(content=png, media_type="image/png",
                    headers={"ETag": f'"{etag}"', "Cache-Control": "no-cache"})


# The 3:4 sheet a page frame's preview is drawn at: a browser window has no
# shape of its own worth previewing.
_PAGE_PREVIEW = (1200, 1600)


def _upright(row: dict) -> pipeline.View:
    """One viewer's view as the page previews it: its own depth, the picture
    the right way up, nothing turned."""
    if frames_mod.transport_of(row) == "page":
        return pipeline.View(*_PAGE_PREVIEW, "color", 0)
    view = viewers.view_of(row)
    w, h = (view.height, view.width) if view.rotation in (90, 270) else (view.width, view.height)
    return pipeline.View(w, h, view.fmt, 0)


# -- hold this plate / block what's showing (W-735) --------------------------


@app.post("/api/hold")
async def api_hold(request: Request):
    """Pin the current plate for a day, a week, or until released."""
    if not _same_origin(request):
        return _forbidden_cross_origin()
    svc = _svc(request)
    form = await request.form()
    duration = str(form.get("duration", "day") or "day")
    hold = svc.hold_current(duration)
    if hold is None:
        return JSONResponse({"ok": False, "error": "Nothing on the wall to hold yet."},
                            status_code=409)
    return JSONResponse({"ok": True, "hold": svc.hold_view()})


@app.post("/api/hold/release")
async def api_hold_release(request: Request):
    if not _same_origin(request):
        return _forbidden_cross_origin()
    svc = _svc(request)
    before = svc.current_etag()
    await run_in_threadpool(svc.release_hold)      # repaints: a render, off the loop
    cur = svc.current_info()
    return JSONResponse({"ok": True, "etag": cur["etag"], "changed": cur["etag"] != before})


@app.post("/api/block-current")
async def api_block_current(request: Request):
    """Blocklist the species on the glass and move past it."""
    if not _same_origin(request):
        return _forbidden_cross_origin()
    svc = _svc(request)
    before = svc.current_etag()
    name = await run_in_threadpool(svc.block_current)   # refreshes: a render
    if name is None:
        return JSONResponse({"ok": False, "error": "No single plate is showing."},
                            status_code=409)
    cur = svc.current_info()
    return JSONResponse({"ok": True, "blocked": name, "etag": cur["etag"],
                         "changed": cur["etag"] != before})


@app.post("/api/unblock")
async def api_unblock(request: Request):
    """Undo for block-current: take one name off the blocklist."""
    if not _same_origin(request):
        return _forbidden_cross_origin()
    svc = _svc(request)
    form = await request.form()
    name = str(form.get("name", "") or "").strip()[:200]
    if not name:
        return JSONResponse({"ok": False, "error": "name required"}, status_code=400)
    return JSONResponse({"ok": True, "removed": svc.unblock(name)})


# -- push ingest (BirdNET-Pi via Apprise) ----------------------------------
def _apprise_detection(payload) -> dict:
    """Pull the detection object out of an Apprise envelope. Apprise posts
    {version, title, message, type} with our JSON body in `message`; BirdNET-Pi
    may append text after it, so extract the {...} span rather than parse whole.
    Falls back to a top-level object if someone posts the fields directly."""
    if not isinstance(payload, dict):
        return {}
    msg = payload.get("message")
    if isinstance(msg, str) and "{" in msg and "}" in msg:
        try:
            obj = json.loads(msg[msg.index("{"): msg.rindex("}") + 1])
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
    return payload


@app.post("/api/ingest/apprise")
@app.post("/api/ingest/apprise/{token}")
async def ingest_apprise(request: Request, token: str = ""):
    """Webhook for the Apprise (BirdNET-Pi push) source. Point Apprise at
    json://<host>/api/ingest/apprise[/<token>] with a JSON detection body."""
    # Same-origin guard like every other state-changing POST: a detection can
    # trigger a render (and a paid generation), so a hostile web page must not
    # be able to inject one cross-site. Apprise sends no Origin, so it passes.
    if not _same_origin(request):
        return _forbidden_cross_origin()
    svc = _svc(request)
    expected = getattr(svc.config, "apprise_token", "")
    # compare_digest refuses non-ASCII str; compare bytes so an accented
    # secret is a 403 on mismatch, never a 500 on every push.
    if expected and not hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
        return JSONResponse({"error": "bad token"}, status_code=403)
    ingest = getattr(svc.source, "ingest", None)
    if not callable(ingest):
        return JSONResponse({"error": "detection source is not Apprise"}, status_code=409)
    # A detection is a few hundred bytes; don't buffer an arbitrary body into
    # a Pi Zero's memory (the queue is persisted, so bloat would be too).
    # Enforced on the stream, not the header: a chunked request carries no
    # Content-Length and would otherwise be buffered whole.
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > _MAX_INGEST_BYTES:
            return JSONResponse({"error": "body too large"}, status_code=413)
    try:
        payload = json.loads(bytes(body).decode("utf-8")) if body else {}
    except (ValueError, UnicodeDecodeError):
        payload = {}
    det = await run_in_threadpool(ingest, _apprise_detection(payload))
    return JSONResponse({"ok": det is not None})


# -- AI-generated plates ---------------------------------------------------
def _valid_slug(slug: str) -> bool:
    return bool(slug) and slug.replace("-", "").isalnum()


@app.get("/api/generated")
async def generated_list(request: Request):
    svc = _svc(request)
    # Each entry carries regenerating/regen_error; the top-level list is what
    # the page's poller checks to decide whether to keep polling.
    cached = svc.generated_listing()
    return JSONResponse({"cached": cached,
                         "regenerating": [m["slug"] for m in cached
                                          if m.get("regenerating")]})


@app.get("/api/generated/{slug}.png")
async def generated_png(request: Request, slug: str):
    svc = _svc(request)
    if not _valid_slug(slug):
        return Response(status_code=404)
    png = svc.genart._png(slug)  # noqa: SLF001 (same package, path is validated)
    if not png.exists():
        return Response(status_code=404)
    # FileResponse streams and stamps Last-Modified; a short max-age keeps the
    # gallery from re-downloading megabytes of PNG on every page view.
    return FileResponse(png, media_type="image/png",
                        headers={"Cache-Control": "max-age=300"})


@app.post("/api/generated/regenerate")
async def generated_regenerate(request: Request, slug: str = Form(...)):
    if not _same_origin(request):
        return _forbidden_cross_origin()
    svc = _svc(request)
    # Fire-and-forget: the generation runs in a service worker thread and the
    # page polls /api/generated for the outcome, so this returns immediately.
    # Threadpool only for the small cache-listing read (SD cards stall).
    ok, error = False, "Not a valid plate name."
    if _valid_slug(slug):
        listing = {m.get("slug"): m for m in await run_in_threadpool(svc.generated_listing)}
        if slug not in listing:
            error = "No cached plate by that name."
        elif listing[slug].get("regenerating"):
            error = "Already regenerating."
        elif not svc.config.imagegen_enabled:
            error = "Image generation is off — enable it first."
        else:
            ok = await run_in_threadpool(svc.start_regenerate, slug)
            error = None if ok else "Could not start — is this plate still on file?"
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/", status_code=303)
    return JSONResponse({"ok": ok, "error": error})


@app.post("/api/generated/delete")
async def generated_delete(request: Request, slug: str = Form(...)):
    if not _same_origin(request):
        return _forbidden_cross_origin()
    svc = _svc(request)
    ok, error = False, "Not a valid plate name."
    if _valid_slug(slug):
        listing = {m.get("slug"): m for m in await run_in_threadpool(svc.generated_listing)}
        if listing.get(slug, {}).get("regenerating"):
            error = "Still regenerating — try again when it finishes."
        else:
            ok = await run_in_threadpool(svc.delete_generated, slug)
            error = None if ok else "No cached plate by that name."
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/", status_code=303)
    return JSONResponse({"ok": ok, "error": error})


@app.get("/api/generated/export")
async def generated_export(request: Request):
    """The generated-plate cache as one zip (W-765): each plate cost an image,
    and data/generated/ otherwise lives only on this box."""
    svc = _svc(request)
    if not svc.genart:
        return Response(status_code=404)
    # Built on disk, not in memory: a full gallery is hundreds of MB and the
    # target is a 512 MB Pi. The temp zip goes once the response has streamed.
    fd, tmp = tempfile.mkstemp(prefix=".generated-", suffix=".zip.tmp", dir=paths.data_dir())
    os.close(fd)
    try:
        count = await run_in_threadpool(svc.export_generated, Path(tmp))
    except Exception:
        os.unlink(tmp)
        raise
    if not count:
        os.unlink(tmp)
        return Response(status_code=404)
    name = f"featherframe-generated-plates-{datetime.now():%Y-%m-%d}.zip"
    return FileResponse(tmp, media_type="application/zip", filename=name,
                        background=BackgroundTask(os.unlink, tmp))


def _import_upload(svc: FeatherframeService, backup: UploadFile) -> dict:
    """Copy the upload to a real file, then import from that. Starlette hands
    over a SpooledTemporaryFile, which has no seekable() before Python 3.11 —
    zipfile can't open it there, and 3.9 is the floor. On disk, not in memory:
    a full gallery is hundreds of MB."""
    with tempfile.TemporaryFile(prefix=".restore-", dir=paths.data_dir()) as tmp:
        shutil.copyfileobj(backup.file, tmp, 1 << 20)
        tmp.seek(0)
        return svc.import_generated(tmp)


@app.post("/api/generated/import")
async def generated_import(request: Request, backup: UploadFile = File(...)):
    if not _same_origin(request):
        return _forbidden_cross_origin()
    svc = _svc(request)
    result = {"restored": 0, "kept": 0, "skipped": 0}
    error = None
    if not svc.genart:
        error = "Generated plates are not available on this install."
    else:
        try:
            result = await run_in_threadpool(_import_upload, svc, backup)
        except ValueError as exc:
            error = str(exc)
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/", status_code=303)
    return JSONResponse({"ok": error is None, "error": error, **result})


@app.get("/api/preview.png")
async def preview_png(request: Request):
    svc = _svc(request)
    png = svc.current_png_bytes()
    if png is None:
        return Response(status_code=404, content=b"no frame yet")
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "no-cache"})


# -- viewers (W-822) ---------------------------------------------------------
@app.get("/api/view.png")
async def view_png(request: Request, w: Optional[str] = None, h: Optional[str] = None,
                   format: Optional[str] = None, rotation: Optional[str] = None):
    """What the frame is showing, for another screen: a PNG `w`x`h`, in
    `format` (gray16 | gray2 | mono are dithered; gray256 | color are smooth),
    the upright picture turned `rotation` inside it. Read-only: asking never
    moves the frame."""
    view = pipeline.View.parse(w, h, format, rotation)
    if view is None:
        return JSONResponse({"error": "w and h (64-4096) are required; format is one of "
                             + ", ".join(pipeline.VIEW_FORMATS) + "; rotation 0, 90, 180 or 270"},
                            status_code=400)
    inm = _strip_etag(request.headers.get("if-none-match"))
    # Threadpool: the first ask for a variant dithers a whole sheet.
    status, png, etag = await run_in_threadpool(_svc(request).view_png, view, inm)
    if status == 404:
        return Response(status_code=404, content=b"no frame yet")
    headers = {"ETag": f'"{etag}"', "Cache-Control": "no-cache"}
    if status == 304:
        return Response(status_code=304, headers=headers)
    return Response(content=png, media_type="image/png", headers=headers)


# TRMNL's bring-your-own-server protocol (W-824), as its firmware speaks it
# (usetrmnl/trmnl-firmware; terminus doc/api.adoc): /api/setup hands a new
# device a key, /api/display says which image to show and when to come back,
# /api/log takes its complaints. A TRMNL, or a Kobo/Kindle/KOReader running
# TRMNL's client, is a frame like any other: it asks, and is sent the waiting
# plate until the owner answers on the page.
def _viewer_image_url(request: Request, viewer_id: str, filename: str) -> str:
    return (str(request.base_url).rstrip("/")
            + f"/api/viewers/{quote(viewer_id, safe='')}/{filename}.png")


@app.get("/api/setup")
async def trmnl_setup(request: Request):
    svc = _svc(request)
    viewer_id = viewers.clean_id(request.headers.get("id"))
    if viewer_id is None:
        return JSONResponse({"status": 404, "api_key": "", "friendly_id": "", "image_url": "",
                             "message": "An ID header (the device's MAC) is required."},
                            status_code=404)
    row = await run_in_threadpool(svc.checkin_viewer, viewer_id, svc._clock(), "trmnl",
                                  viewers.trmnl_report(request.headers),
                                  request.client.host if request.client else None)
    log.info("viewer %s set up (%s)", viewer_id, row["reported"].get("model") or "TRMNL client")
    return JSONResponse({"status": 200, "api_key": row["token"],
                         "friendly_id": viewer_id.replace(":", "")[-6:],
                         "image_url": "", "message": "Welcome to Featherframe"})


@app.get("/api/display")
async def trmnl_display(request: Request):
    svc = _svc(request)
    viewer_id = viewers.clean_id(request.headers.get("id"))
    if viewer_id is None:
        # TRMNL's own shell clients (Kobo, Kindle) always send one; a client
        # that sends only its key is found by it.
        token = _str_header(request.headers.get("access-token"))
        viewer_id = next((str(r["id"]) for r in svc.frames.by_transport(*viewers.KINDS)
                          if token and hmac.compare_digest(str(r.get("token", "")), token)), None)
    if viewer_id is None:
        return JSONResponse({"status": 404, "error": "An ID header is required."}, status_code=404)
    row = await run_in_threadpool(svc.checkin_viewer, viewer_id, svc._clock(), "trmnl",
                                  viewers.trmnl_report(request.headers),
                                  request.client.host if request.client else None)
    view = viewers.view_of(row)
    # Every frame is approved on the server (W-833): until the owner answers,
    # the device is sent the waiting plate, drawn for its own screen, and comes
    # back soon so it picks the picture up moments after they say yes.
    if row.get("status") != frames_mod.ON:
        ignored = row.get("status") == frames_mod.IGNORED
        filename = f"waiting-{view.key}"
        return JSONResponse({"status": 0, "image_url": _viewer_image_url(request, viewer_id,
                                                                         filename),
                             "filename": filename, "image_url_timeout": 0,
                             "refresh_rate": (viewers.IGNORED_REFRESH_SECONDS if ignored
                                              else viewers.WAITING_REFRESH_SECONDS),
                             "update_firmware": False, "firmware_url": None,
                             "reset_firmware": False, "special_function": "none"})
    if not svc.current_etag():
        return Response(status_code=503, content=b"no frame yet")
    etag = svc.picture_etag(viewers.shows_of(row))   # the plate's or the collage's
    # The device repaints only when the filename changes: the frame's ETag and
    # the variant, so a new plate, or a new rotation from the page, is news.
    filename = f"{etag}-{view.key}"
    return JSONResponse({"status": 0, "image_url": _viewer_image_url(request, viewer_id, filename),
                         "filename": filename, "image_url_timeout": 0,
                         "refresh_rate": svc.viewer_refresh_seconds(row),
                         "update_firmware": False, "firmware_url": None, "reset_firmware": False,
                         "special_function": "none"})


@app.post("/api/log")
async def trmnl_log(request: Request):
    body = (await request.body())[:4096]
    log.debug("viewer %s log: %s", viewers.clean_id(request.headers.get("id")) or "?",
              body.decode("utf-8", "replace"))
    return Response(status_code=204)


@app.get("/api/viewers/{viewer_id}/{name}.png")
async def viewer_png(request: Request, viewer_id: str, name: str):
    """A viewer's image. The name is only what made the device fetch (and what
    keeps a cache honest); which picture it is, is `picture_for`'s call. The
    path stays under /api/viewers: a device that is asleep holds the URL it was
    handed, and moving it would blank the next screen that wakes."""
    svc = _svc(request)
    row = svc.frames.get(viewers.clean_id(viewer_id) or "")
    if row is None or frames_mod.transport_of(row) not in viewers.KINDS:
        return Response(status_code=404, content=b"no such viewer")
    inm = _strip_etag(request.headers.get("if-none-match"))
    if row.get("status") != frames_mod.ON:
        # Not added yet: the waiting plate, in this screen's own terms.
        status, png, etag = await run_in_threadpool(svc.waiting_png, viewers.view_of(row),
                                                    svc.frame_short(row["id"]))
        if inm == etag:
            return Response(status_code=304, headers={"ETag": f'"{etag}"',
                                                      "Cache-Control": "no-cache"})
        return Response(content=png, media_type="image/png",
                        headers={"ETag": f'"{etag}"', "Cache-Control": "no-cache"})
    status, png, etag = await run_in_threadpool(svc.view_png, viewers.view_of(row), inm,
                                               viewers.shows_of(row))
    if status == 404:
        return Response(status_code=404, content=b"no frame yet")
    headers = {"ETag": f'"{etag}"', "Cache-Control": "no-cache"}
    if status == 304:
        return Response(status_code=304, headers=headers)
    return Response(content=png, media_type="image/png", headers=headers)


# The kiosk page (W-825): the plate edge to edge in a browser, for a tablet on
# a stand. The page is dumb on purpose (old iPads run it): it says who it is
# and how big, and is told which image to show and whether to go dark.
@app.get("/view", response_class=HTMLResponse)
async def view_page(request: Request):
    return templates.TemplateResponse(request, "view.html",
                                      {"poll_seconds": viewers.PAGE_POLL_SECONDS})


@app.get("/view.webmanifest", include_in_schema=False)
async def view_manifest():
    return JSONResponse({
        "name": "Featherframe", "short_name": "Featherframe", "start_url": "/view",
        "display": "fullscreen", "orientation": "any",
        "background_color": "#ffffff", "theme_color": "#ffffff",
        "icons": [{"src": "/static/favicon-192.png", "sizes": "192x192", "type": "image/png"}],
    }, media_type="application/manifest+json")


@app.get("/api/view/state")
async def view_state(request: Request, viewer: Optional[str] = None, w: Optional[str] = None,
                     h: Optional[str] = None, device: Optional[str] = None):
    svc = _svc(request)
    viewer_id = viewers.clean_id(viewer)
    size = viewers.page_size(w, h)
    if viewer_id is None or size is None:
        return JSONResponse({"error": "viewer, w and h are required"}, status_code=400)
    reported = {"width": size[0], "height": size[1], "model": _str_header(device, 40)}
    row = await run_in_threadpool(svc.checkin_viewer, viewer_id, svc._clock(), "page", reported,
                                  request.client.host if request.client else None)
    # Every frame is approved on the server (W-833). A page that has not been
    # added yet says so on its own glass and keeps asking; the moment the owner
    # answers, the next poll hands it the picture.
    if row.get("status") != frames_mod.ON:
        return JSONResponse({"image": None, "dark": False, "waiting": True,
                             "id": svc.frame_short(row["id"]),
                             "poll": viewers.PAGE_POLL_SECONDS},
                            headers={"Cache-Control": "no-store"})
    if not svc.current_etag():
        return JSONResponse({"image": None, "dark": False, "poll": viewers.PAGE_POLL_SECONDS})
    view = viewers.view_of(row)
    etag = svc.picture_etag(viewers.shows_of(row))
    return JSONResponse({
        "image": f"/api/viewers/{quote(viewer_id, safe='')}/{etag}-{view.key}.png",
        # A page shows the plate whatever the hour; the key stays so a /view
        # tab that has been open since before this build keeps working.
        "dark": False,
        "paper": view.fmt != "color",
        "poll": viewers.PAGE_POLL_SECONDS,
    }, headers={"Cache-Control": "no-store"})


@app.get("/api/battery")
async def api_battery(request: Request, hours: int = 24, frame: Optional[str] = None):
    """One frame's voltage readings for its trend line, plus the power state
    the server infers from them (there is no USB-present line on the board).
    Every frame is named: `frame` is its id."""
    svc = _svc(request)
    hours = max(1, min(int(hours), 24 * 7))
    return JSONResponse(await run_in_threadpool(svc.battery_view, hours,
                                                (frame or "")[:40] or None))


@app.get("/api/status")
async def status(request: Request):
    # Threadpool: status() probes the detection source (network for the
    # HTTP backends) — never on the loop.
    body = await run_in_threadpool(_svc(request).status)
    adv = getattr(request.app.state, "advertiser", None)
    body["mdns"] = adv.status() if adv else None
    return JSONResponse(body)


# -- render history --------------------------------------------------------
_ETAG_RE = re.compile(r"^[0-9a-f]{16}$")


@app.get("/api/history")
async def history(request: Request):
    # Threadpool: a DB read plus one stat per thumbnail on the SD card.
    return JSONResponse({"items": await run_in_threadpool(_svc(request).render_history)})


@app.get("/api/history/{etag}.png")
async def history_png(request: Request, etag: str):
    # The ETag is a content hash and the only path segment we accept, so the
    # file is immutable and safe to cache for a day.
    if not _ETAG_RE.match(etag):
        return Response(status_code=404)
    png = paths.history_dir() / f"{etag}.png"
    if not await run_in_threadpool(png.exists):
        return Response(status_code=404)
    return FileResponse(png, media_type="image/png",
                        headers={"Cache-Control": "max-age=86400"})


@app.get("/api/history/{etag}.jpg")
async def history_jpg(request: Request, etag: str):
    # The same frame full size, for the page's zoom.
    if not _ETAG_RE.match(etag):
        return Response(status_code=404)
    jpg = paths.history_dir() / f"{etag}.jpg"
    if not await run_in_threadpool(jpg.exists):
        return Response(status_code=404)
    return FileResponse(jpg, media_type="image/jpeg",
                        headers={"Cache-Control": "max-age=86400"})


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@app.get("/api/collages/{day}.png")
async def collage_day_png(request: Request, day: str):
    # A download: the day's finished collage, named for the day.
    if not _DATE_RE.match(day):
        return Response(status_code=404)
    png = paths.collage_days_dir() / f"{day}.png"
    if not await run_in_threadpool(png.exists):
        return Response(status_code=404)
    return FileResponse(png, media_type="image/png",
                        filename=f"featherframe-collage-{day}.png")


def _source_test(source, backend: str) -> dict:
    """Describe what a detection source reports. Never raises."""
    try:
        if backend == "apprise":
            n = source.max_rowid() if hasattr(source, "max_rowid") else 0
            return {"ok": True, "detail": f"Webhook ready — {n} detection(s) received so far."}
        if not source.available():
            return {"ok": False, "detail": "Not reachable — check the settings above."}
        latest = source.latest(0.0)
        if latest and latest.common_name:
            return {"ok": True, "detail": f"Connected — most recent: {latest.common_name}."}
        return {"ok": True, "detail": "Connected — no detections yet."}
    except Exception as exc:  # never raise from a diagnostic
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"[:160]}


@app.post("/api/source/test")
async def source_test(request: Request, backend: Optional[str] = Form(None),
                      birdnet_go_url: Optional[str] = Form(None),
                      birdweather_station_id: Optional[str] = Form(None),
                      birdnet_db_path: Optional[str] = Form(None)):
    """Test a detection source using the values currently typed on the config
    page — no save required. Builds a throwaway source from the posted fields
    layered over a copy of the saved config. Only non-secret connection fields
    are accepted (never the Apprise shared secret). Guarded like the app's other
    state endpoints: this probe makes an outbound request to a user-supplied URL,
    so it must not be triggerable cross-site (SSRF)."""
    if not _same_origin(request):
        return _forbidden_cross_origin()
    import dataclasses
    from .sources import make_source
    svc = _svc(request)
    overrides = {}
    if backend:
        overrides["detection_backend"] = backend
    if birdnet_go_url is not None:
        overrides["birdnet_go_url"] = birdnet_go_url
    if birdweather_station_id is not None:
        overrides["birdweather_station_id"] = birdweather_station_id
    if birdnet_db_path is not None:
        overrides["birdnet_db_path"] = birdnet_db_path

    def run():
        try:
            cfg = dataclasses.replace(svc.config, **overrides)  # __post_init__ re-sanitizes
            source = make_source(cfg, svc.db)
        except Exception as exc:  # a diagnostic must never 500
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"[:160]}
        return _source_test(source, cfg.detection_backend)
    return JSONResponse(await run_in_threadpool(run))


@app.get("/api/imagegen/models")
async def imagegen_models(request: Request, provider: Optional[str] = None):
    """Model choices for the image-generation dropdown. Only the saved
    provider can be queried live (we hold only its key); a different provider
    passed by the switching UI gets the static fallback list."""
    from .render import genart
    svc = _svc(request)
    if provider and provider != svc.config.imagegen_provider:
        fb = genart._MODEL_FALLBACKS.get(provider, [])  # noqa: SLF001
        return JSONResponse({"models": fb, "live": False, "free_text": provider == "a1111"})
    out = await run_in_threadpool(genart.list_image_models, svc.config)
    return JSONResponse(out)


@app.get("/api/tasks")
async def tasks(request: Request):
    # Live state of the background one-shot jobs (test detection, collage)
    # so the config page can show progress and clear its spinner on completion.
    return JSONResponse(_svc(request).task_status())


# -- header parsing helpers -----------------------------------------------
_MAX_INGEST_BYTES = 64 * 1024
_HEADER_STR_MAX = 120


def _str_header(v: Optional[str], limit: int = _HEADER_STR_MAX) -> Optional[str]:
    """Free-text device headers are stored and rendered; bound their size."""
    if v is None:
        return None
    # Control characters have no business in a caption or a kv row.
    v = "".join(ch for ch in v if ch.isprintable()).strip()[:limit]
    return v or None


def _ranged_float(v, lo: float, hi: float) -> Optional[float]:
    """A finite float within [lo, hi], else None ("not reported")."""
    f = _to_float(v, None)
    return f if f is not None and lo <= f <= hi else None


def _ranged_int(v, lo: int, hi: int) -> Optional[int]:
    i = _to_int(v, None)
    return i if i is not None and lo <= i <= hi else None


def _to_float(v, default):
    """float(v), or default when it isn't a finite number. NaN and ±inf are
    refused: they parse, but can't be clamped, compared, or serialised to
    JSON (Starlette's JSONResponse raises on them)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _to_int(v, default):
    f = _to_float(v, None)
    if f is None:
        return default
    try:
        return int(f)
    except (OverflowError, ValueError):
        return default
