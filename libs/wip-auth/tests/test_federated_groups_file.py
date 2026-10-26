"""The federated group policy applies without a restart (CASE-947).

The policy used to arrive as WIP_AUTH_FEDERATED_GROUPS, read once at start,
so every `wip-deploy group-map` change restarted every token-validating
service. It is now a file the deployer renders next to the API keys, and the
OIDC provider re-reads it while running.
"""

from __future__ import annotations

import json
from pathlib import Path

from wip_auth import AuthConfig, OIDCProvider, create_providers_from_config


def _policy(*mappings: tuple[str, str]) -> str:
    return json.dumps({
        "local_connectors": ["local"],
        "reserved_groups": ["wip-admins", "wip-services"],
        "mappings": [{"email": e, "group": g} for e, g in mappings],
    })


def _oidc(path: Path) -> OIDCProvider:
    config = AuthConfig(
        mode="jwt_only",
        jwt_jwks_uri="http://dex.invalid/keys",
        federated_groups_file=str(path),
        federated_groups_file_reload_seconds=30,
    )
    provider = next(p for p in create_providers_from_config(config) if isinstance(p, OIDCProvider))
    provider.policy_watcher.stop()  # driven by check_once below
    return provider


FEDERATED = {"email": "ann@corp.example", "federated_claims": {"connector_id": "google"}}


def _groups(provider: OIDCProvider) -> list[str]:
    assert provider.federated_groups is not None
    return provider.federated_groups.apply(FEDERATED, ["wip-admins"])


def test_a_mapping_change_applies_without_rebuilding(tmp_path: Path) -> None:
    path = tmp_path / "federated-groups.json"
    path.write_text(_policy(("ann@corp.example", "wip-editors")))
    provider = _oidc(path)
    assert _groups(provider) == ["wip-editors"]  # reserved group stripped, mapping applied

    path.write_text(_policy(("ann@corp.example", "wip-viewers")))
    assert provider.policy_watcher.check_once() is True
    assert _groups(provider) == ["wip-viewers"]


def test_a_malformed_policy_keeps_the_current_one(tmp_path: Path) -> None:
    path = tmp_path / "federated-groups.json"
    path.write_text(_policy(("ann@corp.example", "wip-editors")))
    provider = _oidc(path)
    path.write_text('{"mappings": [')
    assert provider.policy_watcher.check_once() is False
    assert _groups(provider) == ["wip-editors"]


def test_an_empty_file_means_no_policy(tmp_path: Path) -> None:
    path = tmp_path / "federated-groups.json"
    path.write_text("")
    assert _oidc(path).federated_groups is None


def test_a_policy_file_that_appears_later_is_applied(tmp_path: Path) -> None:
    path = tmp_path / "federated-groups.json"
    provider = _oidc(path)
    assert provider.federated_groups is None

    path.write_text(_policy(("ann@corp.example", "wip-editors")))
    assert provider.policy_watcher.check_once() is True
    assert _groups(provider) == ["wip-editors"]


def test_the_env_policy_still_works_without_a_file() -> None:
    config = AuthConfig(
        mode="jwt_only",
        jwt_jwks_uri="http://dex.invalid/keys",
        federated_groups=_policy(("ann@corp.example", "wip-editors")),
    )
    provider = next(p for p in create_providers_from_config(config) if isinstance(p, OIDCProvider))
    assert provider.policy_watcher is None
    assert _groups(provider) == ["wip-editors"]
