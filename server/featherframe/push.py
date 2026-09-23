"""Push, don't poll (W-841).

A kit on USB holds one WebSocket to `/api/frame/push`. The server says when
something the frame fetches has changed — its output, or a header that rides
on `/api/frame` — and the frame does its usual `GET /api/frame`. The socket
carries no pixels and no settings: `/api/frame` stays the one contract, and a
frame without a socket (battery, an old build, a proxy that drops upgrades)
polls it exactly as before.

`PushHub` is the registry of open sockets and the one way to wake them. The
scheduler thread calls `notify()` after every tick and a settings save calls
it for its frame; each socket's handler then asks the service for that
frame's message again and sends it only if it changed. It is safe to call
from any thread.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Optional


class PushHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._waiters: dict[str, set] = {}   # frame id -> {(loop, asyncio.Event)}

    def register(self, frame_id: str) -> asyncio.Event:
        """A socket for `frame_id` opened: the event it waits on."""
        event = asyncio.Event()
        with self._lock:
            self._waiters.setdefault(frame_id, set()).add((asyncio.get_running_loop(), event))
        return event

    def unregister(self, frame_id: str, event: asyncio.Event) -> None:
        with self._lock:
            held = self._waiters.get(frame_id)
            if not held:
                return
            held.difference_update({w for w in held if w[1] is event})
            if not held:
                self._waiters.pop(frame_id, None)

    def connected(self, frame_id: str) -> bool:
        """This frame holds a push socket right now."""
        with self._lock:
            return bool(self._waiters.get(frame_id))

    def notify(self, frame_id: Optional[str] = None) -> None:
        """Wake one frame's sockets, or every socket when no id is given."""
        with self._lock:
            if frame_id is None:
                waiters = [w for held in self._waiters.values() for w in held]
            else:
                waiters = list(self._waiters.get(frame_id, ()))
        for loop, event in waiters:
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:   # that loop has closed: its socket is gone too
                pass
