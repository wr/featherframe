#!/usr/bin/env python3
"""Serve the setup portal's pages on this machine, as a kit would (W-901).

The portal is WiFiManager's pages with our head (firmware/src/ff_portal.h).
This compiles that header and WiFiManager's own strings on the host, builds
each page the way WiFiManager does, and serves them on localhost, so a change
to the portal can be looked at in a browser at phone width without flashing a
kit. /ffstate answers "joining", then "joined" a few seconds after a Save.

    server/.venv/bin/python firmware/tools/portal_preview.py            # :8199
    server/.venv/bin/python firmware/tools/portal_preview.py --self     # a build without FF_HOSTED_DEFAULT

The frame's state is picked on the index, /_: a new frame (no network saved,
opens on the Wi-Fi list), a frame that knows its network (the KEY2 hold), or
one whose last join failed. Needs a C++ compiler and WiFiManager's sources from
any `pio run` (firmware/.pio/libdeps/*/WiFiManager, here or in the main
checkout of a worktree), or --wm DIR.
"""
from __future__ import annotations

import argparse
import glob
import http.server
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
FIRMWARE = os.path.abspath(os.path.join(HERE, ".."))

# WiFiManager's strings the pages below are built from, and ours.
WM_NAMES = [
    "HTTP_HEAD_START", "HTTP_SCRIPT", "HTTP_STYLE", "HTTP_HEAD_END", "HTTP_ROOT_MAIN",
    "HTTP_ITEM_QI", "HTTP_ITEM_QP", "HTTP_ITEM", "HTTP_FORM_START", "HTTP_FORM_WIFI",
    "HTTP_FORM_WIFI_END", "HTTP_FORM_PARAM_HEAD", "HTTP_FORM_LABEL", "HTTP_FORM_PARAM",
    "HTTP_FORM_END", "HTTP_SCAN_LINK", "HTTP_SAVED", "HTTP_PARAMSAVED", "HTTP_END",
    "HTTP_UPDATE", "HTTP_STATUS_ON", "HTTP_STATUS_OFF", "HTTP_STATUS_OFFPW",
    "HTTP_STATUS_NONE", "HTTP_INFO_esphead", "HTTP_INFO_chiprev", "HTTP_INFO_psrsize",
    "HTTP_INFO_freeheap", "S_titlewifi", "S_titlewifisaved", "S_titlewifisettings",
    "S_titleinfo", "S_titleexit", "S_exiting", "S_passph", "S_nonetworks",
]
FF_NAMES = ["PORTAL_CSS", "FF_PORTAL_BODY_HEADER", "FF_PORTAL_NEW_FRAME_HEAD", "FF_PORTAL_MENU_HTML"]

# What a scan finds: SSID, signal quality (%), secured.
NETWORKS = [
    ("Unicorns", 100, True), ("iot", 92, True), ("ENVOY_003697", 58, False),
    ("Maui-2.4", 46, True), ("The Riley household upstairs extender 5G", 40, True),
    ("Briggs", 30, True), ("Hotspot5471", 22, True),
]
TITLE, AP = "Featherframe", "Featherframe-Setup"
SSID, IP = "Unicorns", "10.0.1.52"
CLOUD = "https://cloud.featherframe.app"
JOIN_S = 4.0


def find_wm(arg: str | None) -> str:
    if arg:
        return arg
    roots = [FIRMWARE]
    try:  # a worktree: the main checkout's .pio has the libraries
        common = subprocess.check_output(["git", "rev-parse", "--git-common-dir"], cwd=FIRMWARE, text=True).strip()
        roots.append(os.path.join(os.path.dirname(os.path.abspath(os.path.join(FIRMWARE, common))), "firmware"))
    except (OSError, subprocess.CalledProcessError):
        pass
    for root in roots:
        hits = sorted(glob.glob(os.path.join(root, ".pio", "libdeps", "*", "WiFiManager", "wm_strings_en.h")))
        if hits:
            return os.path.dirname(hits[0])
    sys.exit("WiFiManager not found: run `pio run` in firmware/ once, or pass --wm DIR")


def extract(wm_dir: str, hosted: bool) -> dict[str, str]:
    """Every string, as the compiler joins it: run a tiny host program that prints them."""
    names = WM_NAMES + FF_NAMES
    # Just enough of the ESP32 core for WiFiManager's headers to compile.
    src = ["#define PROGMEM", "#define ESP32 1", "#include <cstdio>", "#include <cstdint>",
           "typedef const char* PGM_P;",
           "struct wifi_country_t { char cc[3]; uint8_t schan, nchan; int8_t max_tx_power; int policy; };",
           "#define CONFIG_ESP32_PHY_MAX_WIFI_TX_POWER 20", "#define WIFI_COUNTRY_POLICY_AUTO 0",
           '#include "wm_strings_en.h"', '#include "ff_portal.h"',
           "static void put(const char* n, const char* v)"
           "{ fputs(n, stdout); fputc(1, stdout); fputs(v, stdout); fputc(0, stdout); }",
           "int main() {"]
    src += [f'  put("{n}", {n});' for n in names]
    src += ['  for (int i = 0; i < 10; i++) { char k[8]; snprintf(k, sizeof k, "MENU%d", i); put(k, HTTP_PORTAL_MENU[i]); }',
            "  return 0;", "}"]
    with tempfile.TemporaryDirectory() as tmp:
        cpp, exe = os.path.join(tmp, "p.cpp"), os.path.join(tmp, "p")
        with open(cpp, "w") as f:
            f.write("\n".join(src) + "\n")
        cmd = ["c++", "-std=c++17", "-w", "-I", wm_dir, "-I", os.path.join(FIRMWARE, "src"), cpp, "-o", exe]
        if hosted:
            cmd.insert(1, "-DFF_HOSTED_DEFAULT")
        subprocess.run(cmd, check=True)
        out = subprocess.run([exe], check=True, capture_output=True).stdout.decode("utf-8")
    return dict(rec.split("\x01", 1) for rec in out.split("\x00") if rec)


class Portal:
    """The pages, built the way WiFiManager 2.0.x builds them (WiFiManager.cpp)."""

    def __init__(self, s: dict[str, str]):
        self.s = s
        self.mode = "new"          # new | known | failed
        self.saved_at = 0.0

    @property
    def known(self) -> bool:
        return self.mode != "new"

    def head(self, title: str, cls: str) -> str:
        s = self.s
        custom = s["PORTAL_CSS"] + ("" if self.known else s["FF_PORTAL_NEW_FRAME_HEAD"])
        return (s["HTTP_HEAD_START"].replace("{v}", title) + s["HTTP_SCRIPT"] + s["HTTP_STYLE"] + custom
                + s["HTTP_HEAD_END"].replace("{c}", cls) + s["FF_PORTAL_BODY_HEADER"])

    def status(self) -> str:
        s = self.s
        if not self.known:
            return s["HTTP_STATUS_NONE"]
        if self.mode == "known":
            return s["HTTP_STATUS_ON"].replace("{i}", IP).replace("{v}", SSID)
        return s["HTTP_STATUS_OFF"].replace("{c}", "D").replace("{v}", SSID).replace("{r}", s["HTTP_STATUS_OFFPW"])

    def root_main(self) -> str:
        return self.s["HTTP_ROOT_MAIN"].replace("{t}", TITLE).replace("{v}", AP)

    def home(self) -> str:
        s = self.s
        # main.cpp's menus: a frame that knows its network gets Configure WiFi
        # without the scan (its own HTML); a new one the list.
        ids = ["custom", 2, 6, 9, 8] if self.known else [0, 2, 6, 9, 8]
        menu = "".join(s["FF_PORTAL_MENU_HTML"] if i == "custom" else s[f"MENU{i}"] for i in ids)
        return self.head(TITLE, "home") + self.root_main() + menu + self.status() + s["HTTP_END"]

    def scan(self) -> str:
        s = self.s
        item = (s["HTTP_ITEM"].replace("{qp}", s["HTTP_ITEM_QP"]).replace("{h}", "h", 1)
                .replace("{qi}", s["HTTP_ITEM_QI"]).replace("{h}", ""))
        out = ""
        for ssid, perc, secured in NETWORKS:
            q = perc * 3 // 100 + 1        # WiFiManager: round(map(perc, 0, 100, 1, 4))
            out += (item.replace("{V}", ssid).replace("{v}", ssid).replace("{r}", str(perc))
                    .replace("{q}", str(q)).replace("{i}", "l" if secured else ""))
        return out + "<br/>"

    def wifi(self, scan: bool) -> str:
        s = self.s
        page = self.head(s["S_titlewifi"], "wifi")
        if scan:
            page += self.scan()
        page += s["HTTP_FORM_START"].replace("{v}", "wifisave")
        page += s["HTTP_FORM_WIFI"].replace("{v}", SSID if self.known else "").replace(
            "{p}", s["S_passph"] if self.known else "")
        page += s["HTTP_FORM_WIFI_END"] + s["HTTP_FORM_PARAM_HEAD"]
        value = CLOUD if self.known else ""
        page += (s["HTTP_FORM_LABEL"] + s["HTTP_FORM_PARAM"]).replace("{i}", "server").replace(
            "{n}", "server").replace("{t}", "Server").replace("{l}", "128").replace("{v}", value).replace("{c}", "")
        page += s["HTTP_FORM_END"] + s["HTTP_SCAN_LINK"]
        return page + self.status() + s["HTTP_END"]

    def wifisave(self, ssid: str) -> str:
        s = self.s
        self.saved_at = time.time()
        if ssid:
            return self.head(s["S_titlewifisaved"], "wifi") + s["HTTP_SAVED"] + s["HTTP_END"]
        return self.head(s["S_titlewifisettings"], "wifi") + s["HTTP_PARAMSAVED"] + s["HTTP_END"]

    def info(self) -> str:
        s = self.s
        page = self.head(s["S_titleinfo"], "info") + self.status()
        page += s["HTTP_INFO_esphead"] + s["HTTP_INFO_chiprev"].replace("{1}", "0")
        page += s["HTTP_INFO_psrsize"].replace("{1}", "8386295") + s["HTTP_INFO_freeheap"].replace("{1}", "171204")
        page += "</dl><h3>About</h3><hr><dl><dt>WiFiManager</dt><dd>v2.0.17</dd></dl>"
        return page + s["MENU8"] + s["MENU9"] + s["HTTP_END"]

    def exit(self) -> str:
        s = self.s
        return self.head(s["S_titleexit"], "exit") + s["S_exiting"] + s["HTTP_END"]

    def update(self) -> str:
        s = self.s
        return self.head(TITLE, "update") + self.root_main() + s["HTTP_UPDATE"] + s["HTTP_END"]

    def ffstate(self) -> str:
        joined = self.saved_at and time.time() - self.saved_at > JOIN_S and self.mode != "failed"
        return "joined" if joined else "joining"

    def index(self) -> str:
        links = [("/_mode?m=new", "New frame: opens on the Wi-Fi list"),
                 ("/_mode?m=known", "Knows its network (KEY2 hold): menu"),
                 ("/_mode?m=failed", "Last join failed: menu"),
                 ("/wifi", "Wi-Fi list"), ("/0wifi", "Wi-Fi form, no scan"), ("/info", "Info"),
                 ("/update", "Update"), ("/exit", "Exit")]
        rows = "".join(f'<li><a href="{h}">{t}</a></li>' for h, t in links)
        return (f"<!doctype html><meta name=viewport content='width=device-width'><title>Portal preview</title>"
                f"<p>Mode: <b>{self.mode}</b></p><ul>{rows}</ul>")


def serve(portal: Portal, port: int) -> None:
    class Handler(http.server.BaseHTTPRequestHandler):
        def send(self, body: str, ctype: str = "text/html; charset=utf-8", code: int = 200):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def redirect(self, to: str):
            self.send_response(302)
            self.send_header("Location", to)
            self.end_headers()

        def route(self, form: dict[str, list[str]]):
            url = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(url.query)
            p = url.path
            if p == "/_":
                return self.send(portal.index())
            if p == "/_mode":
                portal.mode = q.get("m", ["new"])[0]
                portal.saved_at = 0
                return self.redirect("/")
            pages = {"/": portal.home, "/wifi": lambda: portal.wifi(True), "/0wifi": lambda: portal.wifi(False),
                     "/info": portal.info, "/exit": portal.exit, "/update": portal.update}
            if p in pages:
                return self.send(pages[p]())
            if p == "/wifisave":
                return self.send(portal.wifisave(form.get("s", [""])[0]))
            if p == "/ffstate":
                return self.send(portal.ffstate(), "text/plain")
            self.send("not found", "text/plain", 404)

        def do_GET(self):
            self.route({})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            self.route(urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8")))

        def log_message(self, *a):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"portal preview on http://127.0.0.1:{port}/_ (Ctrl-C to stop)", flush=True)
    httpd.serve_forever()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8199)
    ap.add_argument("--wm", help="WiFiManager's source directory")
    ap.add_argument("--self", dest="hosted", action="store_false",
                    help="a build that starts on self-hosted (no FF_HOSTED_DEFAULT)")
    ap.add_argument("--dump", metavar="DIR", help="write each page to DIR instead of serving")
    args = ap.parse_args()
    portal = Portal(extract(find_wm(args.wm), args.hosted))
    if args.dump:
        os.makedirs(args.dump, exist_ok=True)
        for mode in ("new", "known", "failed"):
            portal.mode = mode
            for name, page in (("home", portal.home()), ("wifi", portal.wifi(True)), ("0wifi", portal.wifi(False))):
                with open(os.path.join(args.dump, f"{mode}-{name}.html"), "w") as f:
                    f.write(page)
        return
    serve(portal, args.port)


if __name__ == "__main__":
    main()
