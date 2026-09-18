"""mDNS advertising (W-763): soft-fails, and never touches the network in tests."""
from __future__ import annotations

import socket

import pytest

from featherframe import discovery


def test_lan_ip_is_an_ipv4_or_none():
    ip = discovery.lan_ip()
    if ip is not None:
        socket.inet_aton(ip)  # raises on garbage


def test_disabled_by_env(monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_MDNS", "0")
    adv = discovery.Advertiser(port=8080)
    assert adv.start() is False
    assert adv.advertised is False
    assert "disabled" in adv.status()["error"]
    adv.stop()  # no-op, must not raise


def test_missing_zeroconf_soft_fails(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "zeroconf":
            raise ImportError("no zeroconf here")
        return real_import(name, *a, **k)

    monkeypatch.setenv("FEATHERFRAME_MDNS", "1")
    monkeypatch.setattr(builtins, "__import__", fake_import)
    adv = discovery.Advertiser(port=8080)
    assert adv.start() is False
    assert adv.advertised is False
    assert "zeroconf" in adv.status()["error"]


def test_registers_with_a_fake_zeroconf(monkeypatch):
    """The record carries our type, the bound port, and the LAN address."""
    import sys
    import types

    calls = {}

    class FakeInfo:
        def __init__(self, type_, name, addresses, port, properties, server):
            calls["info"] = dict(type=type_, name=name, addresses=addresses,
                                 port=port, properties=properties, server=server)

    class FakeZeroconf:
        def register_service(self, info):
            calls["registered"] = info

        def unregister_service(self, info):
            calls["unregistered"] = info

        def close(self):
            calls["closed"] = True

    fake = types.ModuleType("zeroconf")
    fake.ServiceInfo, fake.Zeroconf = FakeInfo, FakeZeroconf
    monkeypatch.setitem(sys.modules, "zeroconf", fake)
    monkeypatch.setenv("FEATHERFRAME_MDNS", "1")
    monkeypatch.setattr(discovery, "lan_ip", lambda: "192.0.2.7")

    adv = discovery.Advertiser(port=8081, version="1.0.0", panel="ee02")
    assert adv.start() is True
    assert adv.advertised is True
    info = calls["info"]
    assert info["properties"]["panel"] == "ee02"     # a frame adopts only its own panel's server
    assert info["type"] == "_featherframe._tcp.local."
    assert info["name"].endswith("._featherframe._tcp.local.")
    assert info["port"] == 8081
    assert info["addresses"] == [socket.inet_aton("192.0.2.7")]
    assert info["properties"]["path"] == "/api/frame"
    st = adv.status()
    assert st == {"advertised": True, "service": "_featherframe._tcp.local",
                  "ip": "192.0.2.7", "port": 8081, "panel": "ee02", "error": None}

    adv.stop()
    assert calls["unregistered"] is calls["registered"]
    assert calls["closed"] is True
    assert adv.advertised is False


@pytest.mark.parametrize("value", ["0", "no", "off", "false", " 0 "])
def test_enabled_env_spellings(monkeypatch, value):
    monkeypatch.setenv("FEATHERFRAME_MDNS", value)
    assert discovery.enabled() is False
