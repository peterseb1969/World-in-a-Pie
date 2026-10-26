"""CASE-947 — a group mapping restarts nothing, a connector change only Dex.

The federated group policy reached seven services as an env var
(WIP_AUTH_FEDERATED_GROUPS) read at start, so `group-map` and `connector`
verbs applied the whole install. It is now a file next to the API keys,
rendered on every install and re-read while running; the verbs scope their
apply accordingly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.cli import _persist_deployment, app
from wip_deploy.config_gen.api_keys import FEDERATED_GROUPS_RENDER_PATH
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_compose, render_k8s
from wip_deploy.secrets import collect_required_secrets
from wip_deploy.secrets_backend import ResolvedSecrets
from wip_deploy.spec import Deployment, DexConnector, GroupMapping

REPO_ROOT = Path(__file__).resolve().parents[3]
runner = CliRunner()
GOOGLE = DexConnector(
    id="google", type="oidc", name="Google",
    config={"issuer": "https://accounts.google.com", "clientID": "abc.apps"},
)


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _deployment(target: str, *, connectors=(), mappings=()) -> Deployment:
    d = build_deployment(BuildInputs(
        target=target,  # type: ignore[arg-type]
        variant="prod", hostname="wip.example.org", tls="internal",
        registry="ghcr.io/test", compose_data_dir=Path("/tmp/d"), k8s_namespace="wip-t",
    ))
    d.spec.auth.connectors = list(connectors)
    d.spec.auth.group_mappings = list(mappings)
    return d


def _secrets(d: Deployment, disc: Discovery) -> ResolvedSecrets:
    names = collect_required_secrets(d, disc.components, disc.apps)
    return ResolvedSecrets(values=dict.fromkeys(names, "x"))


def _compose(d: Deployment, disc: Discovery) -> dict[Path, str]:
    tree = render_compose(d, disc.components, disc.apps, _secrets(d, disc))
    return {p: e.content for p, e in tree.files.items()}


def _readers(disc: Discovery) -> set[str]:
    return {
        c.metadata.name for c in disc.components
        for e in (*c.spec.env.required, *c.spec.env.optional)
        if e.name == "WIP_AUTH_FEDERATED_GROUPS_FILE"
    }


def test_rendered_on_every_install_empty_without_connectors(discovery: Discovery) -> None:
    files = _compose(_deployment("compose"), discovery)
    assert files[Path(FEDERATED_GROUPS_RENDER_PATH)] == ""


def test_the_policy_carries_the_mappings(discovery: Discovery) -> None:
    d = _deployment("compose", connectors=[GOOGLE],
                    mappings=[GroupMapping(email_domain="corp.example", group="wip-editors")])
    policy = json.loads(_compose(d, discovery)[Path(FEDERATED_GROUPS_RENDER_PATH)])
    assert policy["mappings"] == [{"email_domain": "corp.example", "group": "wip-editors"}]


def test_every_reader_mounts_the_directory_compose(discovery: Discovery) -> None:
    services = yaml.safe_load(
        _compose(_deployment("compose"), discovery)[Path("docker-compose.yaml")]
    )["services"]
    readers = _readers(discovery) & set(services)
    assert "auth-gateway" in readers
    for name in readers:
        assert "./config/auth:/etc/wip:ro" in services[name].get("volumes", []), name


def test_every_reader_mounts_the_directory_k8s(discovery: Discovery) -> None:
    d = _deployment("k8s")
    tree = render_k8s(d, discovery.components, discovery.apps, _secrets(d, discovery))
    secret = yaml.safe_load(tree.files[Path("api-keys-secret.yaml")].content)
    assert secret["stringData"]["federated-groups.json"] == ""
    for name in _readers(discovery):
        path = Path(f"services/{name}.yaml")
        if path not in tree.files:
            continue
        workload: Any = next(
            doc for doc in yaml.safe_load_all(tree.files[path].content)
            if doc and doc["kind"] == "Deployment"
        )
        mounts = workload["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]
        assert any(m["mountPath"] == "/etc/wip" and "subPath" not in m for m in mounts), name


def test_a_mapping_change_alters_only_the_policy_file(discovery: Discovery) -> None:
    before = _compose(_deployment("compose", connectors=[GOOGLE]), discovery)
    after = _compose(_deployment(
        "compose", connectors=[GOOGLE],
        mappings=[GroupMapping(email="ann@corp.example", group="wip-viewers")],
    ), discovery)
    changed = {p for p in before if before[p] != after[p]}
    assert changed == {Path(FEDERATED_GROUPS_RENDER_PATH)}


def _install(tmp_path: Path, *, with_connector: bool) -> Path:
    d = build_deployment(BuildInputs(
        target="compose", variant="dev", hostname="localhost", tls="internal",
        compose_data_dir=tmp_path / "data",
    ))
    secrets = d.spec.secrets.model_copy(update={"location": str(tmp_path / "secrets")})
    d = d.model_copy(update={"spec": d.spec.model_copy(update={"secrets": secrets})})
    if with_connector:
        d.spec.auth.connectors = [GOOGLE]
    _persist_deployment(d, tmp_path, repo_root=REPO_ROOT)
    return tmp_path


def _scope_of(install: Path, *args: str) -> Any:
    with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
        result = runner.invoke(
            app, [*args, "--install-dir", str(install), "--repo-root", str(REPO_ROOT)],
        )
    assert result.exit_code == 0, result.output
    return mutate.call_args.kwargs["services_scope"]


def test_group_map_restarts_nothing(tmp_path: Path) -> None:
    install = _install(tmp_path, with_connector=True)
    assert _scope_of(
        install, "group-map", "add", "--email-domain", "corp.example", "--group", "wip-editors",
    ) == []


def test_connector_add_restarts_only_dex(tmp_path: Path) -> None:
    install = _install(tmp_path, with_connector=False)
    secret = tmp_path / "gh-secret"
    secret.write_text("s\n")
    assert _scope_of(
        install, "connector", "add", "gh", "--type", "github", "--display-name", "GitHub",
        "--config", "clientID=id", "--secret-file", str(secret),
    ) == ["dex"]
