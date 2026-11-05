"""Client-sent identity headers never reach an app, on every target (CASE-1060).

The front door is wip-caddy on every target: compose and dev run it on the
host port, and on k8s it runs behind one catch-all Ingress (CASE-1067), so
the same Caddyfile strips the headers there too. An unprotected app route
removes X-WIP-User, X-WIP-Groups and X-WIP-Auth-Method; a protected one has
the first two replaced by the gateway's answer (copy_headers) and removes
X-WIP-Auth-Method. Until CASE-1067 the k8s renderer could not strip them
(ingress-nginx needs a snippet), and these tests pinned that gap; they now
pin that it is closed, so the k8s render can never drift back.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from wip_deploy.config_gen.routing import GATEWAY_IDENTITY_HEADERS
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_k8s

from .test_compose import REPO_ROOT
from .test_k8s import _k8s_deployment
from .test_k8s import _secrets as _k8s_secrets

CLIENT_IDENTITY_HEADERS = ("X-WIP-User", "X-WIP-Groups", "X-WIP-Auth-Method")


@pytest.fixture(scope="module")
def render(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    discovery: Discovery = discover(REPO_ROOT)
    d = _k8s_deployment(apps=["wip-kb", "react-console"])
    s = _k8s_secrets(tmp_path_factory.mktemp("k8s"), d, discovery)
    return render_k8s(d, discovery.components, discovery.apps, s)


@pytest.fixture(scope="module")
def caddyfile(render) -> str:  # type: ignore[no-untyped-def]
    docs = yaml.safe_load_all(render.files[Path("services/caddy.yaml")].content)
    return next(d for d in docs if d["kind"] == "ConfigMap")["data"]["Caddyfile"]


def _block(caddyfile: str, matcher: str) -> str:
    start = caddyfile.index(f"    handle {matcher} {{\n")
    return caddyfile[start: caddyfile.index("\n    }\n", start)]


def test_no_ingress_needs_snippets(render) -> None:  # type: ignore[no-untyped-def]
    """The Ingress carries no snippet: it must work on a controller that
    forbids them."""
    for doc in yaml.safe_load_all(render.files[Path("ingress.yaml")].content):
        annotations = set(doc["metadata"].get("annotations") or {})
        assert not {a for a in annotations if a.endswith("-snippet")}


def test_a_protected_app_route_gets_the_gateways_identity(caddyfile: str) -> None:
    block = _block(caddyfile, "/apps/kb/*")
    assert f"copy_headers {' '.join(GATEWAY_IDENTITY_HEADERS)}" in block
    assert "request_header -X-WIP-Auth-Method" in block
    # Never strip what the gateway just supplied.
    for header in GATEWAY_IDENTITY_HEADERS:
        assert f"request_header -{header}" not in block


def test_an_unprotected_app_route_strips_every_client_identity_header(caddyfile: str) -> None:
    """WIP-KB's server-api/kb subpath is auth_required: false. A client's
    copies of the identity headers are removed before the app sees them."""
    block = _block(caddyfile, "/apps/kb/server-api/kb/*")
    assert "forward_auth" not in block
    for header in CLIENT_IDENTITY_HEADERS:
        assert f"request_header -{header}" in block


def test_the_contract_states_the_rule() -> None:
    contract = (REPO_ROOT / "docs/wip-deployable-app-contract.md").read_text()
    assert "Trust the identity headers only on routes you declare `auth_required: true`" in contract
