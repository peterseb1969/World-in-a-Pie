"""CASE-695 — verify --security resolves a public-shaped hostname before
flagging tls=internal, so a name that resolves only into private space
(VPN/LAN) is not a false positive.

Resolution lives ONLY in the verify check; the shape-only
`is_public_hostname` (shared with the spec's letsencrypt gate) is
unchanged, so spec validation stays offline/deterministic.
"""

from __future__ import annotations

import socket

import pytest

from wip_deploy.spec.deployment import NetworkSpec
from wip_deploy.verify_security import (
    _resolves_private_only,
    check_tls_hostname_sanity,
    is_public_hostname,
)


def _fake_getaddrinfo(addrs: list[str]):
    def _inner(host, *a, **k):
        return [(socket.AF_INET, None, None, "", (ip, 0)) for ip in addrs]

    return _inner


def _raise_gaierror(host, *a, **k):
    raise socket.gaierror("name resolution failed")


class TestResolvesPrivateOnly:
    def test_all_private(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            socket, "getaddrinfo", _fake_getaddrinfo(["10.8.69.1", "192.168.1.4"])
        )
        assert _resolves_private_only("host") is True

    def test_cgnat_counts_as_private(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 100.64.0.0/10 — Tailscale/WireGuard; not internet-routable.
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(["100.100.1.1"]))
        assert _resolves_private_only("host") is True

    def test_any_global_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            socket, "getaddrinfo", _fake_getaddrinfo(["10.0.0.1", "93.184.216.34"])
        )
        assert _resolves_private_only("host") is False

    def test_unresolvable_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", _raise_gaierror)
        assert _resolves_private_only("host") is None


class TestTlsHostnameSanity:
    def _net(self) -> NetworkSpec:
        # public-SHAPED name so the resolution branch is exercised
        return NetworkSpec(hostname="cloud.example.com", tls="internal")

    def test_private_resolving_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(["10.8.69.1"]))
        r = check_tls_hostname_sanity(self._net())
        assert r.passed
        assert "resolves privately" in r.message

    def test_global_resolving_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(["93.184.216.34"]))
        r = check_tls_hostname_sanity(self._net())
        assert not r.passed

    def test_unresolvable_fails_toward_caution(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", _raise_gaierror)
        r = check_tls_hostname_sanity(self._net())
        assert not r.passed
        assert "could not resolve" in r.message

    def test_shape_classifier_unchanged(self) -> None:
        # is_public_hostname must remain resolution-free (offline).
        assert is_public_hostname("cloud.example.com")
        assert not is_public_hostname("kb.internal")
        assert not is_public_hostname("localhost")
