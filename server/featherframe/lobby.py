"""The lobby (W-845): what a hosted frame no household has claimed yet is
shown — its pairing code, drawn for its own panel exactly as a plate would be
(finished, dithered and packed with that panel's own defaults).

The same image as a household's server, another entrypoint, no state:

    python -m featherframe.lobby --port 8080
    GET /render?code=ABC-234&panel=…&w=&h=&fmt=&rot=   -> FFF bytes
"""
from __future__ import annotations

import argparse

from fastapi import FastAPI, Response
from fastapi.concurrency import run_in_threadpool

from . import frames as frames_mod
from .config import Config
from .render import pipeline
from .render import welcome

app = FastAPI(title="Featherframe lobby")


def render_pairing(code: str, panel: str = "", facts: dict | None = None) -> pipeline.RenderResult:
    row = {"id": "lobby", "transport": "kit",
           "reported": {"panel": panel or None, "facts": {k: v for k, v in (facts or {}).items() if v} or None}}
    cfg = frames_mod.frame_config(row, Config())
    sheet = welcome.render_waiting(line=welcome.PAIRING_LINE, code=code[:12])
    return pipeline.render_image(sheet, cfg, "welcome", ""), cfg


@app.get("/render")
async def render(code: str, panel: str = "", w: str = "", h: str = "", fmt: str = "", rot: str = ""):
    result, cfg = await run_in_threadpool(render_pairing, code, panel,
                                          {"w": w, "h": h, "fmt": fmt, "rot": rot})
    return Response(result.frame, media_type="application/octet-stream",
                    headers={"ETag": f'"{result.etag}"', "X-FF-Rotation": str(cfg.panel_rotation)})


def main() -> None:
    ap = argparse.ArgumentParser(description="Featherframe lobby")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=args.port, workers=1, log_level="info")


if __name__ == "__main__":
    main()
