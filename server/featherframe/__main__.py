"""Run the Featherframe server:  python -m featherframe  (or via systemd)."""
from __future__ import annotations

import argparse
import os


def main() -> None:
    ap = argparse.ArgumentParser(description="Featherframe server")
    ap.add_argument("--host", default=os.environ.get("FEATHERFRAME_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("FEATHERFRAME_PORT", "8080")))
    args = ap.parse_args()

    import uvicorn
    # Single worker on purpose: one render thread, memory-frugal, one source of
    # truth for the current frame.
    uvicorn.run("featherframe.app:app", host=args.host, port=args.port,
                workers=1, log_level="info")


if __name__ == "__main__":
    main()
