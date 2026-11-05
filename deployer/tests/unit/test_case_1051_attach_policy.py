"""The attach policy file and the apply's attachment check (CASE-1051).

The Registry refuses an app attachment whose upstream is one of the install's
own hosts, or whose name is a route of the install's own apps. It learns both
from a file the deployer renders beside the namespace reservations
(WIP_ATTACH_POLICY_FILE). The other direction is the apply's: it is refused
when it would give an app a route an attachment owns.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from wip_deploy.attachment_check import check_attachment_collisions
from wip_deploy.config_gen.api_keys import mounts_auth_config_dir
from wip_deploy.config_gen.attach_policy import (
    ATTACH_POLICY_FILENAME,
    ATTACH_POLICY_RENDER_PATH,
    attach_policy_json,
)
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_compose, render_k8s

from .renderers.test_compose import REPO_ROOT, _minimal_compose
from .renderers.test_compose import _secrets as _compose_secrets
from .renderers.test_k8s import _k8s_deployment
from .renderers.test_k8s import _secrets as _k8s_secrets


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def test_compose_policy_names_the_install_hosts_and_app_routes(discovery: Discovery) -> None:
    d = _minimal_compose(apps=["wip-kb", "react-console"])
    policy = json.loads(attach_policy_json(d, discovery.components, discovery.apps))
    hosts = set(policy["blocked_hosts"])
    assert {"registry", "wip-registry", "wip-mongodb", "wip-auth-gateway"} <= hosts
    assert {"caddy", "wip-caddy", "wip-wip-kb", "wip-react-console"} <= hosts
    # The public hostname: an upstream pointing back at it would loop.
    assert d.spec.network.hostname.lower() in hosts
    assert policy["reserved_names"] == ["kb", "rc"]
    assert policy["blocked_hosts"] == sorted(policy["blocked_hosts"])


def test_k8s_policy_names_service_dns(discovery: Discovery) -> None:
    d = _k8s_deployment(apps=["wip-kb"])
    ns = d.spec.platform.k8s.namespace
    policy = json.loads(attach_policy_json(d, discovery.components, discovery.apps))
    assert {"wip-registry", f"wip-registry.{ns}.svc.cluster.local"} <= set(policy["blocked_hosts"])
    # wip-caddy is the front door on k8s too (CASE-1067): an attachment
    # pointing at it would loop.
    assert {"wip-caddy", f"wip-caddy.{ns}.svc.cluster.local"} <= set(policy["blocked_hosts"])
    assert {"kubernetes.default", "kubernetes.default.svc.cluster.local"} <= set(policy["blocked_hosts"])
    assert d.spec.network.hostname.lower() in policy["blocked_hosts"]


def test_the_registry_reads_it_from_the_auth_config_dir(discovery: Discovery) -> None:
    registry = next(c for c in discovery.components if c.metadata.name == "registry")
    assert "WIP_ATTACH_POLICY_FILE" in {e.name for e in registry.spec.env.required}
    assert mounts_auth_config_dir(registry)


def test_compose_renders_the_file(tmp_path: Path, discovery: Discovery) -> None:
    d = _minimal_compose(apps=["wip-kb"])
    s = _compose_secrets(tmp_path, d, discovery)
    tree = render_compose(d, discovery.components, discovery.apps, s)
    policy = json.loads(tree.files[Path(ATTACH_POLICY_RENDER_PATH)].content)
    assert policy["reserved_names"] == ["kb"]
    compose = yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)
    env = compose["services"]["registry"]["environment"]
    assert env["WIP_ATTACH_POLICY_FILE"] == "/etc/wip/attach-policy.json"


def test_k8s_renders_it_into_the_auth_config_secret(tmp_path: Path, discovery: Discovery) -> None:
    d = _k8s_deployment(apps=["wip-kb"])
    s = _k8s_secrets(tmp_path, d, discovery)
    tree = render_k8s(d, discovery.components, discovery.apps, s)
    docs = list(yaml.safe_load_all(tree.files[Path("api-keys-secret.yaml")].content))
    auth = next(doc for doc in docs if doc["metadata"]["name"] == "wip-auth-config")
    assert json.loads(auth["stringData"][ATTACH_POLICY_FILENAME])["reserved_names"] == ["kb"]


# ── the apply's check ─────────────────────────────────────────────────────


def _transport(status: int, body: Any, seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body)
    return httpx.MockTransport(handler)


def test_an_app_taking_an_attachments_route_is_refused(discovery: Discovery) -> None:
    d = _minimal_compose(apps=["wip-kb"])
    seen: list[httpx.Request] = []
    attachments = [{"name": "kb", "status": "inactive", "upstream": "https://kb.other.example"}]
    page = {"items": attachments, "total": 1, "page": 1, "page_size": 1000, "pages": 1}
    result = check_attachment_collisions(
        d, discovery.apps, "admin-key", transport=_transport(200, page, seen)
    )
    assert len(result.errors) == 1
    assert "/apps/kb belongs to the app attachment 'kb' (inactive" in result.errors[0]
    assert seen[0].url.path == "/api/registry/attachments"
    assert seen[0].headers["X-API-Key"] == "admin-key"


def test_no_collision_passes(discovery: Discovery) -> None:
    d = _minimal_compose(apps=["wip-kb"])
    result = check_attachment_collisions(
        d, discovery.apps, "k", transport=_transport(
            200, {"items": [{"name": "aa", "upstream": "x"}], "total": 1, "pages": 1}, []
        )
    )
    assert result.errors == [] and result.note is None


def test_an_older_registry_is_a_note(discovery: Discovery) -> None:
    d = _minimal_compose(apps=["wip-kb"])
    result = check_attachment_collisions(d, discovery.apps, "k", transport=_transport(404, {}, []))
    assert result.errors == [] and "predates app attachments" in result.note


def test_without_an_admin_key_it_is_a_note(discovery: Discovery) -> None:
    d = _minimal_compose(apps=["wip-kb"])
    result = check_attachment_collisions(d, discovery.apps, None)
    assert "not checked" in result.note


def test_the_cli_refuses(discovery: Discovery, monkeypatch) -> None:
    import typer

    from wip_deploy import attachment_check
    from wip_deploy.cli import _refuse_attachment_collisions

    d = _minimal_compose(apps=["wip-kb"])
    monkeypatch.setattr(
        attachment_check, "check_attachment_collisions",
        lambda *a, **k: attachment_check.AttachmentCheck(errors=["/apps/kb belongs to …"]),
    )

    class _S:
        values = {"api-key": "k"}

    with pytest.raises(typer.Exit) as e:
        _refuse_attachment_collisions(d, discovery.apps, _S())  # type: ignore[arg-type]
    assert e.value.exit_code == 2
