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
