"""`wip` is not grantable to an app key — at any permission.

Three of the four models that hand an app a namespace already refused it: an
owned namespace from the manifest (`AppNamespace.not_wip`) and one added on the
install (`AddedNamespace.not_wip`). The *use* route (`NamespaceAccess`) did not,
so `spec.namespace_access: [{namespace: wip, permission: write}]` and
`wip-deploy app-namespace add <app> wip --access write` both reached the derived
key's grants verbatim. `wip` is the exclusive home of the platform's
`_`-prefixed vocabularies, so that grant lets an app rewrite the terminologies
every other namespace validates against.

`read` on `wip` is not something a declaration can ask for either: it arrives
implicitly with every derived app key.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.config_gen.api_keys import API_KEYS_RENDER_PATH, app_api_key
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_compose
from wip_deploy.secrets import collect_required_secrets
from wip_deploy.secrets_backend import ResolvedSecrets
from wip_deploy.spec import App, AppRef, Deployment
from wip_deploy.spec.component import NamespaceAccess

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _app(disc: Discovery, name: str, **spec: Any) -> App:
    base = next(a for a in disc.apps if a.metadata.name == "wip-cooking")
    raw = base.model_dump()
    raw["metadata"]["name"] = name
    raw["spec"]["namespaces"] = []
    raw["spec"].update(spec)
    return App.model_validate(raw)


def _deployment(target: str, apps: list[App]) -> Deployment:
    d = build_deployment(BuildInputs(
        target=target,  # type: ignore[arg-type]
        variant="prod", hostname="wip.example.org", tls="internal",
        registry="ghcr.io/test", compose_data_dir=Path("/tmp/d"), k8s_namespace="wip-t",
    ))
    d.spec.apps = [AppRef(name=a.metadata.name) for a in apps]
    return d


@pytest.mark.parametrize("permission", ["write", "read"])
def test_the_use_route_refuses_wip(permission: str) -> None:
    """The guard is on the declaration, not the privilege level.

    Refusing `write` alone would leave `read` declarable, and an explicit read
    entry is the doorway the write variant walks through.
    """
    with pytest.raises(PydanticValidationError, match="belongs to the platform"):
        NamespaceAccess.model_validate({"namespace": "wip", "permission": permission})


@pytest.mark.parametrize("namespace", ["wip-kb", "wip-t", "wi", "wipp", "my-wip"])
def test_a_near_miss_namespace_is_still_grantable(namespace: str) -> None:
    """The guard matches the platform namespace exactly, never by prefix.

    `wip-kb` is an ordinary app namespace on every install; a `startswith`
    guard would take it down with the real target.
    """
    assert NamespaceAccess(namespace=namespace).namespace == namespace


def test_the_manifest_route_refuses_wip(discovery: Discovery) -> None:
    with pytest.raises(PydanticValidationError, match="belongs to the platform"):
        _app(discovery, "greedy-app", namespace_access=[{"namespace": "wip", "permission": "write"}])


def test_wip_reaches_the_key_as_implicit_read_only(discovery: Discovery) -> None:
    """What the guard must not break: `wip` still scopes the key, never grants.

    An app with no access declaration still reads the platform vocabularies, and
    `wip` stays out of `grants` — where `read` is dropped precisely because the
    scope already carries it.
    """
    key = app_api_key(_app(discovery, "reader"))
    assert key is not None
    assert key.namespaces == ["wip"]
    assert key.grants == {}


def test_a_declared_access_lands_alongside_wip_read(discovery: Discovery) -> None:
    key = app_api_key(
        _app(discovery, "cooking-app", namespace_access=[{"namespace": "library", "permission": "write"}])
    )
    assert key is not None
    assert key.namespaces == ["library", "wip"]
    assert key.grants == {"library": "write"}


def test_a_persisted_spec_carrying_the_grant_is_refused_not_ignored() -> None:
    """An install rendered by an older deployer cannot keep the grant silently.

    The saved state round-trips through `Deployment.model_validate`
    (`_load_deployment` turns a failure into `error: … failed to deserialize` +
    exit 2), so an install whose spec already holds `wip: write` stops the next
    verb instead of re-applying the escalation.
    """
    with pytest.raises(PydanticValidationError, match="belongs to the platform"):
        AppRef.model_validate({"name": "wip-kb", "namespace_access": [{"namespace": "wip"}]})


def test_no_shipped_manifest_declares_wip_access(discovery: Discovery) -> None:
    """The guard lands on nothing real: every shipped app names another namespace."""
    offenders = [
        (a.metadata.name, access.namespace)
        for a in discovery.apps
        for access in a.spec.namespace_access
        if access.namespace == "wip"
    ]
    assert offenders == []


def test_rendered_key_file_carries_no_wip_grant(discovery: Discovery) -> None:
    apps = [_app(discovery, "cooking-app", namespace_access=[{"namespace": "library", "permission": "write"}])]
    d = _deployment("compose", apps)
    names = collect_required_secrets(d, discovery.components, apps)
    secrets = ResolvedSecrets(values={n: f"secret-of-{n}" for n in names})
    tree = render_compose(d, discovery.components, apps, secrets)
    keys = {k["name"]: k for k in json.loads(tree.files[Path(API_KEYS_RENDER_PATH)].content)["keys"]}
    entry = keys["app-cooking-app"]
    assert "wip" not in entry["grants"]
    assert entry["namespaces"] == ["library", "wip"]
