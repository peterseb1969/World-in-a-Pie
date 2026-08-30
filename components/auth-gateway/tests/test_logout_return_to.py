"""Logout's return_to must never send the browser off-site.

`return_to` exists so an app can land the user back on its own page after
the session is cleared. A bare startswith("/") check admits the
protocol-relative form ("//evil.host/x"), which browsers resolve to another
origin — an open redirect on the one door every app links to.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from auth_gateway.main import _same_origin_path, app


@pytest.mark.parametrize("value", ["/apps/kb/", "/", "/x?y=1", "/a//b"])
def test_same_origin_paths_accepted(value):
    assert _same_origin_path(value)


@pytest.mark.parametrize(
    "value",
    ["//evil.host/x", "/\\evil.host", "https://evil.host/", "evil.host", "", "javascript:alert(1)"],
)
def test_off_site_and_malformed_values_rejected(value):
    assert not _same_origin_path(value)


def test_logout_protocol_relative_return_to_falls_back_to_root():
    client = TestClient(app)
    resp = client.get(
        "/auth/logout", params={"return_to": "//evil.host/x"}, follow_redirects=False
    )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert not location.startswith("//")
    assert "evil.host" not in location


def test_logout_same_origin_return_to_honored():
    client = TestClient(app)
    resp = client.get(
        "/auth/logout", params={"return_to": "/apps/kb/"}, follow_redirects=False
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/apps/kb/"
