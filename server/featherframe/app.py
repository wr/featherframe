"""FastAPI app: the device endpoint and the LAN config page.

No auth (LAN-only — see the README). No SPA, no build step: one server-rendered
page and a handful of endpoints.
"""
from __future__ import annotations

import logging
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
async def api_frame(request: Request):
    svc = _svc(request)
    inm = _strip_etag(request.headers.get("if-none-match"))
    volt = _float_header(request.headers.get("x-battery-voltage"))
    pct = _int_header(request.headers.get("x-battery-percent"))
    status, body, etag = svc.get_frame(inm, request.headers.get("user-agent", ""), volt, pct)

    if status == 503:
        return Response(status_code=503, content=b"no frame yet")
    headers = {"ETag": f'"{etag}"', "Cache-Control": "no-cache"}
    if status == 304:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/octet-stream", headers=headers)


# -- config page -----------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    svc = _svc(request)
    return templates.TemplateResponse(
        request, "index.html",
        {"status": svc.status(), "config": svc.config, "version": __version__,
         "generated": svc.genart.cached_species() if svc.genart else []})


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
        quiet_hours_enabled=b("quiet_hours_enabled"),
        quiet_hours_start=s("quiet_hours_start", cur["quiet_hours_start"]),
        quiet_hours_end=s("quiet_hours_end", cur["quiet_hours_end"]),
        quiet_hours_render_collage=b("quiet_hours_render_collage"),
        species_blocklist=blocklist,
        detection_backend=s("detection_backend", cur["detection_backend"]),
        birdnet_db_path=s("birdnet_db_path", cur["birdnet_db_path"]),
        birdnet_go_url=s("birdnet_go_url", cur["birdnet_go_url"]),
        poll_interval_seconds=i("poll_interval_seconds", cur["poll_interval_seconds"]),
        gray_mode=s("gray_mode", cur["gray_mode"]),
        dither=s("dither", cur["dither"]),
        show_plate_number=b("show_plate_number"),
        collage_rebuilds_per_day=i("collage_rebuilds_per_day", cur["collage_rebuilds_per_day"]),
        panel_rotation=i("panel_rotation", cur["panel_rotation"]),
        mat_inset_pct=f("mat_inset_pct", cur["mat_inset_pct"]),
        imagegen_enabled=b("imagegen_enabled"),
        collage_generated=b("collage_generated"),
        imagegen_provider=s("imagegen_provider", cur["imagegen_provider"]),
        imagegen_model=s("imagegen_model", cur["imagegen_model"]),
        imagegen_quality=s("imagegen_quality", cur["imagegen_quality"]),
        # A typed key always wins; blank means "keep the stored key" unless
        # the clear checkbox is ticked.
        imagegen_api_key=(str(form.get("imagegen_api_key", "") or "").strip()
                          or ("" if b("imagegen_clear_key") else cur["imagegen_api_key"])),
    )
    render_affecting = (new.gray_mode != svc.config.gray_mode
                        or new.dither != svc.config.dither
                        or new.show_plate_number != svc.config.show_plate_number
                        or new.panel_rotation != svc.config.panel_rotation
                        or new.mat_inset_pct != svc.config.mat_inset_pct)
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
    # Threadpool: a plate-less species may generate art (up to ~2 min).
    await run_in_threadpool(svc.force_test_detection, common, sci)
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/", status_code=303)
    return JSONResponse({"ok": True, "etag": svc._etag})


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
    # Threadpool: a fresh sheet is a ~1-2 minute generation.
    ok = await run_in_threadpool(svc.force_day_review, repaint)
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/", status_code=303)
    return JSONResponse({"ok": ok})


# -- AI-generated plates ---------------------------------------------------
def _valid_slug(slug: str) -> bool:
    return bool(slug) and slug.replace("-", "").isalnum()


@app.get("/api/generated")
async def generated_list(request: Request):
    svc = _svc(request)
    return JSONResponse({"cached": svc.genart.cached_species()})


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
    ok = False
    if _valid_slug(slug):
        ok = await run_in_threadpool(svc.regenerate_generated, slug)
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
