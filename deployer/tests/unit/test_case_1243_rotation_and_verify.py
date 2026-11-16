"""The post-rotation probe tells a certificate that does not verify from a TLS
hiccup, and keeps an answer it already has; `verify --live` reports a cluster
it cannot select instead of exiting before the report.

- Only a certificate verification failure means the probe could not ask. Any
  other ssl error (a connection torn down mid-handshake while services
  restart) is a transient failure, retried like a refused connection.
- When the new key was already refused, the old key's probe failing on the
  certificate does not turn that answer into "not checked".
- `verify --security --live` on a k8s install whose recorded cluster cannot be
  selected reports the live checks as not run, with every other check, and
  exits 1 like any other failed check, instead of exiting 2 with no report.
"""

from __future__ import annotations

import ssl
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from wip_deploy import kube
from wip_deploy.cli import app
from wip_deploy.verify_security import RotationNotChecked, check_key_rotation

from .test_case_1243_tls import _deployment
from .test_verify_live_k8s_cluster import _k8s_install


def _ssl_failure(request: httpx.Request, error: ssl.SSLError) -> httpx.Response:
    try:
        raise error
    except ssl.SSLError as e:
        raise httpx.ConnectError("tls", request=request) from e


def test_a_transient_ssl_error_is_retried_not_a_skip() -> None:
    """An SSLEOFError while the install restarts, then the new key answers."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return _ssl_failure(request, ssl.SSLEOFError("EOF occurred in violation of protocol"))
        return httpx.Response(200 if request.headers["X-API-Key"] == "new" else 401)

    problems = check_key_rotation(
        _deployment(), "new", "old", transport=httpx.MockTransport(handler),
        attempts=3, delay_seconds=0,
    )
    assert problems == []
    assert calls["n"] == 3  # the new key twice (one retry), the old key once


def test_a_refused_new_key_survives_an_unverifiable_old_key_probe() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["X-API-Key"] == "new":
            return httpx.Response(401)
        return _ssl_failure(request, ssl.SSLCertVerificationError("certificate verify failed"))

    problems = check_key_rotation(
        _deployment(), "new", "old", transport=httpx.MockTransport(handler),
        attempts=1, delay_seconds=0,
    )
    assert any("new key is not accepted" in p for p in problems)
    assert any("old key" in p and "not checked" in p for p in problems)


def test_an_unverifiable_certificate_alone_is_still_not_checked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ssl_failure(request, ssl.SSLCertVerificationError("certificate verify failed"))

    with pytest.raises(RotationNotChecked):
        check_key_rotation(
            _deployment(), "new", "old", transport=httpx.MockTransport(handler),
            attempts=1, delay_seconds=0,
        )


def test_live_verify_reports_a_cluster_it_cannot_select(tmp_path: Path, monkeypatch) -> None:
    _k8s_install(tmp_path)

    def refuse(*_a, **_k):
        raise kube.KubeError("the context 'unit-test' points at another cluster")

    kube.reset_for_tests()
    monkeypatch.setattr(kube, "select", refuse)
    result = CliRunner().invoke(
        app, ["verify", "--security", "--live", "--install-dir", str(tmp_path)]
    )

    assert result.exit_code == 1, result.output
    assert "Security verification" in result.output  # the report was printed
    assert "live checks" in result.output and "another cluster" in result.output
