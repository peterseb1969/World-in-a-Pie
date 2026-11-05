"""The gateway hands apps the user's identity, never an API key (CASE-1050).

The auth gateway's verify answer used to carry the install's master key as
X-API-Key, and every renderer copied it onto every protected route — so each
app behind the umbrella received the master key with every user request.
Now the copied set is the user's identity only (routing.
GATEWAY_IDENTITY_HEADERS), the gateway holds no key, and app routes remove
the client-sent identity headers the gateway's answer does not overwrite, so
an app never reads a user the browser made up.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from wip_deploy.config_gen.routing import (
    CLIENT_IDENTITY_HEADERS,
    GATEWAY_IDENTITY_HEADERS,
    ResolvedRoute,
)
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_compose, render_k8s

from .test_compose import REPO_ROOT, _minimal_compose
from .test_compose import _secrets as _compose_secrets
from .test_k8s import _k8s_deployment
from .test_k8s import _secrets as _k8s_secrets


@pytest.fixture(scope="module")
def real_discovery() -> Discovery:
    return discover(REPO_ROOT)


def _route(*, is_app: bool, protected: bool) -> ResolvedRoute:
    return ResolvedRoute(
        path="/apps/x", backend_component="x", backend_port=1, auth_protected=protected,
        streaming=False, strip_prefix=False, redirect_bare_path=True, is_app=is_app,
    )


def test_the_gateway_hands_on_identity_only() -> None:
    assert GATEWAY_IDENTITY_HEADERS == ("X-WIP-User", "X-WIP-Groups")
    assert "X-API-Key" not in CLIENT_IDENTITY_HEADERS


def test_which_client_headers_a_route_strips() -> None:
    # A platform route is left alone: services authenticate the key they get.
    assert _route(is_app=False, protected=False).client_identity_headers_to_strip() == ()
    # A protected app route: the gateway's answer overwrites its two headers,
    # the rest it never supplies, so those go.
    assert _route(is_app=True, protected=True).client_identity_headers_to_strip() == (
        "X-WIP-Auth-Method",
    )
    # An unprotected app route: nothing overwrites anything.
    assert _route(is_app=True, protected=False).client_identity_headers_to_strip() == (
        CLIENT_IDENTITY_HEADERS
    )


def test_the_gateway_manifest_holds_no_key(real_discovery: Discovery) -> None:
    gateway = next(c for c in real_discovery.components if c.metadata.name == "auth-gateway")
    env = [*gateway.spec.env.required, *gateway.spec.env.optional]
    assert "API_KEY" not in {e.name for e in env}
    assert all(getattr(e.source, "from_secret", None) != "api-key" for e in env)


# ── compose (Caddy) ───────────────────────────────────────────────────────


def _block(caddyfile: str, matcher: str) -> str:
    start = caddyfile.index(f"    handle {matcher} {{\n")
    return caddyfile[start:caddyfile.index("\n    }\n", start)]


@pytest.fixture(scope="module")
def caddyfile(tmp_path_factory: pytest.TempPathFactory, real_discovery: Discovery) -> str:
    tmp = tmp_path_factory.mktemp("caddy")
    d = _minimal_compose(apps=["wip-kb"])
    s = _compose_secrets(tmp, d, real_discovery)
    tree = render_compose(d, real_discovery.components, real_discovery.apps, s)
    return tree.files[Path("config/caddy/Caddyfile")].content


def test_caddy_copies_identity_only(caddyfile: str) -> None:
    copies = [line.strip() for line in caddyfile.splitlines() if "copy_headers" in line]
    assert copies
    assert set(copies) == {"copy_headers X-WIP-User X-WIP-Groups"}


def test_caddy_strips_client_identity_on_an_unprotected_app_route(caddyfile: str) -> None:
    block = _block(caddyfile, "/apps/kb/server-api/kb/*")
    assert "forward_auth" not in block
    for header in CLIENT_IDENTITY_HEADERS:
        assert f"request_header -{header}\n" in block


def test_caddy_protected_app_route_strips_only_what_the_gateway_does_not_set(caddyfile: str) -> None:
    block = _block(caddyfile, "/apps/kb/*")
    assert "forward_auth" in block
    assert "request_header -X-WIP-Auth-Method\n" in block
    # Removing the gateway's own headers here would delete them: Caddy runs
    # request_header after forward_auth's copy.
    assert "request_header -X-WIP-User" not in block
    assert "request_header -X-WIP-Groups" not in block


def test_caddy_leaves_platform_routes_alone(caddyfile: str) -> None:
    assert "request_header" not in _block(caddyfile, "/api/registry/*")


# ── k8s (NGINX Ingress) ───────────────────────────────────────────────────


def test_k8s_copies_identity_only(tmp_path: Path, real_discovery: Discovery) -> None:
    """On k8s too the front door is wip-caddy: every protected route copies
    exactly the gateway's two identity headers, never X-API-Key."""
    d = _k8s_deployment(apps=["wip-kb", "react-console"])
    s = _k8s_secrets(tmp_path, d, real_discovery)
    tree = render_k8s(d, real_discovery.components, real_discovery.apps, s)
    docs = yaml.safe_load_all(tree.files[Path("services/caddy.yaml")].content)
    caddyfile = next(x for x in docs if x["kind"] == "ConfigMap")["data"]["Caddyfile"]
    copied = {line.strip() for line in caddyfile.splitlines() if "copy_headers" in line}
    assert copied == {"copy_headers X-WIP-User X-WIP-Groups"}
    assert "X-API-Key" not in caddyfile
