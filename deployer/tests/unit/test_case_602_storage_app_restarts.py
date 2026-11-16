"""An app that declares storage still restarts when its key rotates (CASE-602).

The workload config hash, which makes a rotated secret a definition change,
skipped every storage-bearing owner, and `rotate-key` skipped them as readers.
That is right for a storage-bearing component (a database's password lives in
its volume; a restart does not rotate it), but wip-aa gained a `/data` volume
for its Settings key, and then `rotate-key app-wip-aa` neither restarted it
nor named it: AA kept the old key, which the install refuses once the
rotation applies. An app is now hashed and restarted with or without storage.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from wip_deploy.cli import _app_key_secret, _key_readers
from wip_deploy.config_gen.config_hash import CONFIG_HASH_KEY, restarts_on_config_change
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_compose, render_k8s
from wip_deploy.secrets import ensure_secrets
from wip_deploy.secrets_backend import FileSecretBackend
from wip_deploy.spec import (
    AppRef,
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

REPO_ROOT = Path(__file__).resolve().parents[3]


def _deployment(target: str = "compose") -> Deployment:
    platform = (
        PlatformSpec(k8s=K8sPlatform(namespace="wip-test"))
        if target == "k8s"
        else PlatformSpec(compose=ComposePlatform(data_dir="/tmp/d"))
    )
    return Deployment(
        metadata=DeploymentMetadata(name="t"),
        spec=DeploymentSpec(
            target=target,  # type: ignore[arg-type]
            modules={"optional": ["mcp-server", "reporting-sync"]},  # type: ignore[arg-type]
            apps=[AppRef(name="wip-aa")],
            auth=AuthSpec(mode="oidc", gateway=True),
            network=NetworkSpec(hostname="wip.local"),
            images=ImagesSpec(registry="ghcr.io/test", tag="v2.0.0"),
            platform=platform,
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
        ),
    )


def _aa_label(tmp_path: Path, d: Deployment, disc: Discovery) -> str | None:
    secrets = ensure_secrets(d, disc.components, disc.apps, FileSecretBackend(tmp_path / "secrets"))
    tree = render_compose(d, disc.components, disc.apps, secrets)
    compose = yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)
    service = next(v for k, v in compose["services"].items() if k.endswith("wip-aa"))
    labels = service.get("labels") or {}
    if isinstance(labels, list):
        labels = dict(item.split("=", 1) for item in labels)
    return labels.get(CONFIG_HASH_KEY)


def _aa_k8s_workload(tmp_path: Path, d: Deployment, disc: Discovery) -> dict:
    secrets = ensure_secrets(d, disc.components, disc.apps, FileSecretBackend(tmp_path / "secrets"))
    tree = render_k8s(d, disc.components, disc.apps, secrets)
    for doc in yaml.safe_load_all(tree.files[Path("infrastructure/wip-aa.yaml")].content):
        if doc and doc.get("kind") == "StatefulSet":
            return doc
    raise AssertionError("wip-aa rendered no StatefulSet")


def test_rotating_the_storage_apps_key_rolls_its_k8s_pods(tmp_path: Path) -> None:
    """The k8s half: AA's StatefulSet carries the hash on its pod template,
    and a rotated key moves it, so the apply rolls the pods."""
    disc = discover(REPO_ROOT)
    d = _deployment("k8s")

    def annotation() -> str | None:
        meta = _aa_k8s_workload(tmp_path, d, disc)["spec"]["template"]["metadata"]
        return (meta.get("annotations") or {}).get(CONFIG_HASH_KEY)

    before = annotation()
    assert before is not None

    secret = _app_key_secret(disc.apps, "wip-aa")
    assert secret is not None
    (tmp_path / "secrets" / secret).write_text("rotated-key")
    assert annotation() not in (None, before)


def test_a_storage_app_restarts_but_a_storage_component_does_not() -> None:
    disc = discover(REPO_ROOT)
    aa = next(a for a in disc.apps if a.metadata.name == "wip-aa")
    postgres = next(c for c in disc.components if c.metadata.name == "postgres")
    assert aa.spec.storage and postgres.spec.storage  # the premise: both declare storage
    assert restarts_on_config_change(aa)
    assert not restarts_on_config_change(postgres)


def test_rotating_the_storage_apps_key_changes_its_definition(tmp_path: Path) -> None:
    disc = discover(REPO_ROOT)
    d = _deployment()
    before = _aa_label(tmp_path, d, disc)
    assert before is not None  # stamped although wip-aa declares storage

    secret = _app_key_secret(disc.apps, "wip-aa")
    assert secret is not None
    (tmp_path / "secrets" / secret).write_text("rotated-key")
    assert _aa_label(tmp_path, d, disc) not in (None, before)


def test_rotate_key_names_the_storage_app_as_a_reader() -> None:
    disc = discover(REPO_ROOT)
    d = _deployment()
    secret = _app_key_secret(disc.apps, "wip-aa")
    readers = _key_readers(secret, d, disc.components, disc.apps)
    assert "wip-aa" in readers
    # A storage-bearing component still never restarts this way: postgres
    # reads postgres-password at start (its manifest), and is not named.
    postgres = next(c for c in disc.components if c.metadata.name == "postgres")
    assert postgres.spec.storage
    assert any(
        ev.source.from_secret == "postgres-password" for ev in postgres.spec.env.required
    )
    assert "postgres" not in _key_readers("postgres-password", d, disc.components, disc.apps)
