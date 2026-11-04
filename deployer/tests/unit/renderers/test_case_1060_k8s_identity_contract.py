"""On k8s the renderer cannot remove client-sent identity headers (CASE-1060).

ingress-nginx removes a request header only through a configuration snippet,
which controllers disable by default, so the k8s renderer emits none: the
install must work on a controller that forbids snippets. What that leaves is
the documented contract (wip-deployable-app-contract.md): an app trusts
X-WIP-User / X-WIP-Groups only on routes it declares auth_required: true,
where the gateway's answer replaces them. These tests pin both halves, so a
future change that starts stripping (a snippet, or app traffic through the
app edge) has to update the contract with them.
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

SNIPPETS = (
    "nginx.ingress.kubernetes.io/configuration-snippet",
    "nginx.ingress.kubernetes.io/server-snippet",
)


@pytest.fixture(scope="module")
def ingresses(tmp_path_factory: pytest.TempPathFactory) -> list[dict]:
    discovery: Discovery = discover(REPO_ROOT)
    d = _k8s_deployment(apps=["wip-kb", "react-console"])
    s = _k8s_secrets(tmp_path_factory.mktemp("k8s"), d, discovery)
    tree = render_k8s(d, discovery.components, discovery.apps, s)
    return [doc for doc in yaml.safe_load_all(tree.files[Path("ingress.yaml")].content) if doc]


def _paths(doc: dict) -> list[str]:
    return [p["path"] for rule in doc["spec"]["rules"] for p in rule["http"]["paths"]]


def _annotations(doc: dict) -> dict:
    return doc["metadata"].get("annotations") or {}


def test_no_ingress_needs_snippets(ingresses: list[dict]) -> None:
    for doc in ingresses:
        assert not set(_annotations(doc)) & set(SNIPPETS), doc["metadata"]["name"]


def test_a_protected_app_route_gets_the_gateways_identity(ingresses: list[dict]) -> None:
    kb = [d for d in ingresses if any(p.startswith("/apps/kb") for p in _paths(d))
          and "nginx.ingress.kubernetes.io/auth-url" in _annotations(d)]
    assert kb
    for doc in kb:
        assert _annotations(doc)["nginx.ingress.kubernetes.io/auth-response-headers"] == ",".join(
            GATEWAY_IDENTITY_HEADERS
        )


def test_an_unprotected_app_route_is_the_documented_gap(ingresses: list[dict]) -> None:
    """WIP-KB's server-api/kb subpath is auth_required: false. On k8s nothing
    in its Ingress touches identity headers, so a client's copies reach the
    app: the contract tells apps not to read identity there."""
    unprotected = [d for d in ingresses if any(p.startswith("/apps/kb/server-api/kb") for p in _paths(d))]
    assert unprotected, [_paths(d) for d in ingresses]
    for doc in unprotected:
        annotations = _annotations(doc)
        assert "nginx.ingress.kubernetes.io/auth-url" not in annotations
        assert "nginx.ingress.kubernetes.io/auth-response-headers" not in annotations


def test_the_contract_states_the_rule() -> None:
    contract = (REPO_ROOT / "docs/wip-deployable-app-contract.md").read_text()
    assert "Trust the identity headers only on routes you declare `auth_required: true`" in contract
