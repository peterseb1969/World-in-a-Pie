"""Per-route allowed groups for gateway-protected routes (CASE-901).

A route's `allowed_groups` travels to the auth-gateway in the verify URL each
target renders — Caddy's forward_auth `uri` (compose and dev) and NGINX's
`auth-url` (k8s) — through one definition, routing.verify_query, so the
targets cannot drift. Only that route's check carries it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from wip_deploy.config_gen.routing import ResolvedRoute, verify_path, verify_query
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_compose, render_k8s
from wip_deploy.spec.component import Route

from .test_compose import REPO_ROOT, _minimal_compose
from .test_compose import _secrets as _compose_secrets
from .test_k8s import _k8s_deployment
from .test_k8s import _secrets as _k8s_secrets


@pytest.fixture(scope="module")
def real_discovery() -> Discovery:
    return discover(REPO_ROOT)


def _with_rc_groups(discovery: Discovery, groups: list[str]) -> list:
    """The discovered apps, with React Console's route limited to `groups`."""
    apps = []
    for app in discovery.apps:
        if app.metadata.name == "react-console":
            app = app.model_copy(deep=True)
            app.spec.routes = [
                r.model_copy(update={"allowed_groups": groups}) if r.auth_required else r
                for r in app.spec.routes
            ]
        apps.append(app)
    return apps


# ── spec and routing ──────────────────────────────────────────────────────


def test_groups_require_an_authenticated_route() -> None:
    with pytest.raises(ValidationError, match="requires auth_required"):
        Route(path="/apps/x", auth_required=False, allowed_groups=["wip-admins"])


@pytest.mark.parametrize("bad", ["", "wip admins", "a&b", "x\ny", "grp}"])
def test_group_names_must_be_url_and_caddyfile_safe(bad) -> None:
    with pytest.raises(ValidationError, match="allowed_groups entry"):
        Route(path="/apps/x", allowed_groups=[bad])


def test_verify_url_shapes() -> None:
    assert verify_query(()) == ""
    assert verify_query(("wip-admins", "team.x")) == "?allowed_groups=wip-admins,team.x"
    route = ResolvedRoute(
        path="/apps/x", backend_component="x", backend_port=1, auth_protected=True,
        streaming=False, strip_prefix=False, redirect_bare_path=True,
        allowed_groups=("wip-admins",),
    )
    assert verify_path(route) == "/auth/verify?allowed_groups=wip-admins"


# ── compose (Caddy) ───────────────────────────────────────────────────────


def _caddyfile(tmp_path: Path, discovery: Discovery, groups: list[str]) -> str:
    d = _minimal_compose(apps=["react-console"])
    s = _compose_secrets(tmp_path, d, discovery)
    tree = render_compose(d, discovery.components, _with_rc_groups(discovery, groups), s)
    return tree.files[Path("config/caddy/Caddyfile")].content


def test_caddy_route_carries_its_groups(tmp_path: Path, real_discovery: Discovery) -> None:
    caddyfile = _caddyfile(tmp_path, real_discovery, ["wip-admins"])
    rc = caddyfile[caddyfile.index("handle /apps/rc/*"):][:500]
    assert "uri /auth/verify?allowed_groups=wip-admins\n" in rc
    # Only that route's check carries them (its bare-path block just
    # redirects to the trailing slash); every other protected block,
    # the catch-all included, keeps the plain URI.
    assert caddyfile.count("allowed_groups=") == 1


def test_caddy_without_groups_is_unchanged(tmp_path: Path, real_discovery: Discovery) -> None:
    caddyfile = _caddyfile(tmp_path, real_discovery, [])
    assert "allowed_groups" not in caddyfile
    assert "uri /auth/verify\n" in caddyfile


# ── k8s (NGINX Ingress) ───────────────────────────────────────────────────


def test_k8s_ingress_auth_url_carries_its_groups(tmp_path: Path, real_discovery: Discovery) -> None:
    d = _k8s_deployment(apps=["react-console"])
    s = _k8s_secrets(tmp_path, d, real_discovery)
    tree = render_k8s(d, real_discovery.components, _with_rc_groups(real_discovery, ["wip-admins"]), s)
    docs = list(yaml.safe_load_all(tree.files[Path("ingress.yaml")].content))

    rc = next(doc for doc in docs if doc["metadata"]["name"] == "react-console-ingress")
    auth_url = rc["metadata"]["annotations"]["nginx.ingress.kubernetes.io/auth-url"]
    assert auth_url.endswith("/auth/verify?allowed_groups=wip-admins")

    others = [
        doc["metadata"]["annotations"]["nginx.ingress.kubernetes.io/auth-url"]
        for doc in docs
        if doc is not rc and "nginx.ingress.kubernetes.io/auth-url" in (doc["metadata"].get("annotations") or {})
    ]
    assert all("allowed_groups" not in url for url in others)
