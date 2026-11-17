"""A failed apply leaves the install as the kept spec describes it.

Three ways it did not, each a medium of a forward review:

- apply_compose put the previous render back only on an ApplyError that
  moved nothing. An apply that ended any other way (an interrupt during a
  hanging `compose up`, an unexpected exception) left the refused render on
  disk while the CLI kept the old spec, and `up` deployed from it.
- rotate-key and the connector verbs change the secret store before the
  render. When the render refused, or the apply changed nothing, the store
  kept the change: the running key's value was gone (rotate-key) or the
  store held a connector secret Dex does not run, and every later mutation
  stopped on the secret-drift check.
- apply_k8s marked the change made for every ApplyError, also one raised by
  `kubectl apply` itself. An unreachable API server therefore saved the
  spec and ran its after-persist step: remove-app deleted the removed app's
  key plaintext while the cluster's key file, which the Registry imports,
  still held the key. A failure of the apply step is now judged by what
  kubectl reports it changed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from wip_deploy.apply import ApplyError, apply_compose, apply_k8s
from wip_deploy.cli import app
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers.base import FileTree

from . import test_case_443 as t443
from .test_apply_k8s import _k8s_deployment
from .test_case_951_secret_drift import _applied
from .test_case_1413_render_restored import OLD, _disk, _new_tree, _previous_render
from .test_dex_connectors import _install, _invoke

REPO_ROOT = Path(__file__).resolve().parents[3]
runner = CliRunner()


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


# ── compose: any exit that does not save the spec restores the render ──


@pytest.mark.parametrize("raised", [KeyboardInterrupt(), RuntimeError("unexpected")], ids=["interrupt", "exception"])
def test_an_apply_ended_by_something_else_puts_the_previous_render_back(
    tmp_path: Path, raised: BaseException,
) -> None:
    _previous_render(tmp_path)
    state = frozenset({("test-install_wip-registry", "id-1")})
    with (
        patch("wip_deploy.apply._detect_compose_cmd", return_value=["podman-compose"]),
        patch("wip_deploy.apply._run_build"),
        patch("wip_deploy.apply._run_up", side_effect=raised),
        patch("wip_deploy.apply._remove_install_containers"),
        patch("wip_deploy.apply._container_cli", return_value="podman"),
        patch("wip_deploy.apply._project_container_state", return_value=state),
        pytest.raises(type(raised)),
    ):
        apply_compose(
            deployment=t443._deployment("compose"), components=[], apps=[],
            tree=_new_tree(), install_dir=tmp_path, services_scope=["clintrial"],
        )
    assert _disk(tmp_path) == OLD


# ── the verbs that edit the secret store before they render ──


def _rotate(tmp_path: Path) -> Any:
    return runner.invoke(
        app,
        ["rotate-key", "--secret", "api-key", "--no-check",
         "--install-dir", str(tmp_path), "--repo-root", str(REPO_ROOT)],
    )


def test_a_refused_rotation_keeps_the_running_key(tmp_path: Path, discovery: Discovery) -> None:
    _applied(tmp_path, discovery)
    old = (tmp_path / "secrets" / "api-key").read_text()
    with patch("wip_deploy.cli._render_tree", side_effect=ValueError("garage is not active")):
        result = _rotate(tmp_path)
    assert result.exit_code == 2, result.output
    assert (tmp_path / "secrets" / "api-key").read_text() == old


def test_a_rotation_whose_apply_changed_nothing_keeps_the_running_key(
    tmp_path: Path, discovery: Discovery,
) -> None:
    _applied(tmp_path, discovery)
    old = (tmp_path / "secrets" / "api-key").read_text()
    with patch("wip_deploy.cli.apply_compose", side_effect=ApplyError("build failed")):
        result = _rotate(tmp_path)
    assert result.exit_code == 1, result.output
    assert (tmp_path / "secrets" / "api-key").read_text() == old


def test_a_rotation_saved_by_a_failure_that_moved_the_install_keeps_the_new_key(
    tmp_path: Path, discovery: Discovery,
) -> None:
    _applied(tmp_path, discovery)
    old = (tmp_path / "secrets" / "api-key").read_text()
    with patch("wip_deploy.cli.apply_compose", side_effect=ApplyError("health timeout", mutated=True)):
        result = _rotate(tmp_path)
    assert result.exit_code == 1, result.output
    assert (tmp_path / "secrets" / "api-key").read_text() != old


def _unsaved(*_: Any, **__: Any) -> None:
    raise typer.Exit(1)


def _saved(*_: Any, after_persist: Any = None, **__: Any) -> None:
    after_persist()


def test_a_connector_add_that_is_not_saved_leaves_no_secret(tmp_path: Path) -> None:
    install = _install(tmp_path)
    secret = tmp_path / "google-secret"
    secret.write_text("from-google\n")
    with patch("wip_deploy.cli._apply_and_persist_mutation", side_effect=_unsaved):
        result = _invoke(
            install, "connector", "add", "google", "--type", "oidc", "--display-name", "Google",
            "--config", "issuer=https://accounts.google.com", "--config", "clientID=abc",
            "--secret-file", str(secret),
        )
    assert result.exit_code == 1, result.output
    assert not (tmp_path / "secrets" / "dex-connector-google").exists()


def _saving(install: Path):  # type: ignore[no-untyped-def]
    """A stand-in for the mutation that saves the mutated spec, as a
    successful apply does, and runs the after-persist step."""
    from wip_deploy.cli import _persist_deployment

    def run(deployment: Any, *_: Any, after_persist: Any = None, **__: Any) -> None:
        _persist_deployment(deployment, install, repo_root=REPO_ROOT)
        if after_persist is not None:
            after_persist()

    return run


@pytest.mark.parametrize(("mutation", "kept"), [(_unsaved, "old"), (_saved, "new")], ids=["unsaved", "saved"])
def test_connector_set_secret_keeps_the_new_secret_only_when_saved(
    tmp_path: Path, mutation: Any, kept: str,
) -> None:
    install = _install(tmp_path)
    old = tmp_path / "old-secret"
    old.write_text("old\n")
    with patch("wip_deploy.cli._apply_and_persist_mutation", side_effect=_saving(install)):
        added = _invoke(
            install, "connector", "add", "google", "--type", "oidc", "--display-name", "Google",
            "--config", "issuer=https://accounts.google.com", "--config", "clientID=abc",
            "--secret-file", str(old),
        )
    assert added.exit_code == 0, added.output
    new = tmp_path / "new-secret"
    new.write_text("new\n")
    with patch("wip_deploy.cli._apply_and_persist_mutation", side_effect=mutation):
        _invoke(install, "connector", "set-secret", "google", "--secret-file", str(new))
    assert (tmp_path / "secrets" / "dex-connector-google").read_text().strip() == kept


# ── k8s: a failure of the apply step is a change only when kubectl made one ──


def _tree() -> FileTree:
    tree = FileTree()
    tree.add("namespace.yaml", "kind: Namespace\nmetadata: {name: wip-test}\n")
    tree.add("services/registry.yaml", "kind: Deployment\nmetadata: {name: wip-registry}\n")
    return tree


def _apply_k8s(tmp_path: Path, discovery: Discovery, outputs: dict[str, tuple[int, str]]) -> ApplyError:
    """Run apply_k8s with kubectl answering per call: the namespace apply
    ("namespace"), the recursive apply ("tree") and the rollout ("rollout"),
    each (exit code, stdout)."""

    def kubectl(cmd: list[str], *_: Any, **__: Any) -> Any:
        key = "rollout" if "rollout" in cmd else "tree" if "-R" in cmd else "namespace"
        code, stdout = outputs.get(key, (0, ""))
        if code:
            raise subprocess.CalledProcessError(code, cmd, output=stdout, stderr="refused")
        return MagicMock(returncode=0, stdout=stdout, stderr="")

    d = _k8s_deployment()
    d.spec.apply.wait = True
    d.spec.apply.timeout_seconds = 1
    with (
        patch("wip_deploy.apply.shutil.which", return_value="/usr/bin/kubectl"),
        patch("wip_deploy.apply.subprocess.run", side_effect=kubectl),
        pytest.raises(ApplyError) as raised,
    ):
        apply_k8s(
            deployment=d, components=discovery.components, apps=discovery.apps,
            tree=_tree(), install_dir=tmp_path,
        )
    return raised.value


def test_an_apply_that_reached_no_cluster_changed_nothing(tmp_path: Path, discovery: Discovery) -> None:
    err = _apply_k8s(tmp_path, discovery, {"namespace": (1, "")})
    assert err.mutated is False


def test_a_refused_tree_apply_that_changed_nothing_is_no_change(tmp_path: Path, discovery: Discovery) -> None:
    err = _apply_k8s(tmp_path, discovery, {
        "namespace": (0, "namespace/wip-test unchanged\n"),
        "tree": (1, "configmap/wip-config unchanged\n"),
    })
    assert err.mutated is False


def test_a_refused_tree_apply_that_changed_an_object_is_a_change(tmp_path: Path, discovery: Discovery) -> None:
    err = _apply_k8s(tmp_path, discovery, {"tree": (1, "secret/wip-key-file configured\n")})
    assert err.mutated is True


def test_a_change_earlier_in_the_step_makes_a_later_refusal_a_change(
    tmp_path: Path, discovery: Discovery,
) -> None:
    err = _apply_k8s(tmp_path, discovery, {
        "namespace": (0, "namespace/wip-test created\n"),
        "tree": (1, ""),
    })
    assert err.mutated is True


def test_a_failure_after_the_apply_step_is_still_a_change(tmp_path: Path, discovery: Discovery) -> None:
    err = _apply_k8s(tmp_path, discovery, {"rollout": (1, "")})
    assert err.mutated is True


def test_a_failed_k8s_remove_app_that_changed_nothing_keeps_the_key(tmp_path: Path) -> None:
    """The chain #7 describes: a failure that changed nothing saves no spec,
    so the after-persist step (remove-app forgetting the key) never runs."""
    from wip_deploy.cli import _apply_and_persist_mutation

    after = MagicMock()
    with (
        patch("wip_deploy.cli._validate_or_exit"),
        patch("wip_deploy.cli._refuse_sourceless_dev_apps"),
        patch("wip_deploy.cli._select_cluster_or_exit"),
        patch("wip_deploy.cli._ensure_secrets_via_spec"),
        patch("wip_deploy.cli._refuse_runtime_reservation_collisions"),
        patch("wip_deploy.cli._refuse_attachment_collisions"),
        patch("wip_deploy.cli._render_tree"),
        patch("wip_deploy.cli._persist_generated_secrets"),
        patch("wip_deploy.cli._persist_deployment") as persist,
        patch("wip_deploy.cli.apply_k8s", side_effect=ApplyError("kubectl apply failed")),
        pytest.raises(typer.Exit),
    ):
        _apply_and_persist_mutation(
            _k8s_deployment(), [], [], tmp_path, "Removed app 'wip-aa'",
            repo_root=REPO_ROOT, after_persist=after,
        )
    persist.assert_not_called()
    after.assert_not_called()
