"""FastAPI app: the device endpoint and the LAN config page.

No auth (LAN-only — see the README). No SPA, no build step: one server-rendered
page and a handful of endpoints.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Form, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__, paths
from .config import Config
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
    volt = _float_header(request.headers.get("x-battery-voltage"))
    pct = _int_header(request.headers.get("x-battery-percent"))
    rssi = _int_header(request.headers.get("x-wifi-rssi"))
    wake = request.headers.get("x-wake")
    if wake:
        log.info("device wake: %s (view=%s)", wake, view)

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
                                wifi_rssi=rssi)
        return Response(content=result.frame, media_type="application/octet-stream",
                        headers={"ETag": f'"{result.etag}"', "Cache-Control": "no-store",
                                 "X-FF-Invert": invert})

    status, body, etag = svc.get_frame(inm, request.headers.get("user-agent", ""), volt, pct,
                                       wifi_rssi=rssi)

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
    blob = bin_path.read_bytes()
    md5 = hashlib.md5(blob).hexdigest()
    if request.headers.get("x-firmware-md5", "").lower() == md5:
        return Response(status_code=304)
    log.info("serving firmware.bin (%d bytes, md5=%s) to %s",
             len(blob), md5, request.headers.get("user-agent", "?"))
    return Response(content=blob, media_type="application/octet-stream",
                    headers={"X-MD5": md5, "Cache-Control": "no-store"})


# -- config page -----------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    svc = _svc(request)
    return templates.TemplateResponse(
        request, "index.html",
        {"status": svc.status(), "config": svc.config, "version": __version__,
         "dev_mode": DEV_MODE,
         "generated": svc.generated_listing() if svc.genart else []})


@app.post("/settings")
async def save_settings(request: Request):
    if not _same_origin(request):
        return _forbidden_cross_origin()
    svc = _svc(request)
    form = await request.form()
    cur = svc.config.to_dict()

    def s(key, default): return form.get(key, default)
    def f(key, default): return _to_float(form.get(key), default)
    def i(key, default): return _to_int(form.get(key), default)
    def b(key): return key in form  # checkbox present -> true

    blocklist_raw = str(form.get("species_blocklist", "") or "")
    blocklist = [x.strip() for x in blocklist_raw.replace(",", "\n").splitlines() if x.strip()]

    new = Config(
        mode=s("mode", cur["mode"]),
        confidence_threshold=f("confidence_threshold", cur["confidence_threshold"]),
        single_show_latest=b("single_show_latest"),
        refresh_debounce_minutes=i("refresh_debounce_minutes", cur["refresh_debounce_minutes"]),
        wake_interval_minutes=i("wake_interval_minutes", cur["wake_interval_minutes"]),
        quiet_hours_mode=s("quiet_hours_mode", cur["quiet_hours_mode"]),
        quiet_hours_start=s("quiet_hours_start", cur["quiet_hours_start"]),
        quiet_hours_end=s("quiet_hours_end", cur["quiet_hours_end"]),
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
        imagegen_api_key=(str(form.get("imagegen_api_key", "") or "").strip()
                          or ("" if b("imagegen_clear_key") else cur["imagegen_api_key"])),
        imagegen_text_key=(str(form.get("imagegen_text_key", "") or "").strip()
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
    svc.update_config(new)
    if render_affecting:
        # Threadpool: the provider chain may generate art over the network now,
        # and a blocking render here would stall every endpoint on the loop.
        await run_in_threadpool(svc.rerender_current)
    return RedirectResponse("/", status_code=303)


@app.post("/api/test-detection")
async def test_detection(request: Request):
    if not _same_origin(request):
        return _forbidden_cross_origin()
    svc = _svc(request)
    form = await request.form()
    common = str(form.get("common", "") or "").strip() or "Northern Cardinal"
    sci = str(form.get("scientific", "") or "").strip()
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
    if expected and token != expected:
        return JSONResponse({"error": "bad token"}, status_code=403)
    ingest = getattr(svc.source, "ingest", None)
    if not callable(ingest):
        return JSONResponse({"error": "detection source is not Apprise"}, status_code=409)
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
    ok = False
    if _valid_slug(slug):
        ok = await run_in_threadpool(svc.start_regenerate, slug)
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/", status_code=303)
    return JSONResponse({"ok": ok})


@app.post("/api/generated/delete")
async def generated_delete(request: Request, slug: str = Form(...)):
    if not _same_origin(request):
        return _forbidden_cross_origin()
    svc = _svc(request)
    ok = svc.delete_generated(slug) if _valid_slug(slug) else False
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/", status_code=303)
    return JSONResponse({"ok": ok})


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
    return JSONResponse(_svc(request).status())


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


@app.get("/api/source/test")
async def source_test(request: Request, backend: Optional[str] = None,
                      birdnet_go_url: Optional[str] = None,
                      birdweather_station_id: Optional[str] = None,
                      birdnet_db_path: Optional[str] = None):
    """Test a detection source using the values currently typed on the config
    page — no save required. Builds a throwaway source from the posted fields
    layered over a copy of the saved config. Only non-secret connection fields
    are accepted (never the Apprise shared secret)."""
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
    cfg = dataclasses.replace(svc.config, **overrides)  # __post_init__ re-sanitizes

    def run():
        return _source_test(make_source(cfg, svc.db), cfg.detection_backend)
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
def _float_header(v: Optional[str]) -> Optional[float]:
    return _to_float(v, None)


def _int_header(v: Optional[str]) -> Optional[int]:
    return _to_int(v, None)


def _to_float(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _to_int(v, default):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default
