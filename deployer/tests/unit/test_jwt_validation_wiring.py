"""Services validate Dex tokens against what Dex actually issues.

Dex stamps `iss` with its public URL and `aud` with the client that requested
the token, and serves its signing keys at `<issuer>/keys`. The services' JWT
settings are therefore rendered from the same sources as Dex's own config:
the issuer from the public base Dex's `issuer:` comes from, the keys from
Dex's internal address, and the accepted audiences from the Dex clients the
deployment registers (the gateway's and every enabled app's own). Before
this, services compared tokens against the internal URL, derived a key path
Dex does not serve, and expected an audience no client has, so every Bearer
request failed.

Contract under test, on every target: each service that receives
WIP_AUTH_JWT_ISSUER_URL gets exactly Dex's rendered issuer, the internal
`/dex/keys` URL, and exactly the rendered Dex client ids as audiences.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_compose, render_dev_simple, render_k8s
from wip_deploy.secrets import ensure_secrets
from wip_deploy.secrets_backend import FileSecretBackend
from wip_deploy.spec import (
    AppRef,
    AuthSpec,
    ComposePlatform,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    DevPlatform,
    ImagesSpec,
    K8sPlatform,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
# dnd declares its own Dex client, so its tokens carry aud=dnd.
APP_WITH_OWN_CLIENT = "dnd"


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _deployment(target: str) -> Deployment:
    apps = [APP_WITH_OWN_CLIENT]
    platform = {
        "compose": PlatformSpec(compose=ComposePlatform(data_dir="/tmp/d")),
        "dev": PlatformSpec(dev=DevPlatform(mode="simple", apps_from_registry=apps)),
        "k8s": PlatformSpec(k8s=K8sPlatform(namespace="wip-test")),
    }[target]
    return Deployment(
        metadata=DeploymentMetadata(name="jwtwiring"),
        spec=DeploymentSpec(
            target=target,  # type: ignore[arg-type]
            modules={"optional": ["reporting-sync", "ingest-gateway"]},  # type: ignore[arg-type]
            apps=[AppRef(name=n) for n in apps],
            auth=AuthSpec(mode="hybrid", gateway=True),
            network=NetworkSpec(hostname="wip.example.com"),
            images=ImagesSpec(registry="ghcr.io/peterseb1969", tag="v2.0.0"),
            platform=platform,
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
        ),
    )


def _render(target: str, tmp_path: Path, disc: Discovery) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
    """(service → env, parsed Dex config) for one target."""
    d = _deployment(target)
    s = ensure_secrets(d, disc.components, disc.apps, FileSecretBackend(tmp_path / "s"))
    if target == "k8s":
        tree = render_k8s(d, disc.components, disc.apps, s)
        docs = [doc for e in tree.files.values() for doc in yaml.safe_load_all(e.content) if doc]
        envs = {
            doc["metadata"]["name"].removeprefix("wip-"): {
                e["name"]: e.get("value", "") for e in doc["spec"]["template"]["spec"]["containers"][0].get("env", [])
            }
            for doc in docs if doc["kind"] in ("Deployment", "StatefulSet")
        }
        cm = next(x for x in docs if x["kind"] == "ConfigMap" and x["metadata"]["name"] == "wip-dex-config")
        return envs, yaml.safe_load(cm["data"]["config.yaml"])
    if target == "dev":
        tree = render_dev_simple(d, disc.components, disc.apps, s, repo_root=REPO_ROOT)
    else:
        tree = render_compose(d, disc.components, disc.apps, s)
    services = yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)["services"]
    envs = {name: {k: str(v) for k, v in (svc.get("environment") or {}).items()} for name, svc in services.items()}
    return envs, yaml.safe_load(tree.files[Path("config/dex/config.yaml")].content)


@pytest.mark.parametrize("target", ["compose", "dev", "k8s"])
def test_every_validating_service_matches_dex(target: str, tmp_path: Path, discovery: Discovery) -> None:
    envs, dex = _render(target, tmp_path, discovery)
    validating = {name: env for name, env in envs.items() if "WIP_AUTH_JWT_ISSUER_URL" in env}
    assert {"registry", "def-store", "template-store", "document-store", "reporting-sync",
            "ingest-gateway"} <= set(validating)

    client_ids = sorted(c["id"] for c in dex["staticClients"])
    assert {"wip-gateway", APP_WITH_OWN_CLIENT} <= set(client_ids)
    for name, env in validating.items():
        assert env["WIP_AUTH_JWT_ISSUER_URL"] == dex["issuer"], name
        assert env["WIP_AUTH_JWT_JWKS_URI"] == "http://wip-dex:5556/dex/keys", name
        assert sorted(env["WIP_AUTH_JWT_AUDIENCE"].split(",")) == client_ids, name
