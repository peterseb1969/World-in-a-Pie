"""Every service gets a variable for every outbound key it refuses to start
without, on all three targets.

A service presents a key to each service it calls, read from a chain of
environment variables (``REGISTRY_API_KEY``, then ``API_KEY``, and so on).
The publicly documented dev key no longer stands in when a chain is unset:
the service's lifespan refuses to start, naming the variable
(``wip_auth.require_service_keys``). So a variable the deployer does not render
is a service that does not start.

What each service requires is read from its own source: the module-level
``OUTBOUND_KEYS`` in its ``main.py``, whose elements are chains declared in
wip-auth's ``service_keys`` (or in the service's own modules). Parsing rather
than restating them keeps this test from drifting from the code it guards.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

from wip_deploy.discovery import Discovery
from wip_deploy.renderers import render_compose, render_dev_simple
from wip_deploy.renderers.k8s import render_k8s
from wip_deploy.secrets import ensure_secrets
from wip_deploy.secrets_backend import FileSecretBackend
from wip_deploy.spec import (
    AuthSpec,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    DevPlatform,
    ImagesSpec,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)

from .conftest import REPO_ROOT

# Service name -> its main module, which declares OUTBOUND_KEYS.
_MAINS = {
    "registry": "components/registry/src/registry/main.py",
    "def-store": "components/def-store/src/def_store/main.py",
    "template-store": "components/template-store/src/template_store/main.py",
    "document-store": "components/document-store/src/document_store/main.py",
    "reporting-sync": "components/reporting-sync/src/reporting_sync/main.py",
    "ingest-gateway": "components/ingest-gateway/src/ingest_gateway/main.py",
}

# Modules whose module-level string tuples name key chains.
_CHAIN_SOURCES = (
    "libs/wip-auth/src/wip_auth/service_keys.py",
    "components/document-store/src/document_store/services/reporting_client.py",
    "components/registry/src/registry/services/namespace_deletion.py",
)


def _str_tuple(node: ast.expr) -> tuple[str, ...] | None:
    if isinstance(node, ast.Tuple) and all(
        isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts
    ):
        return tuple(e.value for e in node.elts)  # type: ignore[attr-defined]
    return None


def _module_assignments(path: Path) -> dict[str, ast.expr]:
    tree = ast.parse(path.read_text())
    out: dict[str, ast.expr] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target = stmt.targets[0]
            if isinstance(target, ast.Name):
                out[target.id] = stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            if stmt.value is not None:
                out[stmt.target.id] = stmt.value
    return out


def _known_chains() -> dict[str, tuple[str, ...]]:
    chains: dict[str, tuple[str, ...]] = {}
    for rel in _CHAIN_SOURCES:
        for name, value in _module_assignments(REPO_ROOT / rel).items():
            chain = _str_tuple(value)
            if chain is not None:
                chains[name] = chain
    return chains


def _outbound_keys(service: str) -> list[tuple[str, ...]]:
    known = _known_chains()
    value = _module_assignments(REPO_ROOT / _MAINS[service]).get("OUTBOUND_KEYS")
    assert isinstance(value, ast.Tuple), f"{service}: OUTBOUND_KEYS is not a tuple"
    chains: list[tuple[str, ...]] = []
    for elt in value.elts:
        literal = _str_tuple(elt)
        if literal is not None:
            chains.append(literal)
        elif isinstance(elt, ast.Name) and elt.id in known:
            chains.append(known[elt.id])
        else:
            pytest.fail(
                f"{service}: OUTBOUND_KEYS element {ast.unparse(elt)} is neither a "
                f"literal chain nor a chain declared in {', '.join(_CHAIN_SOURCES)}"
            )
    assert chains, f"{service}: OUTBOUND_KEYS is empty"
    return chains


def _effective(names: set[str]) -> set[str]:
    """wip-auth copies API_KEY or MASTER_API_KEY into WIP_AUTH_LEGACY_API_KEY
    at import when that is unset (config._check_legacy_env_vars), so a
    rendered MASTER_API_KEY satisfies a chain that starts with it."""
    if names & {"API_KEY", "MASTER_API_KEY"}:
        return names | {"WIP_AUTH_LEGACY_API_KEY"}
    return names


def _assert_every_chain_rendered(target: str, env_by_service: dict[str, set[str]]) -> None:
    problems = []
    for service in _MAINS:
        assert service in env_by_service, f"{target}: {service} was not rendered"
        env = _effective(env_by_service[service])
        for chain in _outbound_keys(service):
            if not env & set(chain):
                problems.append(f"{service}: none of {', '.join(chain)}")
    assert not problems, (
        f"{target}: services would refuse to start for want of a key —\n  "
        + "\n  ".join(problems)
    )


def _compose_env(compose_yaml: str) -> dict[str, set[str]]:
    services = yaml.safe_load(compose_yaml)["services"]
    out = {}
    for name, block in services.items():
        env = block.get("environment") or {}
        out[name] = set(env) if isinstance(env, dict) else {e.split("=", 1)[0] for e in env}
    return out


def test_the_declarations_parse_for_every_service() -> None:
    for service in _MAINS:
        for chain in _outbound_keys(service):
            assert chain and all(name.isupper() for name in chain), (service, chain)


def test_compose_renders_every_outbound_key(
    tmp_path: Path, maximal_compose_deployment: Deployment, real_discovery: Discovery,
) -> None:
    secrets = ensure_secrets(
        maximal_compose_deployment, real_discovery.components, real_discovery.apps,
        FileSecretBackend(tmp_path / "secrets"),
    )
    tree = render_compose(
        maximal_compose_deployment, real_discovery.components, real_discovery.apps, secrets,
    )
    _assert_every_chain_rendered(
        "compose", _compose_env(tree.files[Path("docker-compose.yaml")].content),
    )


def test_dev_renders_every_outbound_key(tmp_path: Path, real_discovery: Discovery) -> None:
    optional = sorted(
        c.metadata.name for c in real_discovery.components
        if c.metadata.category == "optional"
    )
    deployment = Deployment(
        metadata=DeploymentMetadata(name="keys-dev"),
        spec=DeploymentSpec(
            target="dev",
            modules={"optional": optional},  # type: ignore[arg-type]
            auth=AuthSpec(mode="oidc", gateway=True),
            network=NetworkSpec(hostname="wip.localhost"),
            images=ImagesSpec(registry="ghcr.io/test", tag="test"),
            platform=PlatformSpec(dev=DevPlatform(mode="simple")),
            secrets=SecretsSpec(backend="file", location=str(tmp_path / "secrets")),
        ),
    )
    secrets = ensure_secrets(
        deployment, real_discovery.components, real_discovery.apps,
        FileSecretBackend(tmp_path / "secrets"),
    )
    tree = render_dev_simple(
        deployment, real_discovery.components, real_discovery.apps, secrets,
        repo_root=REPO_ROOT,
    )
    _assert_every_chain_rendered(
        "dev", _compose_env(tree.files[Path("docker-compose.yaml")].content),
    )


def test_k8s_renders_every_outbound_key(
    tmp_path: Path, maximal_k8s_deployment: Deployment, real_discovery: Discovery,
) -> None:
    secrets = ensure_secrets(
        maximal_k8s_deployment, real_discovery.components, real_discovery.apps,
        FileSecretBackend(tmp_path / "secrets"),
    )
    tree = render_k8s(
        maximal_k8s_deployment, real_discovery.components, real_discovery.apps, secrets,
    )
    env_by_service: dict[str, set[str]] = {}
    for service in _MAINS:
        path = Path(f"services/{service}.yaml")
        assert path in tree.files, f"k8s tree has no {path}"
        names: set[str] = set()
        for doc in yaml.safe_load_all(tree.files[path].content):
            if not doc or doc.get("kind") not in ("Deployment", "StatefulSet"):
                continue
            for container in doc["spec"]["template"]["spec"].get("containers", []):
                names |= {e["name"] for e in container.get("env", [])}
        env_by_service[service] = names
    _assert_every_chain_rendered("k8s", env_by_service)
