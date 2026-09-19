"""mDNS advertisement, so a frame finds its server without a typed IP.

The server registers ``_featherframe._tcp`` on the LAN; the firmware queries
that service on first boot and whenever its stored URL stops answering, then
adopts whatever address and port it finds. This is the whole reason a kit can
ship with no server address baked in.

Everything here soft-fails: a missing ``zeroconf`` package, a box with no
route, or a multicast-hostile network only logs a warning. The dashboard and
the frame's typed URL keep working without it.

Set ``FEATHERFRAME_MDNS=0`` to turn advertising off.
"""
from __future__ import annotations

import logging
import os
import socket
from typing import Optional

log = logging.getLogger("featherframe.discovery")

SERVICE_TYPE = "_featherframe._tcp.local."


def lan_ip() -> Optional[str]:
    """The address a LAN peer would reach us on. No packet is sent: a UDP
    connect() only picks the outbound interface."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None


def enabled() -> bool:
    return os.environ.get("FEATHERFRAME_MDNS", "1").strip().lower() not in ("0", "no", "off", "false")


class Advertiser:
    """Registers (and on stop, unregisters) the service record."""

    def __init__(self, port: int, version: str = "", panel: str = "") -> None:
        self.port = int(port)
        self.version = version
        # TXT "panel": a frame only adopts a server that draws for its panel
        # (two instances on one LAN: the gray wall frame and the colour one).
        self.panel = panel
        self.ip: Optional[str] = None
        self.name: Optional[str] = None
        self.error: Optional[str] = None
        self._zc = None
        self._info = None

    @property
    def advertised(self) -> bool:
        return self._info is not None

    def start(self) -> bool:
        if not enabled():
            self.error = "disabled (FEATHERFRAME_MDNS=0)"
            log.info("mDNS advertising disabled")
            return False
        try:
            from zeroconf import ServiceInfo, Zeroconf  # optional dependency
        except Exception as e:  # pragma: no cover - depends on the environment
            self.error = f"zeroconf not installed ({e.__class__.__name__})"
            log.warning("mDNS off: %s", self.error)
            return False
        ip = lan_ip()
        if not ip:
            self.error = "no LAN address"
            log.warning("mDNS off: %s", self.error)
            return False
        host = socket.gethostname().split(".")[0] or "featherframe"
        # The port keeps two instances on one host from colliding on the name.
        name = f"Featherframe on {host}:{self.port}.{SERVICE_TYPE}"
        try:
            info = self._make_info(ServiceInfo, name, ip, host)
            zc = Zeroconf()
            zc.register_service(info)
        except Exception as e:
            self.error = f"{e.__class__.__name__}: {e}"
            log.warning("mDNS registration failed: %s", self.error)
            return False
        self._zc, self._info, self.ip, self.name, self.error = zc, info, ip, name, None
        log.info("mDNS: advertising %s at %s:%d", SERVICE_TYPE.rstrip("."), ip, self.port)
        return True

    def _make_info(self, ServiceInfo, name: str, ip: str, host: str):
        return ServiceInfo(
            SERVICE_TYPE, name,
            addresses=[socket.inet_aton(ip)], port=self.port,
            properties={"path": "/api/frame", "version": self.version, "panel": self.panel},
            server=f"{host}.local.",
        )

    def set_panel(self, panel: str) -> None:
        """Re-announce with a new TXT panel (the instance switched panels)."""
        if panel == self.panel:
            return
        self.panel = panel
        if self._zc is None or self._info is None or not self.ip or not self.name:
            return
        try:
            from zeroconf import ServiceInfo
            host = socket.gethostname().split(".")[0] or "featherframe"
            info = self._make_info(ServiceInfo, self.name, self.ip, host)
            self._zc.update_service(info)
            self._info = info
            log.info("mDNS: now advertising panel=%s", panel)
        except Exception as e:  # noqa: BLE001 — discovery is a convenience
            log.warning("mDNS panel update failed: %s", e)

    # -- peers ---------------------------------------------------------------
    # Two instances on one LAN (one per frame). A frame that lands on the wrong
    # one is parked there (403); this lets that 403 say where the instance for
    # the frame's panel is. The frame cannot always work it out itself: the
    # ESP's one-shot mDNS query has been seen to return only one of two
    # instances advertised from the same host.
    _PEER_TTL = 120.0

    def find_peer(self, panel: str, wait: float = 2.0) -> Optional[str]:
        """"http://ip:port" of ANOTHER instance advertising `panel`, or None."""
        import time
        if not panel or self._zc is None:
            return None
        cache = getattr(self, "_peer_cache", None)
        if cache is None:
            cache = self._peer_cache = {}
        hit = cache.get(panel)
        if hit and time.monotonic() - hit[0] < self._PEER_TTL:
            return hit[1]
        url = None
        try:
            from zeroconf import ServiceBrowser

            names: list[str] = []

            class _Listener:
                def add_service(self, zc, type_, name):
                    names.append(name)

                def update_service(self, zc, type_, name):
                    pass

                def remove_service(self, zc, type_, name):
                    pass

            browser = ServiceBrowser(self._zc, SERVICE_TYPE, _Listener())
            time.sleep(wait)
            browser.cancel()
            for name in names:
                if name == self.name:
                    continue
                info = self._zc.get_service_info(SERVICE_TYPE, name, timeout=1500)
                if info is None or not info.port:
                    continue
                props = {(k.decode() if isinstance(k, bytes) else k):
                         (v.decode() if isinstance(v, bytes) else v)
                         for k, v in (info.properties or {}).items()}
                addrs = info.parsed_addresses() if hasattr(info, "parsed_addresses") else []
                if props.get("panel") == panel and addrs:
                    url = f"http://{addrs[0]}:{info.port}"
                    break
        except Exception as e:  # noqa: BLE001 — a hint, never a failure
            log.debug("peer lookup failed: %s", e)
        cache[panel] = (time.monotonic(), url)
        return url

    def stop(self) -> None:
        zc, info = self._zc, self._info
        self._zc = self._info = None
        if zc is None:
            return
        try:
            if info is not None:
                zc.unregister_service(info)
            zc.close()
        except Exception as e:  # pragma: no cover
            log.debug("mDNS stop: %s", e)

    def status(self) -> dict:
        return {"advertised": self.advertised, "service": SERVICE_TYPE.rstrip("."),
                "ip": self.ip, "port": self.port, "panel": self.panel, "error": self.error}
