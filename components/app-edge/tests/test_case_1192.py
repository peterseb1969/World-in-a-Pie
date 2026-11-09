"""CASE-1192 — the umbrella review's low findings, app-edge side.

- The host the install runs on is blocked: on compose the container's
  default gateway is the host, which publishes the front door's port, so an
  upstream there loops back through it.
- An install host that stops resolving keeps blocking its last addresses:
  a DNS hiccup (a container being recreated drops out of DNS) never lets an
  IP-literal upstream reach the install.
- An IPv6-literal upstream's Host header carries brackets.
"""

from __future__ import annotations

import ipaddress

import pytest

import edge_sync as es

from .test_edge_sync import SETTINGS, block, rec

DNS = {"wip-registry": {"10.89.0.5"}, "aa.apps.example": {"203.0.113.10"}}


@pytest.fixture(autouse=True)
def both_families(monkeypatch):
    import socket
    monkeypatch.setattr(es, "reachable_families", lambda: {socket.AF_INET, socket.AF_INET6})


@pytest.fixture
def dns(monkeypatch):
    table = {k: set(v) for k, v in DNS.items()}

    def resolve(host):
        try:
            return {ipaddress.ip_address(host)}
        except ValueError:
            return {ipaddress.ip_address(a) for a in table.get(host, set())}

    monkeypatch.setattr(es, "resolve", resolve)
    return table


def test_an_upstream_on_the_host_is_refused(dns):
    host = {ipaddress.ip_address("10.89.0.1")}
    p = es.plan([rec(upstream="https://10.89.0.1:8443")], [], host_addresses=host)
    assert p.skipped == {"aa": "10.89.0.1 resolves to 10.89.0.1, the host the install runs on"}
    assert not p.targets


def test_the_host_address_is_the_default_gateway(tmp_path):
    route = tmp_path / "route"
    route.write_text(
        "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
        "eth0\t00000000\t0100590A\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
        "eth0\t0000590A\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n"
    )
    assert es.host_addresses(str(route)) == {ipaddress.ip_address("10.89.0.1")}
    assert es.host_addresses(str(tmp_path / "absent")) == set()


def test_an_install_host_that_stops_resolving_keeps_blocking(dns):
    known: dict = {}
    first = es.plan([rec(upstream="https://aa.apps.example")], ["wip-registry"], known=known)
    assert first.targets and known == {"wip-registry": {ipaddress.ip_address("10.89.0.5")}}

    dns.pop("wip-registry")  # recreated: out of DNS for a moment
    p = es.plan([rec(upstream="https://10.89.0.5")], ["wip-registry"], known=known)
    assert p.skipped == {"aa": "10.89.0.5 resolves to 10.89.0.5, the install's own host wip-registry"}


def test_a_name_that_resolves_again_replaces_its_old_addresses(dns):
    known = {"wip-registry": {ipaddress.ip_address("10.89.0.9")}}
    p = es.plan([rec(upstream="https://10.89.0.9")], ["wip-registry"], known=known)
    assert p.targets  # 10.89.0.9 is no longer the registry's
    assert known == {"wip-registry": {ipaddress.ip_address("10.89.0.5")}}


def test_an_ipv6_literal_host_header_is_bracketed(dns):
    p = es.plan([rec(upstream="https://[2001:db8::10]:9443", upstream_host_header=True)], [])
    assert "header_up Host [2001:db8::10]:9443" in block(es.caddyfile(p, SETTINGS), "@t0")


def test_a_default_port_ipv6_host_header_has_no_port(dns):
    p = es.plan([rec(upstream="https://[2001:db8::10]", upstream_host_header=True)], [])
    assert "header_up Host [2001:db8::10]\n" in block(es.caddyfile(p, SETTINGS), "@t0") + "\n"
