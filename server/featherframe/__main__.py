"""Run the Featherframe server:  python -m featherframe  (or via systemd)."""
from __future__ import annotations

import argparse
import os


def main() -> None:
    ap = argparse.ArgumentParser(description="Featherframe server")
    ap.add_argument("--host", default=os.environ.get("FEATHERFRAME_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("FEATHERFRAME_PORT", "8080")))
    args = ap.parse_args()

    os.environ["FEATHERFRAME_PORT"] = str(args.port)   # the mDNS record needs the bound port

    import uvicorn
    # Single worker on purpose: one render thread, memory-frugal, one source of
    # truth for the current frame.
    # A frame on a push socket (W-841) answers pings from its own loop, which a
    # colour panel's ~30 s paint holds up: give it a minute before the socket
    # is called dead, or every paint would cost a reconnect.
    uvicorn.run("featherframe.app:app", host=args.host, port=args.port,
                workers=1, log_level="info", ws_ping_interval=30, ws_ping_timeout=60)


if __name__ == "__main__":
    main()
