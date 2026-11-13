"""Every upstream a rendered Caddyfile names is a service the same render produced.

The instance (CASE-1245 item 4: a gateway-off install routing app origins at
wip-auth-gateway, which it did not render) was fixed by a guard. These pin the
class: real renders pass on every target, and a render that reopens that hole
— the guard dropped, here by forcing the gateway's routes on — is refused.
"""

from __future__ import annotations

import dataclasses
import tempfile
from pathlib import Path

import pytest

import wip_deploy.renderers.compose as compose_mod
import wip_deploy.renderers.dev_simple as dev_mod
import wip_deploy.renderers.k8s as k8s_mod
from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_compose, render_dev_simple, render_k8s
from wip_deploy.renderers.base import FileTree
from wip_deploy.renderers.upstreams import (
    UpstreamNotRendered,
    _caddyfiles,
    _host_of,
    _upstreams,
    check_caddy_upstreams,
)
from wip_deploy.secrets import ensure_secrets
from wip_deploy.secrets_backend import FileSecretBackend

REPO_ROOT = Path(__file__).resolve().parents[4]
APPS = ["wip-kb", "react-console"]
RENDER = {"compose": render_compose, "k8s": render_k8s, "dev": render_dev_simple}
MODULE = {"compose": compose_mod, "k8s": k8s_mod, "dev": dev_mod}


@pytest.fixture(scope="module")
def disc() -> Discovery:
    return discover(REPO_ROOT)


def _render(disc: Discovery, target: str, *, gateway: bool = True, add: list[str] | None = None) -> FileTree:
    auth = {} if gateway else {"auth_mode": "api-key-only", "auth_gateway": False}
    d = build_deployment(BuildInputs(
        name="t", preset="standard", target=target, hostname="wip.example.org",
        compose_data_dir=Path("/tmp/wip-1247"), k8s_namespace="wip-t", apps=APPS,
        add=add or [], dev_mode="simple", apps_from_registry=APPS if target == "dev" else [],
        **auth,
    ))
    store = Path(tempfile.mkdtemp())
    d.spec.secrets.location = str(store)
    s = ensure_secrets(d, disc.components, disc.apps, FileSecretBackend(store))
    kw = {"repo_root": REPO_ROOT} if target == "dev" else {}
    return RENDER[target](d, disc.components, disc.apps, s, **kw)


@pytest.mark.parametrize("target", ["compose", "k8s", "dev"])
@pytest.mark.parametrize("add", [[], ["app-edge"]])
def test_a_real_render_routes_only_to_what_it_renders(disc: Discovery, target: str, add: list[str]) -> None:
    tree = _render(disc, target, add=add)  # the renderer runs the check itself
    files = _caddyfiles(tree)
    assert len(files) == 2, [w for w, _ in files]  # the front door and the router, ConfigMaps on k8s
    assert sum(len(_upstreams(t)) for _, t in files) > 20


@pytest.mark.parametrize("target", ["compose", "k8s", "dev"])
def test_a_dead_upstream_is_refused(disc: Discovery, target: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The mutation pin: a gateway-off install with apps, with the gateway's
    routes forced on as if the CASE-1245 guard were dropped."""
    real = MODULE[target].generate_caddy_config

    def unguarded(*a, **k):
        return dataclasses.replace(real(*a, **k), gateway_active=True)

    monkeypatch.setattr(MODULE[target], "generate_caddy_config", unguarded)
    with pytest.raises(UpstreamNotRendered, match=r"reverse_proxy wip-auth-gateway[\w.-]*:4180 — no rendered service is named 'wip-auth-gateway"):
        _render(disc, target, gateway=False)


def test_the_error_names_the_file_and_the_site() -> None:
    tree = FileTree()
    tree.add("docker-compose.yaml", "services:\n  registry: {container_name: t_wip-registry}\n")
    tree.add("config/caddy/Caddyfile", "app.example {\n    reverse_proxy wip-ghost:80\n}\n")
    with pytest.raises(UpstreamNotRendered) as exc:
        check_caddy_upstreams(tree)
    assert "config/caddy/Caddyfile, in `app.example`: reverse_proxy wip-ghost:80" in str(exc.value)


def test_only_upstream_directives_are_read() -> None:
    # Absolute URLs in redir and friends are not upstreams; ask is.
    text = (
        "{\n    on_demand_tls {\n        ask http://wip-app-edge:8080/edge/ask\n    }\n}\n"
        "app.example {\n    redir * https://other.example/{uri}\n"
        "    forward_auth wip-auth-gateway:4180 {\n        uri /auth\n    }\n"
        "    reverse_proxy wip-a:1 wip-b:2\n}\n"
    )
    assert [(d, u) for _, d, u in _upstreams(text)] == [
        ("ask", "http://wip-app-edge:8080/edge/ask"),
        ("forward_auth", "wip-auth-gateway:4180"),
        ("reverse_proxy", "wip-a:1"),
        ("reverse_proxy", "wip-b:2"),
    ]


@pytest.mark.parametrize("upstream,host", [
    ("wip-x:80", "wip-x"),
    ("http://wip-x:80/edge/ask", "wip-x"),
    ("wip-x.ns.svc.cluster.local:3900", "wip-x.ns.svc.cluster.local"),
    ("wip-x", "wip-x"),
])
def test_host_of(upstream: str, host: str) -> None:
    assert _host_of(upstream) == host
