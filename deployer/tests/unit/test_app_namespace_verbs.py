"""An admin gives an app another namespace on one install.

`wip-deploy app-namespace add|remove|list` keeps the addition in the
install's own spec (the app's AppRef), never in the shared manifest. The
app's derived key and the namespace reservations count it like the
manifest's declarations, and the apply recreates nothing: only the auth
config files change, which the services and the Registry re-read.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.cli import _persist_deployment, app
from wip_deploy.config_gen.api_keys import API_KEYS_RENDER_PATH, app_api_key
from wip_deploy.config_gen.namespaces import RESERVATIONS_RENDER_PATH
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_compose
from wip_deploy.secrets import collect_required_secrets
from wip_deploy.secrets_backend import ResolvedSecrets
from wip_deploy.spec import AppRef, Deployment
from wip_deploy.spec.component import NamespaceAccess, NamespaceSettings
from wip_deploy.spec.deployment import AddedNamespace
from wip_deploy.spec.validators import validate_all

REPO_ROOT = Path(__file__).resolve().parents[3]
runner = CliRunner()


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _deployment(*refs: AppRef) -> Deployment:
    d = build_deployment(BuildInputs(
        target="compose", variant="prod", hostname="wip.example.org", tls="internal",
        registry="ghcr.io/test", compose_data_dir=Path("/tmp/d"),
    ))
    d.spec.apps = list(refs)
    return d


def _files(d: Deployment, disc: Discovery) -> dict[Path, str]:
    names = collect_required_secrets(d, disc.components, disc.apps)
    secrets = ResolvedSecrets(values=dict.fromkeys(names, "x"))
    tree = render_compose(d, disc.components, disc.apps, secrets)
    return {p: e.content for p, e in tree.files.items()}


RESEARCH = AddedNamespace(
    name="kb-research", settings=NamespaceSettings(description="Research corpus"),
)


def test_an_added_namespace_widens_the_key_and_is_reserved(discovery: Discovery) -> None:
    ref = AppRef(name="wip-kb", namespaces=[RESEARCH],
                 namespace_access=[NamespaceAccess(namespace="shared", permission="write")])
    files = _files(_deployment(ref), discovery)
    keys = {k["name"]: k for k in json.loads(files[Path(API_KEYS_RENDER_PATH)])["keys"]}
    kb = keys["app-wip-kb"]
    assert kb["grants"] == {"kb": "admin", "library": "admin",
                            "kb-research": "admin", "shared": "write"}
    reservations = json.loads(files[Path(RESERVATIONS_RENDER_PATH)])["reservations"]
    assert {"prefix": "kb-research", "app": "wip-kb",
            "settings": {"description": "Research corpus"}} in reservations
    assert not any(r["prefix"] == "shared" for r in reservations)


def test_only_the_auth_config_files_change(discovery: Discovery) -> None:
    """Why the verb can apply without recreating anything."""
    before = _files(_deployment(AppRef(name="wip-kb")), discovery)
    after = _files(_deployment(AppRef(name="wip-kb", namespaces=[RESEARCH])), discovery)
    changed = {p for p in before if before[p] != after[p]}
    assert changed == {Path(API_KEYS_RENDER_PATH), Path(RESERVATIONS_RENDER_PATH)}


def test_the_key_secret_does_not_change(discovery: Discovery) -> None:
    kb = next(a for a in discovery.apps if a.metadata.name == "wip-kb")
    plain = app_api_key(kb)
    widened = app_api_key(kb, AppRef(name="wip-kb", namespaces=[RESEARCH]))
    assert plain is not None and widened is not None
    assert widened.secret_name == plain.secret_name


@pytest.mark.parametrize("ref,message", [
    (AppRef(name="react-console", namespaces=[RESEARCH]), "master key"),
    (AppRef(name="wip-kb", namespaces=[AddedNamespace(name="library")]), "already declared"),
    (AppRef(name="wip-kb", namespace_access=[NamespaceAccess(namespace="kb")]), "already declared"),
])
def test_contradicting_additions_are_refused(discovery: Discovery, ref: AppRef, message: str) -> None:
    report = validate_all(_deployment(ref), discovery.components, discovery.apps)
    assert any(message in str(e) for e in report.errors), report.errors


def test_one_owner_counts_additions(discovery: Discovery) -> None:
    d = _deployment(
        AppRef(name="wip-kb", namespaces=[RESEARCH]),
        AppRef(name="wip-cooking", namespaces=[AddedNamespace(name="kb-research")]),
    )
    report = validate_all(d, discovery.components, discovery.apps)
    assert any("'kb-research' is reserved by both" in str(e) for e in report.errors)


def test_an_addition_on_a_disabled_app_renders_nothing(discovery: Discovery) -> None:
    files = _files(_deployment(AppRef(name="wip-kb", enabled=False, namespaces=[RESEARCH])), discovery)
    assert "kb-research" not in files[Path(RESERVATIONS_RENDER_PATH)]


def test_adding_a_namespace_twice_is_refused_by_the_model() -> None:
    with pytest.raises(ValueError, match="added twice"):
        AppRef(name="wip-kb", namespaces=[RESEARCH],
               namespace_access=[NamespaceAccess(namespace="kb-research")])


# --- the verbs ----------------------------------------------------------


def _install(tmp_path: Path, *refs: AppRef) -> Path:
    d = build_deployment(BuildInputs(
        target="compose", variant="dev", hostname="localhost", tls="internal",
        compose_data_dir=tmp_path / "data",
    ))
    secrets = d.spec.secrets.model_copy(update={"location": str(tmp_path / "secrets")})
    d = d.model_copy(update={"spec": d.spec.model_copy(update={"secrets": secrets})})
    d.spec.apps = list(refs)
    _persist_deployment(d, tmp_path, repo_root=REPO_ROOT)
    return tmp_path


def _run(install: Path, *args: str) -> tuple[Any, Any]:
    with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
        result = runner.invoke(
            app, [*args, "--install-dir", str(install), "--repo-root", str(REPO_ROOT)],
        )
    return result, mutate


def test_add_owned_applies_config_only(tmp_path: Path) -> None:
    install = _install(tmp_path, AppRef(name="wip-kb"))
    result, mutate = _run(
        install, "app-namespace", "add", "wip-kb", "kb-research",
        "--description", "Research corpus", "--setting", "deletion_mode=full",
    )
    assert result.exit_code == 0, result.output
    assert mutate.call_args.kwargs["services_scope"] == []
    deployment = mutate.call_args.args[0]
    [ref] = deployment.spec.apps
    assert ref.namespaces == [AddedNamespace(
        name="kb-research",
        settings=NamespaceSettings(description="Research corpus", deletion_mode="full"),
    )]


def test_add_access(tmp_path: Path) -> None:
    install = _install(tmp_path, AppRef(name="wip-kb"))
    result, mutate = _run(install, "app-namespace", "add", "wip-kb", "shared", "--access", "write")
    assert result.exit_code == 0, result.output
    [ref] = mutate.call_args.args[0].spec.apps
    assert ref.namespace_access == [NamespaceAccess(namespace="shared", permission="write")]


@pytest.mark.parametrize("args,message", [
    (["add", "wip-kb", "x", "--access", "write", "--description", "d"], "owned namespace only"),
    (["add", "wip-kb", "x", "--setting", "nonsense"], "KEY=VALUE"),
    (["add", "wip-kb", "x", "--setting", "deletion_mode=sometimes"], "deletion_mode"),
    (["add", "wip-cooking", "x"], "not enabled"),
    (["add", "wip-kb", "existing"], "already added"),
    (["remove", "wip-kb", "never-added"], "was not added"),
])
def test_verb_refusals(tmp_path: Path, args: list[str], message: str) -> None:
    install = _install(tmp_path, AppRef(name="wip-kb", namespaces=[AddedNamespace(name="existing")]))
    result, mutate = _run(install, "app-namespace", *args)
    assert result.exit_code == 2, result.output
    assert message in result.output
    mutate.assert_not_called()


def test_remove(tmp_path: Path) -> None:
    install = _install(tmp_path, AppRef(
        name="wip-kb", namespaces=[RESEARCH],
        namespace_access=[NamespaceAccess(namespace="shared")],
    ))
    result, mutate = _run(install, "app-namespace", "remove", "wip-kb", "kb-research")
    assert result.exit_code == 0, result.output
    assert mutate.call_args.kwargs["services_scope"] == []
    [ref] = mutate.call_args.args[0].spec.apps
    assert ref.namespaces == [] and ref.namespace_access == [NamespaceAccess(namespace="shared")]


def test_list_marks_the_source(tmp_path: Path) -> None:
    install = _install(tmp_path, AppRef(name="wip-kb", namespaces=[RESEARCH]))
    result, _ = _run(install, "app-namespace", "list", "wip-kb")
    assert result.exit_code == 0, result.output
    lines = [line.split() for line in result.output.strip().splitlines()]
    assert ["kb", "admin", "manifest"] in lines
    assert ["kb-research", "admin", "this", "install"] in lines
    assert ["wip", "read", "platform"] in lines


def test_the_saved_spec_keeps_additions(tmp_path: Path) -> None:
    """redeploy re-renders the saved spec, so additions must round-trip."""
    from wip_deploy.cli import _load_deployment

    install = _install(tmp_path, AppRef(name="wip-kb", namespaces=[RESEARCH]))
    [ref] = _load_deployment(install).spec.apps
    assert ref.namespaces == [RESEARCH]
