"""`wip-deploy user add|remove` restart Dex and nothing else.

Dex reads its config.yaml (static users and clients) only at start, and as a
storage-bearing component it gets no generic config hash. The renderers
therefore stamp a digest of the rendered config on the Dex workload (compose
and dev: a service label, k8s: a pod-template annotation), so a changed user
set is a definition change that recreates Dex. With that in place the user
verbs scope their apply to Dex instead of re-upping the whole stack, which on
a dev install removed and rebuilt every container.

Contract under test: every target carries the digest; it is stable across
identical renders (no spurious restarts) and moves with the user set; the
user verbs hand the apply a Dex-only scope, and an empty one when Dex is not
active.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.cli import _persist_deployment, _user_verb_scope, app
from wip_deploy.config_gen.config_hash import CONFIG_HASH_KEY
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
from wip_deploy.spec.deployment import DexUser

REPO_ROOT = Path(__file__).resolve().parents[3]
runner = CliRunner()


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _deployment(target: str, *, extra_user: bool = False, mode: str = "oidc") -> Deployment:
    platform = {
        "compose": PlatformSpec(compose=ComposePlatform(data_dir="/tmp/d")),
        "dev": PlatformSpec(dev=DevPlatform(mode="simple")),
        "k8s": PlatformSpec(k8s=K8sPlatform(namespace="wip-test")),
    }[target]
    auth = AuthSpec(mode=mode, gateway=mode != "api-key-only")  # type: ignore[arg-type]
    if extra_user:
        auth.users.append(DexUser(email="e2e@wip.local", username="e2e", groups=["wip-admins"]))
    return Deployment(
        metadata=DeploymentMetadata(name="dexscope"),
        spec=DeploymentSpec(
            target=target,  # type: ignore[arg-type]
            auth=auth,
            network=NetworkSpec(hostname="wip.local"),
            images=ImagesSpec(registry="ghcr.io/peterseb1969", tag="v2.0.0"),
            platform=platform,
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
        ),
    )


def _dex_hash(target: str, tmp_path: Path, disc: Discovery, *, extra_user: bool = False) -> Any:
    d = _deployment(target, extra_user=extra_user)
    # One secret dir per test: the second render reuses the first's
    # passwords and bcrypt hashes, exactly as a re-apply does.
    s = ensure_secrets(d, disc.components, disc.apps, FileSecretBackend(tmp_path / "s"))
    if target == "k8s":
        tree = render_k8s(d, disc.components, disc.apps, s)
        docs = [doc for e in tree.files.values() for doc in yaml.safe_load_all(e.content) if doc]
        workload = next(
            x for x in docs
            if x["kind"] in ("StatefulSet", "Deployment") and x["metadata"]["name"] == "wip-dex"
        )
        return workload["spec"]["template"]["metadata"]["annotations"][CONFIG_HASH_KEY]
    if target == "dev":
        tree = render_dev_simple(d, disc.components, disc.apps, s, repo_root=REPO_ROOT)
    else:
        tree = render_compose(d, disc.components, disc.apps, s)
    services = yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)["services"]
    return services["dex"]["labels"][CONFIG_HASH_KEY]


@pytest.mark.parametrize("target", ["compose", "dev", "k8s"])
def test_dex_hash_is_stable_and_moves_with_the_user_set(
    target: str, tmp_path: Path, discovery: Discovery,
) -> None:
    first = _dex_hash(target, tmp_path, discovery)
    assert first == _dex_hash(target, tmp_path, discovery)  # re-apply restarts nothing
    assert first != _dex_hash(target, tmp_path, discovery, extra_user=True)


class TestScope:
    def test_dex_only_when_dex_is_active(self, discovery: Discovery) -> None:
        assert _user_verb_scope(_deployment("compose"), discovery.components) == ["dex"]

    def test_empty_without_dex(self, discovery: Discovery) -> None:
        d = _deployment("compose", mode="api-key-only")
        assert _user_verb_scope(d, discovery.components) == []


def _install(tmp_path: Path) -> Path:
    d = build_deployment(
        BuildInputs(
            target="compose",
            variant="dev",
            hostname="localhost",
            tls="internal",
            compose_data_dir=tmp_path / "data",
        )
    )
    secrets = d.spec.secrets.model_copy(update={"location": str(tmp_path / "secrets")})
    d = d.model_copy(update={"spec": d.spec.model_copy(update={"secrets": secrets})})
    _persist_deployment(d, tmp_path, repo_root=REPO_ROOT)
    (tmp_path / "secrets").mkdir()
    return tmp_path


def _invoke(install: Path, *args: str) -> Any:
    return runner.invoke(
        app, [*args, "--install-dir", str(install), "--repo-root", str(REPO_ROOT)],
    )


@pytest.mark.parametrize(
    "args",
    [
        ("user", "add", "e2e", "--email", "e2e@wip.local", "--group", "wip-admins"),
        ("user", "remove", "viewer"),
        ("user", "set-groups", "editor", "--group", "wip-editors,wip-admins"),
    ],
)
def test_user_verbs_scope_the_apply_to_dex(args: tuple[str, ...], tmp_path: Path) -> None:
    install = _install(tmp_path)
    (install / "secrets" / "dex-password-e2e").write_text("pw\n")
    with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
        result = _invoke(install, *args)
    assert result.exit_code == 0, result.output
    assert mutate.call_args.kwargs["services_scope"] == ["dex"]
