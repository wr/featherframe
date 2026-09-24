"""The lobby (W-845): what a hosted frame no household has claimed yet is
shown — its pairing code, drawn for its own panel exactly as a plate would be
(finished, dithered and packed with that panel's own defaults).

The same image as a household's server, another entrypoint, no state:

    python -m featherframe.lobby --port 8080
    GET /render?code=ABC-234&panel=…&w=&h=&fmt=&rot=   -> FFF bytes
    GET /render-view?code=ABC-234&w=&h=&model=           -> PNG (a TRMNL, an
        e-reader: drawn as that screen draws a plate; no code, the waiting plate)
"""
from __future__ import annotations

import argparse

from fastapi import FastAPI, Response
from fastapi.concurrency import run_in_threadpool

from . import frames as frames_mod
from . import viewers as viewers_mod
from .config import Config
from .render import pipeline
from .render import welcome

app = FastAPI(title="Featherframe lobby")


def render_pairing(code: str, panel: str = "", facts: dict | None = None,
                   rotation: int | None = None) -> pipeline.RenderResult:
    """`rotation`: the way up the frame hangs now (W-851), when it is one its
    panel can do; else the panel's default."""
    row = {"id": "lobby", "transport": "kit",
           "reported": {"panel": panel or None, "facts": {k: v for k, v in (facts or {}).items() if v} or None},
           "set": {"panel_rotation": rotation} if rotation is not None else {}}
    cfg = frames_mod.frame_config(row, Config())
    sheet = welcome.render_pairing(code[:12], color=cfg.panel_spec.color)
    return pipeline.render_image(sheet, cfg, "welcome", ""), cfg


@app.get("/render")
async def render(code: str, panel: str = "", w: str = "", h: str = "", fmt: str = "", rot: str = "",
                 cur: str = ""):
    rotation = int(cur) if cur.isdigit() else None
    result, cfg = await run_in_threadpool(render_pairing, code, panel,
                                          {"w": w, "h": h, "fmt": fmt, "rot": rot}, rotation)
    return Response(result.frame, media_type="application/octet-stream",
                    headers={"ETag": f'"{result.etag}"', "X-FF-Rotation": str(cfg.panel_rotation)})


def render_viewer_pairing(code: str, report: dict) -> bytes:
    """The pairing code (or, with none, the waiting plate) for a viewer that
    reported `report` (viewers.trmnl_report's shape), as its own screen draws."""
    row = {"id": "lobby", "transport": "trmnl", "reported": report}
    view = viewers_mod.view_of(row)
    sheet = (welcome.render_pairing(code[:12], color=view.fmt == "color") if code
             else welcome.render_waiting())
    return pipeline.encode_png(pipeline.render_view(sheet, view), view.fmt)


@app.get("/render-view")
async def render_view(code: str = "", w: str = "", h: str = "", model: str = ""):
    report = viewers_mod.trmnl_report({"width": w, "height": h, "model": model})
    png = await run_in_threadpool(render_viewer_pairing, code, report)
    return Response(png, media_type="image/png")


def main() -> None:
    ap = argparse.ArgumentParser(description="Featherframe lobby")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=args.port, workers=1, log_level="info")


if __name__ == "__main__":
    main()
