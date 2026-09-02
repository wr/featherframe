"""FastAPI app: the device endpoint and the LAN config page.

No auth (LAN-only — see the README). No SPA, no build step: one server-rendered
page and a handful of endpoints.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, Form, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__, paths
from .config import Config, valid_hhmm
from .names import normalize
from .service import FeatherframeService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("featherframe.app")

templates = Jinja2Templates(directory=str(paths.templates_dir()))

# Dev-only affordances (e.g. the Test-detection button) are hidden in a normal
# install; `make serve` sets FEATHERFRAME_DEV=1. Anything truthy-ish enables.
DEV_MODE = os.environ.get("FEATHERFRAME_DEV", "").strip().lower() in ("1", "true", "yes", "on")


@asynccontextmanager
async def lifespan(app: FastAPI):
    service = FeatherframeService()
    app.state.service = service
    service.start()
    try:
        yield
    finally:
        service.stop()


app = FastAPI(title="Featherframe", version=__version__, lifespan=lifespan)
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
@app.get("/api/frame")
async def api_frame(request: Request, view: Optional[str] = None):
    svc = _svc(request)
    inm = _strip_etag(request.headers.get("if-none-match"))
    # Telemetry is untrusted input from the LAN: "nan"/"inf" parse as floats
    # but a NaN persisted into device_status makes every /api/status 500
    # (JSON can't carry it), and int(float("inf")) raises. Only plausible,
    # finite values are kept; anything else reads as "not reported".
    volt = _ranged_float(request.headers.get("x-battery-voltage"), 0.0, 6.0)
    pct = _ranged_int(request.headers.get("x-battery-percent"), 0, 100)
    rssi = _ranged_int(request.headers.get("x-wifi-rssi"), -120, 0)
    wake = _str_header(request.headers.get("x-wake"))
    client_ip = request.client.host if request.client else None
    if wake:
        log.info("device wake: %s (view=%s)", wake, view)

    # Optional device-reported identity/telemetry (docs/firmware-device-stats.md).
    # Every field is optional on the wire; absent headers leave the row unchanged.
    device_extra = {
        "fw_version": _str_header(request.headers.get("x-ff-version")),
        "sketch_md5": _str_header(request.headers.get("x-ff-sketch-md5")),
        "last_wake": wake,
        "wake_detail": _str_header(request.headers.get("x-wake-detail")),
        "boot_count": _ranged_int(request.headers.get("x-boot-count"), 0, 2**31),
        "refresh_count": _ranged_int(request.headers.get("x-refresh-count"), 0, 2**31),
        "panel": _str_header(request.headers.get("x-panel")),
        "board": _str_header(request.headers.get("x-board")),
    }

    # Dark mode rides along on every response — a 304 included — so the device
    # always knows whether to invert its baked boot screens.
    invert = "1" if svc.config.dark_now() else "0"

    # On-demand button views: rendered fresh, never the resident frame, no
    # 304s. Threadpool: the collage leg walks the provider chain (which may
    # generate art over the network) and a blocking render here would stall
    # every endpoint on the loop.
    if view in ("collage", "status"):
        if view == "collage":
            result = await run_in_threadpool(svc.render_collage_on_demand)
            if result is None:
                return Response(status_code=404, content=b"not enough birds for a collage")
        else:
            result = await run_in_threadpool(svc.render_status_page, volt, pct, rssi)
        svc.record_view_checkin(request.headers.get("user-agent", ""), volt, pct, view,
                                wifi_rssi=rssi, ip=client_ip, device_extra=device_extra)
        return Response(content=result.frame, media_type="application/octet-stream",
                        headers={"ETag": f'"{result.etag}"', "Cache-Control": "no-store",
                                 "X-FF-Invert": invert})

    status, body, etag = svc.get_frame(inm, request.headers.get("user-agent", ""), volt, pct,
                                       wifi_rssi=rssi, ip=client_ip, device_extra=device_extra)

    if status == 503:
        return Response(status_code=503, content=b"no frame yet",
                        headers={"X-FF-Invert": invert})
    headers = {"ETag": f'"{etag}"', "Cache-Control": "no-cache", "X-FF-Invert": invert}
    if status == 304:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/octet-stream", headers=headers)


# -- firmware OTA ----------------------------------------------------------
# The device offers its running sketch MD5 on every wake. If data/firmware.bin
# exists and differs, it gets the new build; otherwise 304. Deploy = drop a new
# firmware.bin in the data dir (`make ota` does build + copy).
@app.get("/api/firmware")
async def api_firmware(request: Request):
    bin_path = paths.data_dir() / "firmware.bin"
    if not bin_path.exists():
        return Response(status_code=404, content=b"no firmware hosted")
    blob, md5 = _hosted_firmware(bin_path)
    if blob is None:
        return Response(status_code=404, content=b"no firmware hosted")
    if request.headers.get("x-firmware-md5", "").lower() == md5:
        return Response(status_code=304)
    log.info("serving firmware.bin (%d bytes, md5=%s) to %s",
             len(blob), md5, request.headers.get("user-agent", "?"))
    return Response(content=blob, media_type="application/octet-stream",
                    headers={"X-MD5": md5, "Cache-Control": "no-store"})


_FW_CACHE: dict = {}   # (mtime_ns, size) -> (bytes, md5)


def _hosted_firmware(bin_path):
    """The hosted image and its digest, re-read only when the file changes:
    the device asks on every wake and hashing 1.5 MB each time on a Pi Zero
    is wasteful. A file with a valid ESP image header only (0xE9 magic), so
    a stray file dropped in data/ is not pushed to the frame."""
    try:
        st = bin_path.stat()
    except OSError:
        return None, None
    key = (st.st_mtime_ns, st.st_size)
    hit = _FW_CACHE.get(key)
    if hit is None:
        blob = bin_path.read_bytes()
        if not blob or blob[0] != 0xE9:
            log.warning("data/firmware.bin is not an ESP image (bad magic); not hosting it")
            return None, None
        hit = (blob, hashlib.md5(blob).hexdigest())
        _FW_CACHE.clear()
        _FW_CACHE[key] = hit
    return hit


# -- config page -----------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    svc = _svc(request)
    # Threadpool: status() probes the detection source (a 5 s-timeout HTTP
    # call for BirdNET-Go/BirdWeather) and the listing reads the SD card;
    # blocking the loop here would stall the device's /api/frame fetch.
    status = await run_in_threadpool(svc.status)
    generated = await run_in_threadpool(svc.generated_listing) if svc.genart else []
    return templates.TemplateResponse(
        request, "index.html",
        {"status": status, "config": svc.config, "version": __version__,
         "dev_mode": DEV_MODE, "generated": generated})


@app.post("/settings")
async def save_settings(request: Request):
    if not _same_origin(request):
        return _forbidden_cross_origin()
    svc = _svc(request)
    try:
        form = await request.form()
    except Exception:  # noqa: BLE001 — a malformed body must land on the page, not a 500
        return RedirectResponse("/?error=" + quote("Could not read the form."), status_code=303)
    cur = svc.config.to_dict()

    # A multipart file part under a text field's name comes back as an
    # UploadFile, which Config can't sanitize or serialise: only real strings
    # are accepted, anything else keeps the stored value.
    def s(key, default):
        v = form.get(key)
        return v if isinstance(v, str) else default
    def f(key, default): return _to_float(s(key, None), default)
    def i(key, default): return _to_int(s(key, None), default)
    def b(key): return key in form  # checkbox present -> true
    # A clock time that isn't one ("99:99") keeps the stored value rather
    # than being coerced to some other time or reset to the default.
    def t(key, default): return s(key, default) if valid_hhmm(s(key, None)) else default

    blocklist_raw = s("species_blocklist", "")
    blocklist = [x.strip() for x in blocklist_raw.replace(",", "\n").splitlines() if x.strip()]

    new = Config(
        mode=s("mode", cur["mode"]),
        confidence_threshold=f("confidence_threshold", cur["confidence_threshold"]),
        single_show_latest=b("single_show_latest"),
        refresh_debounce_minutes=i("refresh_debounce_minutes", cur["refresh_debounce_minutes"]),
        wake_interval_minutes=i("wake_interval_minutes", cur["wake_interval_minutes"]),
        quiet_hours_mode=s("quiet_hours_mode", cur["quiet_hours_mode"]),
        quiet_hours_start=t("quiet_hours_start", cur["quiet_hours_start"]),
        quiet_hours_end=t("quiet_hours_end", cur["quiet_hours_end"]),
        quiet_hours_render_collage=b("quiet_hours_render_collage"),
        species_blocklist=blocklist,
        detection_backend=s("detection_backend", cur["detection_backend"]),
        birdnet_db_path=s("birdnet_db_path", cur["birdnet_db_path"]),
        birdnet_go_url=s("birdnet_go_url", cur["birdnet_go_url"]),
        birdweather_station_id=s("birdweather_station_id", cur["birdweather_station_id"]),
        apprise_token=s("apprise_token", cur["apprise_token"]),
        poll_interval_seconds=i("poll_interval_seconds", cur["poll_interval_seconds"]),
        gray_mode=s("gray_mode", cur["gray_mode"]),
        dither=s("dither", cur["dither"]),
        show_plate_number=b("show_plate_number"),
        show_battery=b("show_battery"),
        collage_rebuilds_per_day=i("collage_rebuilds_per_day", cur["collage_rebuilds_per_day"]),
        panel_rotation=i("panel_rotation", cur["panel_rotation"]),
        mat_inset_pct=f("mat_inset_pct", cur["mat_inset_pct"]),
        mat_offset_x_px=i("mat_offset_x_px", cur["mat_offset_x_px"]),
        mat_offset_y_px=i("mat_offset_y_px", cur["mat_offset_y_px"]),
        dark_mode=s("dark_mode", cur["dark_mode"]),
        imagegen_enabled=b("imagegen_enabled"),
        collage_generated=b("collage_generated"),
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
    render_affecting = (new.gray_mode != svc.config.gray_mode
                        or new.dither != svc.config.dither
                        or new.show_plate_number != svc.config.show_plate_number
                        or new.panel_rotation != svc.config.panel_rotation
                        or new.mat_inset_pct != svc.config.mat_inset_pct
                        or new.mat_offset_x_px != svc.config.mat_offset_x_px
                        or new.mat_offset_y_px != svc.config.mat_offset_y_px
                        or new.dark_mode != svc.config.dark_mode)
    # Config.sanitize() clamps silently; tell the page which fields it changed
    # so the user isn't left staring at a different number than they typed.
    adjusted = _adjusted_fields(form, new)
    try:
        svc.update_config(new)
        if render_affecting:
            # Threadpool: the provider chain may generate art over the network now,
            # and a blocking render here would stall every endpoint on the loop.
            await run_in_threadpool(svc.rerender_current)
    except Exception as exc:  # noqa: BLE001 — surface it on the page, keep the old config
        log.exception("saving settings failed")
        return RedirectResponse("/?error=" + quote(f"Settings were not saved: {exc}"),
                                status_code=303)
    return RedirectResponse("/?saved=1" + ("&adjusted=" + ",".join(adjusted) if adjusted else ""),
                            status_code=303)


_NUMERIC_FORM_FIELDS = ("confidence_threshold", "refresh_debounce_minutes", "wake_interval_minutes",
                        "poll_interval_seconds", "collage_rebuilds_per_day", "mat_inset_pct",
                        "mat_offset_x_px", "mat_offset_y_px", "panel_rotation")


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
    sci = str(form.get("scientific", "") or "").strip()[:200]
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
    entry = svc.audubon._index._by_common.get(normalize(common))  # noqa: SLF001
    if entry and entry.get("scientific"):
        return entry["scientific"]
    summary = getattr(svc.source, "_species_summary", None)
    if callable(summary):
        want = normalize(common)
        for row in summary():
            if normalize(str(row.get("common_name", ""))) == want:
                return row.get("scientific_name")
    return None


@app.post("/api/collage/day-review")
async def day_review(request: Request):
    if not _same_origin(request):
        return _forbidden_cross_origin()
    svc = _svc(request)
    form = await request.form()
    repaint = "repaint" in form
    # Fire-and-forget: a fresh sheet is a ~1-2 minute generation. Same contract
    # as test-detection — run it on a worker thread and let the page poll.
    svc.start_day_review(repaint)
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
    st = await run_in_threadpool(svc.status)
    etag = st.get("current", {}).get("etag")
    return JSONResponse({"ok": True, "etag": etag, "changed": etag != before,
                         "rendered_at": st.get("current", {}).get("rendered_at")})


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
    if expected and not hmac.compare_digest(token, expected):
        return JSONResponse({"error": "bad token"}, status_code=403)
    ingest = getattr(svc.source, "ingest", None)
    if not callable(ingest):
        return JSONResponse({"error": "detection source is not Apprise"}, status_code=409)
    # A detection is a few hundred bytes; don't buffer an arbitrary body into
    # a Pi Zero's memory (the queue is persisted, so bloat would be too).
    if _to_int(request.headers.get("content-length"), 0) > _MAX_INGEST_BYTES:
        return JSONResponse({"error": "body too large"}, status_code=413)
    try:
        payload = await request.json()
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


@app.get("/api/preview.png")
async def preview_png(request: Request):
    svc = _svc(request)
    png = svc.current_png_bytes()
    if png is None:
        return Response(status_code=404, content=b"no frame yet")
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "no-cache"})


@app.get("/api/status")
async def status(request: Request):
    # Threadpool: status() probes the detection source (network for the
    # HTTP backends) — never on the loop.
    return JSONResponse(await run_in_threadpool(_svc(request).status))


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
    # Live state of the background one-shot jobs (test detection, day-in-review)
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
