#!/usr/bin/env python3
"""Register a kit for shipping (W-888): ask it over USB who it is and record
it on hosted, so its owner can set it up from the phone with no setup code.

    ~/.platformio/penv/bin/python tools/register_kit.py --port /dev/cu.usbmodem2101 [--note "Order 1042"]

Run it after flashing the release image. It sends Improv's Featherframe
request (0xF0, W-848), which answers the frame's id, its key and what it is;
only a SHA-256 of the key leaves this machine. The admin token comes from the
keychain (`featherframe-hosted-admin-token`) or FEATHERFRAME_ADMIN_TOKEN.

Opening the port restarts the board; the request is repeated until it
answers (about 5 s after boot). Erasing the kit's NVS afterwards (the
flasher's Erase) gives it a new key, and it would have to be registered again.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request

import serial  # pyserial: PlatformIO's own Python has it

HEADER = b"IMPROV"
VERSION = 1
TYPE_RPC = 0x03
TYPE_RPC_RESULT = 0x04
CMD_FF_SERVER = 0xF0
FIELDS = ("id", "key", "panel", "w", "h", "fmt", "rots", "rotation", "board", "mat")


def packet(type_: int, data: bytes) -> bytes:
    body = HEADER + bytes([VERSION, type_, len(data)]) + data
    return body + bytes([sum(body) & 0xFF]) + b"\n"


def parse_result(buf: bytes) -> list[str] | None:
    """The first 0xF0 result in `buf`: its length-prefixed strings."""
    at = 0
    while True:
        i = buf.find(HEADER, at)
        if i < 0 or len(buf) < i + 9:
            return None
        type_, n = buf[i + 7], buf[i + 8]
        end = i + 9 + n
        if len(buf) < end + 1:
            return None
        data = buf[i + 9:end]
        if type_ == TYPE_RPC_RESULT and sum(buf[i:end]) & 0xFF == buf[end] and data[:1] == bytes([CMD_FF_SERVER]):
            out, p = [], 2
            while p < len(data):
                ln = data[p]
                out.append(data[p + 1:p + 1 + ln].decode("utf-8", "replace"))
                p += 1 + ln
            return out
        at = i + 1


def identity(port: str, timeout_s: float = 30.0) -> dict[str, str]:
    ask = packet(TYPE_RPC, bytes([CMD_FF_SERVER, 0]))   # no URL: only ask
    with serial.Serial(port, 115200, timeout=0.2) as s:
        buf, deadline, sent = b"", time.time() + timeout_s, 0.0
        while time.time() < deadline:
            if time.time() - sent > 1.0:
                s.write(ask)
                sent = time.time()
            buf = (buf + s.read(4096))[-8192:]
            got = parse_result(buf)
            if got and len(got) >= 2 and got[0] and got[1]:
                return dict(zip(FIELDS, got))
    raise SystemExit(f"{port}: the frame did not answer (is it running Featherframe firmware?)")


def admin_token() -> str:
    tok = os.environ.get("FEATHERFRAME_ADMIN_TOKEN", "")
    if not tok:
        tok = subprocess.run(["security", "find-generic-password", "-s", "featherframe-hosted-admin-token", "-w"],
                             capture_output=True, text=True).stdout.strip()
    if not tok:
        raise SystemExit("no admin token: set FEATHERFRAME_ADMIN_TOKEN or add it to the keychain")
    return tok


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", required=True)
    ap.add_argument("--note", default="")
    ap.add_argument("--host", default="https://app.featherframe.app")
    ap.add_argument("--dry-run", action="store_true", help="ask the frame, register nothing")
    args = ap.parse_args()

    me = identity(args.port)
    kit = "ee02" if "ee02" in me.get("board", "").lower() else "ee03" if "ee03" in me.get("board", "").lower() else "other"
    body = {"device_id": me["id"], "key_hash": hashlib.sha256(me["key"].encode()).hexdigest(),
            "kit": kit, "note": args.note}
    print(f"frame {me['id']} ({me.get('board', '?')}, {me.get('panel', '?')})")
    if args.dry_run:
        return
    req = urllib.request.Request(f"{args.host}/_admin/kit", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {admin_token()}",
                                          "Content-Type": "application/json",
                                          "User-Agent": "featherframe-register-kit/1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        print(json.loads(r.read()).get("result", "registered"))


if __name__ == "__main__":
    sys.exit(main())
