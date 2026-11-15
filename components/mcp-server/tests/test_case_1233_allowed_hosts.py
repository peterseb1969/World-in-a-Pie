"""CASE-1233: the network transport's Host allow-list holds both names a proxy
dials — the short container name (same-network clients, the router) and the
name the public edge dials (the Service FQDN on k8s) — and nothing else."""

from __future__ import annotations

import pytest
from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings

from wip_mcp.server import allow_hosts, allowed_hosts_from_env

FQDN = "wip-mcp-server.wip.svc.cluster.local"


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    def set_env(short: str | None, service: str | None) -> list[str]:
        for var, value in (("MCP_ALLOWED_HOST", short), ("MCP_ALLOWED_SERVICE_HOST", service)):
            if value is None:
                monkeypatch.delenv(var, raising=False)
            else:
                monkeypatch.setenv(var, value)
        return allowed_hosts_from_env()
    return set_env


def test_both_names_are_read_in_order_and_once(env) -> None:
    assert env("wip-mcp-server", FQDN) == ["wip-mcp-server", FQDN]
    assert env("wip-mcp-server", "wip-mcp-server") == ["wip-mcp-server"]  # compose: the same name
    assert env(None, FQDN) == [FQDN]
    assert env("  ", None) == []


def test_the_sdk_accepts_each_name_with_any_port_and_refuses_the_rest(env) -> None:
    settings = TransportSecuritySettings(enable_dns_rebinding_protection=True)
    allow_hosts(settings, env("wip-mcp-server", FQDN))
    check = TransportSecurityMiddleware(settings)._validate_host
    for host in ("wip-mcp-server", "wip-mcp-server:8007", FQDN, f"{FQDN}:8007"):
        assert check(host), host
    # What a proxy forwarding the client's Host would send.
    for host in ("wip-router:8080", "wip.example.org", "wip-mcp-server.evil:8007"):
        assert not check(host), host
