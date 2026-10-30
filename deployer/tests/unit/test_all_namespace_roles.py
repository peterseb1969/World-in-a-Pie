"""Roles that read every namespace belong to privileged apps only.

`event-subscriber` and `replay-subscriber` receive every namespace's events
(NATS subjects carry no namespace to scope a subscription by), and MongoDB
`read-all` reads every service database. An app holding a key of its own,
scoped to its namespaces, must not get around that scope through them; only
an app declaring `privileged_key` (it holds the master key) may declare them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from wip_deploy.discovery import Discovery, discover
from wip_deploy.spec import App

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _app(disc: Discovery, **spec: Any) -> App:
    base = next(a for a in disc.apps if a.metadata.name == "wip-cooking")
    raw = base.model_dump()
    raw["metadata"]["name"] = "probe-app"
    raw["spec"].update(spec)
    return App.model_validate(raw)


def _privileged(disc: Discovery, **spec: Any) -> App:
    return _app(disc, privileged_key=True, namespaces=[], namespace_access=[], **spec)


@pytest.mark.parametrize("role", ["event-subscriber", "replay-subscriber"])
def test_an_unprivileged_app_cannot_subscribe_to_every_namespace(discovery, role):
    with pytest.raises(ValidationError, match=f"{role} reads every namespace"):
        _app(discovery, nats_client={"roles": [role]})


def test_an_unprivileged_app_cannot_read_all_of_mongodb(discovery):
    with pytest.raises(ValidationError, match="mongo read-all reads every namespace"):
        _app(discovery, mongo_client={"roles": ["read-all"]})


@pytest.mark.parametrize("role", ["ingest-producer", "nats-monitor"])
def test_scoped_nats_roles_stay_open_to_every_app(discovery, role):
    assert _app(discovery, nats_client={"roles": [role]}).spec.nats_client is not None


def test_a_privileged_app_may_declare_them(discovery):
    app = _privileged(
        discovery,
        nats_client={"roles": ["event-subscriber", "replay-subscriber"]},
        mongo_client={"roles": ["read-all"]},
    )
    assert app.spec.mongo_client is not None


def test_the_shipped_manifests_comply(discovery):
    """React Console (privileged) is the only app declaring read-all."""
    holders = [
        a.metadata.name for a in discovery.apps
        if a.spec.mongo_client is not None and "read-all" in a.spec.mongo_client.roles
    ]
    assert holders == ["react-console"]
