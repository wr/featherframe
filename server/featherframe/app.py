"""FastAPI app: the device endpoint and the LAN config page.

No auth (LAN-only — see the README). No SPA, no build step: one server-rendered
page and a handful of endpoints.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__, paths
from .config import Config
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
        {"status": svc.status(), "config": svc.config, "version": __version__})


@app.post("/settings")
async def save_settings(request: Request):
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
        refresh_debounce_minutes=i("refresh_debounce_minutes", cur["refresh_debounce_minutes"]),
        wake_interval_minutes=i("wake_interval_minutes", cur["wake_interval_minutes"]),
        quiet_hours_enabled=b("quiet_hours_enabled"),
        quiet_hours_start=s("quiet_hours_start", cur["quiet_hours_start"]),
        quiet_hours_end=s("quiet_hours_end", cur["quiet_hours_end"]),
        quiet_hours_render_collage=b("quiet_hours_render_collage"),
        species_blocklist=blocklist,
        birdnet_db_path=s("birdnet_db_path", cur["birdnet_db_path"]),
        poll_interval_seconds=i("poll_interval_seconds", cur["poll_interval_seconds"]),
        gray_mode=s("gray_mode", cur["gray_mode"]),
        dither=s("dither", cur["dither"]),
        show_plate_number=b("show_plate_number"),
        collage_rebuilds_per_day=i("collage_rebuilds_per_day", cur["collage_rebuilds_per_day"]),
        panel_rotation=i("panel_rotation", cur["panel_rotation"]),
        mat_inset_pct=f("mat_inset_pct", cur["mat_inset_pct"]),
    )
    render_affecting = (new.gray_mode != svc.config.gray_mode
                        or new.dither != svc.config.dither
                        or new.show_plate_number != svc.config.show_plate_number
                        or new.panel_rotation != svc.config.panel_rotation
                        or new.mat_inset_pct != svc.config.mat_inset_pct)
    svc.update_config(new)
    if render_affecting:
        svc.rerender_current()
    return RedirectResponse("/", status_code=303)


@app.post("/api/test-detection")
async def test_detection(request: Request):
    svc = _svc(request)
    svc.force_test_detection()
    # form POST from the page -> redirect back; API callers can read /api/status
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/", status_code=303)
    return JSONResponse({"ok": True, "etag": svc._etag})


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
