"""CASE-885 — compose and dev containers receive only the secrets they declare.

Both renderers used to give every service that referenced any secret
`env_file: [".env"]`. The rendered `.env` holds EVERY install secret, so each
such container — apps included — received the PostgreSQL superuser password,
MinIO's root password, the Dex user passwords and the master key, whatever its
manifest declared. k8s never had this: it references each secret on its own.

`env_file` was never needed for interpolation: compose reads `${VAR}` from the
project's `.env` itself (apply passes `--env-file .env`). The contract: no
service carries `env_file`, every `${VAR}` in a service's environment names a
secret its manifest declares, and `.env` still holds every secret as the
interpolation source.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from wip_deploy.config_gen.nats import password_secret
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_compose, render_dev_simple
from wip_deploy.renderers.compose import _secret_to_env_var
from wip_deploy.secrets import ensure_secrets
from wip_deploy.secrets_backend import FileSecretBackend, ResolvedSecrets
from wip_deploy.spec import (
    AppRef,
    AuthSpec,
    ComposePlatform,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    DevPlatform,
    ImagesSpec,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

_MODULES = ["mcp-server", "reporting-sync", "ingest-gateway"]
_APPS = ["wip-kb", "react-console"]
_INTERPOLATION = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _deployment(target: str) -> Deployment:
    platform = (
        PlatformSpec(compose=ComposePlatform(data_dir="/tmp/d"))
        if target == "compose"
        else PlatformSpec(dev=DevPlatform(mode="simple", apps_from_registry=_APPS))
    )
    return Deployment(
        metadata=DeploymentMetadata(name="case885"),
        spec=DeploymentSpec(
            target=target,  # type: ignore[arg-type]
            modules={"optional": _MODULES},  # type: ignore[arg-type]
            apps=[AppRef(name=n) for n in _APPS],
            auth=AuthSpec(mode="oidc", gateway=True),
            network=NetworkSpec(hostname="wip.local"),
            images=ImagesSpec(registry="ghcr.io/peterseb1969", tag="v2.0.0"),
            platform=platform,
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
        ),
    )


def _render(
    target: str, tmp_path: Path, discovery: Discovery
) -> tuple[dict[str, Any], str, ResolvedSecrets]:
    d = _deployment(target)
    secrets = ensure_secrets(
        d, discovery.components, discovery.apps,
        FileSecretBackend(tmp_path / "secrets"),
    )
    if target == "compose":
        tree = render_compose(d, discovery.components, discovery.apps, secrets)
    else:
        tree = render_dev_simple(
            d, discovery.components, discovery.apps, secrets, repo_root=REPO_ROOT,
        )
    doc = yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)
    return doc, tree.files[Path(".env")].content, secrets


def _declared_secret_vars(owner: Any, discovery: Discovery) -> set[str]:
    """Secrets the owner's manifest declares: its env's from_secret
    entries, plus the NATS passwords derived from `nats_client` blocks
    (its own as a client; every client's when the owner is NATS)."""
    declared = {
        _secret_to_env_var(v.source.from_secret)
        for v in [*owner.spec.env.required, *owner.spec.env.optional]
        if v.source.from_secret
    }
    if owner.spec.nats_client is not None:
        declared.add(_secret_to_env_var(password_secret(owner.metadata.name)))
    if owner.metadata.name == "nats":
        declared |= {
            _secret_to_env_var(password_secret(o.metadata.name))
            for o in [*discovery.components, *discovery.apps]
            if o.spec.nats_client is not None
        }
    return declared


def _referenced_vars(service: dict[str, Any]) -> set[str]:
    return {
        m
        for value in (service.get("environment") or {}).values()
        for m in _INTERPOLATION.findall(str(value))
    }


@pytest.mark.parametrize("target", ["compose", "dev"])
class TestOnlyDeclaredSecrets:
    def test_no_service_carries_env_file(
        self, target: str, tmp_path: Path, discovery: Discovery
    ) -> None:
        doc, _, _ = _render(target, tmp_path, discovery)
        carrying = [n for n, s in doc["services"].items() if "env_file" in s]
        assert carrying == []

    def test_interpolations_are_manifest_declared_secrets(
        self, target: str, tmp_path: Path, discovery: Discovery
    ) -> None:
        doc, _, _ = _render(target, tmp_path, discovery)
        owners = {o.metadata.name: o for o in [*discovery.components, *discovery.apps]}
        checked = 0
        for name, service in doc["services"].items():
            refs = _referenced_vars(service)
            if name not in owners:
                # Platform-generated services (Caddy) declare no manifest env.
                assert refs == set(), name
                continue
            assert refs <= _declared_secret_vars(owners[name], discovery), name
            checked += bool(refs)
        assert checked >= 5  # not vacuous: the backend services reference secrets

    def test_app_and_mcp_server_see_only_their_key(
        self, target: str, tmp_path: Path, discovery: Discovery
    ) -> None:
        """The two containers observed holding every secret on wip-local."""
        doc, _, _ = _render(target, tmp_path, discovery)
        for name in ("wip-kb", "mcp-server"):
            refs = _referenced_vars(doc["services"][name])
            assert "API_KEY" in refs, name
            assert not refs & {
                "POSTGRES_PASSWORD", "MINIO_PASSWORD", "GATEWAY_SESSION_SECRET",
            }, name

    def test_dotenv_still_holds_every_secret(
        self, target: str, tmp_path: Path, discovery: Discovery
    ) -> None:
        _, dotenv, secrets = _render(target, tmp_path, discovery)
        for name in secrets.values:
            assert f"{_secret_to_env_var(name)}=" in dotenv
