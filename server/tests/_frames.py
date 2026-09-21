"""Frames, for tests (W-833).

Every screen this server draws for is a row in the registry, and a kit is
served whatever its row says. These give a service one without driving the
whole check-in path, so a test about headers, ETags or pictures says what it
means in one line.
"""
from __future__ import annotations

from featherframe import frames as frames_mod

EE03_PANEL = "ED103TC2 1404x1872 gray16"
EE02_PANEL = "T133A01 1200x1600 spectra6"
EE03_BOARD = "XIAO ESP32-S3 Plus + EE03"
EE02_BOARD = "XIAO ESP32-S3 Plus + EE02"

FRAME_ID = "AA:AA:AA:00:00:03"


def add_kit(svc, frame_id: str = FRAME_ID, panel: str = EE03_PANEL,
            shows: str = "", status: str = frames_mod.ON, facts=None,
            reported=None, name: str = "", **settings) -> dict:
    """Give this service a kit that shows `shows` with settings `settings`, as
    if it had checked in once."""
    stamp = svc._clock().isoformat(timespec="seconds")
    row = frames_mod.new_row(frame_id, "kit", stamp, status=status)
    rep = {"panel": panel} if panel else {}
    if facts:
        rep["facts"] = facts
    rep.update(reported or {})
    row["reported"] = rep
    own = dict(settings)
    if shows:
        own["shows"] = shows
    if name:
        own["name"] = name
    row["set"] = own
    return svc.frames.save(row)


def give_output(svc, frame_id: str = FRAME_ID, body: bytes = b"FFF1" + bytes(12),
                etag: str = "abc123") -> str:
    """Put bytes on one frame's glass without rendering anything."""
    fff, _png = svc._out_paths(frame_id)
    fff.write_bytes(body)
    svc._out[frame_id] = {"etag": etag, "src": "test"}
    svc._save_outputs()
    return etag


def seed_frame(svc, frame_id: str = FRAME_ID, body: bytes = b"FFF1" + bytes(12),
               etag: str = "abc123", **kw) -> str:
    """A kit with something already on its glass."""
    add_kit(svc, frame_id, **kw)
    return give_output(svc, frame_id, body, etag)


def connect(client, headers: dict):
    """Check a kit in and let one tick draw its first frame, as the running
    server's scheduler does. Nothing is ever rendered in a request."""
    client.get("/api/frame", headers=headers)
    client.app.state.service._tick_frames()
    return client.get("/api/frame", headers=headers)
