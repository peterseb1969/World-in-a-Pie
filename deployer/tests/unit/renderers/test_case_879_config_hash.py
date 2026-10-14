"""CASE-879 — a changed secret or key file restarts exactly the services that read it.

Services read their secrets (env) and api-keys.json (and, on k8s, the
router's Caddyfile — a subPath mount) only at start. A rotation that
changed only that content left every rendered workload identical, so
neither `kubectl apply` nor `compose up -d` restarted anything and the new
key never reached the running services.

The renderers now stamp a per-workload hash of those inputs (k8s pod-template
annotation, compose service label). Contract under test: the hash moves for
exactly the workloads whose inputs moved, never for storage-bearing ones,
and an unchanged install renders byte-identically (a no-op re-apply
restarts nothing).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from wip_deploy.config_gen.config_hash import CONFIG_HASH_KEY, config_hash
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_compose, render_k8s
from wip_deploy.secrets import ensure_secrets
from wip_deploy.secrets_backend import FileSecretBackend, ResolvedSecrets
from wip_deploy.spec import (
    AuthSpec,
    ComposePlatform,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    ImagesSpec,
    K8sPlatform,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)
from wip_deploy.spec.deployment import SpecAPIKey

REPO_ROOT = Path(__file__).parent.parent.parent.parent.parent.resolve()


@pytest.fixture(scope="session")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _deployment(target: str, *, api_keys: list[str] | None = None) -> Deployment:
    platform = (
        PlatformSpec(k8s=K8sPlatform(namespace="wip-test"))
        if target == "k8s"
        else PlatformSpec(compose=ComposePlatform(data_dir="/tmp/d"))
    )
    auth = AuthSpec(
        mode="oidc",
        gateway=True,
        api_keys=[SpecAPIKey(name=n, namespaces=["wip"]) for n in (api_keys or [])],
    )
    return Deployment(
        metadata=DeploymentMetadata(name="t"),
        spec=DeploymentSpec(
            target=target,  # type: ignore[arg-type]
            modules={"optional": ["mcp-server", "reporting-sync"]},  # type: ignore[arg-type]
            auth=auth,
            network=NetworkSpec(hostname="wip.local"),
            images=ImagesSpec(registry="ghcr.io/peterseb1969", tag="v2.0.0"),
            platform=platform,
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
        ),
    )


def _secrets(tmp_path: Path, d: Deployment, disc: Discovery) -> ResolvedSecrets:
    return ensure_secrets(d, disc.components, disc.apps, FileSecretBackend(tmp_path / "s"))


def _rotated(secrets: ResolvedSecrets, name: str) -> ResolvedSecrets:
    assert name in secrets.values
    return ResolvedSecrets(values={**secrets.values, name: secrets.values[name] + "-rotated"})


def _k8s_workloads(d, disc, secrets) -> dict[str, dict]:
    tree = render_k8s(d, disc.components, disc.apps, secrets)
    out: dict[str, dict] = {}
    for path, entry in tree.files.items():
        if path.parts[0] not in ("services", "infrastructure"):
            continue
        for doc in yaml.safe_load_all(entry.content):
            if doc and doc.get("kind") in ("Deployment", "StatefulSet"):
                out[doc["metadata"]["name"]] = doc
    return out


def _annotation(workload: dict) -> str | None:
    return (workload["spec"]["template"]["metadata"].get("annotations") or {}).get(CONFIG_HASH_KEY)


def _reads_secret(workload: dict, secret: str) -> bool:
    env = workload["spec"]["template"]["spec"]["containers"][0].get("env", [])
    return any(
        e.get("valueFrom", {}).get("secretKeyRef", {}).get("key") == secret for e in env
    )


def _mounts_api_keys(workload: dict) -> bool:
    vols = workload["spec"]["template"]["spec"].get("volumes", [])
    return any(v.get("name") == "api-keys-config" for v in vols)


class TestK8s:
    def test_unchanged_install_renders_identically(self, tmp_path, discovery):
        d = _deployment("k8s")
        s = _secrets(tmp_path, d, discovery)
        first = render_k8s(d, discovery.components, discovery.apps, s)
        second = render_k8s(d, discovery.components, discovery.apps, s)
        assert {p: e.content for p, e in first.files.items()} == {
            p: e.content for p, e in second.files.items()
        }

    def test_statefulsets_carry_no_hash(self, tmp_path, discovery):
        d = _deployment("k8s")
        w = _k8s_workloads(d, discovery, _secrets(tmp_path, d, discovery))
        stateful = [n for n, doc in w.items() if doc["kind"] == "StatefulSet"]
        assert stateful, "expected storage-bearing components in the standard set"
        assert all(_annotation(w[n]) is None for n in stateful)

    def test_master_key_rotation_moves_exactly_its_readers(self, tmp_path, discovery):
        d = _deployment("k8s")
        s = _secrets(tmp_path, d, discovery)
        before = _k8s_workloads(d, discovery, s)
        after = _k8s_workloads(d, discovery, _rotated(s, "api-key"))

        readers = {n for n, doc in before.items()
                   if doc["kind"] == "Deployment" and _reads_secret(doc, "api-key")}
        assert {"wip-registry", "wip-document-store"} <= readers
        for name, doc in before.items():
            moved = _annotation(doc) != _annotation(after[name])
            assert moved == (name in readers), name

    def test_declared_key_rotation_moves_the_key_file_readers(self, tmp_path, discovery):
        d = _deployment("k8s", api_keys=["agent"])
        s = _secrets(tmp_path, d, discovery)
        before = _k8s_workloads(d, discovery, s)
        after = _k8s_workloads(d, discovery, _rotated(s, "agent-api-key"))

        readers = {n for n, doc in before.items()
                   if doc["kind"] == "Deployment"
                   and (_mounts_api_keys(doc) or _reads_secret(doc, "agent-api-key"))}
        assert readers, "expected services that mount api-keys.json"
        for name, doc in before.items():
            moved = _annotation(doc) != _annotation(after[name])
            assert moved == (name in readers), name


class TestCompose:
    def _services(self, d, disc, secrets) -> dict[str, dict]:
        tree = render_compose(d, disc.components, disc.apps, secrets)
        return yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)["services"]

    def test_storage_services_and_proxies_carry_no_label(self, tmp_path, discovery):
        d = _deployment("compose")
        svc = self._services(d, discovery, _secrets(tmp_path, d, discovery))
        storage = {c.metadata.name for c in discovery.components if c.spec.storage}
        for name in (storage & set(svc)) | {"caddy"}:
            assert CONFIG_HASH_KEY not in (svc[name].get("labels") or {}), name

    def test_master_key_rotation_relabels_its_readers_only(self, tmp_path, discovery):
        d = _deployment("compose")
        s = _secrets(tmp_path, d, discovery)
        before = self._services(d, discovery, s)
        after = self._services(d, discovery, _rotated(s, "api-key"))

        def label(block):
            return (block.get("labels") or {}).get(CONFIG_HASH_KEY)

        moved = {n for n in before if label(before[n]) != label(after[n])}
        assert "registry" in moved and "document-store" in moved
        assert "caddy" not in moved
        storage = {c.metadata.name for c in discovery.components if c.spec.storage}
        assert not (moved & storage)


class TestConfigHash:
    def test_mounted_file_content_moves_the_hash(self, tmp_path, discovery):
        d = _deployment("k8s")
        s = _secrets(tmp_path, d, discovery)
        router = next(c for c in discovery.components if c.metadata.name == "router")
        from wip_deploy.config_gen import make_spec_context, resolve_all_env
        env = resolve_all_env(
            d, discovery.components, discovery.apps,
            make_spec_context(d, discovery.components),
            collected_secrets=set(s.values),
        )["router"]
        a = config_hash(router, d, env, s, mounted_files={"Caddyfile": "a"})
        b = config_hash(router, d, env, s, mounted_files={"Caddyfile": "b"})
        assert a and b and a != b
