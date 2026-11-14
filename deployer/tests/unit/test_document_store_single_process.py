"""document-store runs as one process per install, and its jobs rely on that.

At startup document-store marks FAILED every backup or restore job left
PENDING or RUNNING, and so frees the one-restore-at-a-time claim: with one
process, a job no worker in this process holds belongs to a process that
died. A second replica or uvicorn worker would break that. One process's
restart would fail another's live restore and release its claim, and two
restores could then write the same file keys. These tests stop an install
from rendering more than one document-store process without that job
reconciliation being redesigned first (an owner plus heartbeat lease).
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
WHY = (
    "document-store must run as one process: its startup job reconciliation "
    "fails every running backup/restore job and frees the restore claim, which "
    "is only safe when no other process can be running one"
)


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _deployment(target: str) -> Deployment:
    platform = {
        "compose": PlatformSpec(compose=ComposePlatform(data_dir="/tmp/d")),
        "dev": PlatformSpec(dev=DevPlatform(mode="simple")),
        "k8s": PlatformSpec(k8s=K8sPlatform(namespace="wip-test")),
    }[target]
    return Deployment(
        metadata=DeploymentMetadata(name="singleproc"),
        spec=DeploymentSpec(
            target=target,  # type: ignore[arg-type]
            auth=AuthSpec(mode="api-key-only", gateway=False),
            network=NetworkSpec(hostname="wip.example.com"),
            images=ImagesSpec(registry="ghcr.io/peterseb1969", tag="v2.0.0"),
            platform=platform,
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
        ),
    )


def _secrets(d: Deployment, disc: Discovery, tmp_path: Path) -> Any:
    return ensure_secrets(d, disc.components, disc.apps, FileSecretBackend(tmp_path / "s"))


def _no_workers_flag(command: Any) -> bool:
    text = " ".join(map(str, command)) if isinstance(command, list) else str(command or "")
    return "--workers" not in text


def test_the_image_starts_one_uvicorn_worker() -> None:
    dockerfile = (REPO_ROOT / "components/document-store/Dockerfile").read_text()
    cmd = [line for line in dockerfile.splitlines() if line.startswith("CMD [\"uvicorn\"")]
    assert cmd, "document-store's Dockerfile no longer starts uvicorn directly"
    assert all(_no_workers_flag(line) for line in cmd), WHY


def test_k8s_renders_one_document_store_replica(tmp_path: Path, discovery: Discovery) -> None:
    d = _deployment("k8s")
    tree = render_k8s(d, discovery.components, discovery.apps, _secrets(d, discovery, tmp_path))
    docs = [doc for e in tree.files.values() for doc in yaml.safe_load_all(e.content) if doc]
    [workload] = [
        doc for doc in docs
        if doc["kind"] in ("Deployment", "StatefulSet")
        and doc["metadata"]["name"] == "wip-document-store"
    ]
    assert workload["spec"]["replicas"] == 1, WHY
    # A rolling update would run the new pod beside the old one: two
    # processes for a moment, the new one's startup recovery failing the old
    # one's running restore.
    assert workload["kind"] == "StatefulSet" or workload["spec"].get("strategy") == {"type": "Recreate"}, WHY
    for container in workload["spec"]["template"]["spec"]["containers"]:
        assert _no_workers_flag(container.get("command")), WHY
        assert _no_workers_flag(container.get("args")), WHY


@pytest.mark.parametrize("target", ["compose", "dev"])
def test_compose_renders_one_document_store_process(
    target: str, tmp_path: Path, discovery: Discovery,
) -> None:
    d = _deployment(target)
    secrets = _secrets(d, discovery, tmp_path)
    if target == "dev":
        tree = render_dev_simple(d, discovery.components, discovery.apps, secrets, repo_root=REPO_ROOT)
    else:
        tree = render_compose(d, discovery.components, discovery.apps, secrets)
    services = yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)["services"]
    svc = services["document-store"]
    assert (svc.get("deploy") or {}).get("replicas", 1) == 1, WHY
    assert svc.get("scale", 1) == 1, WHY
    assert _no_workers_flag(svc.get("command")), WHY
