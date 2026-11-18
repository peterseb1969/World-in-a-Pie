"""A failed apply leaves the install as the kept spec describes it: the k8s
render and the supplied password hashes.

Three places still broke that rule after the compose render and the store
verbs were fixed:

- apply_k8s deletes the previous render and writes the new one before
  kubectl runs. When kubectl then refused having changed nothing, the CLI
  kept the old spec, but the install dir held the refused render: an
  operator's `kubectl apply -R -f <install dir>` would deploy what the spec
  refused. The previous render now comes back on every exit that saves no
  spec, files only the previous render had included.
- `user add --password-hash-file` and `user import` removed the hashes they
  stored on every failure, also on one that moved the install and so saved
  the spec. The saved spec then named users whose operator-supplied hash was
  gone, and every later mutation stopped on the missing operator secret. The
  hashes now stay exactly when the spec is saved.
- the evidence that decides whether a failed `compose up` moved anything
  never reached podman: the listing asked for `{{.Id}}`, podman refused the
  format, and every failed up counted as a change, which saved the spec.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import typer

from wip_deploy.apply import _CONTAINER_ROW_FORMAT, ApplyError, apply_k8s
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers.base import FileTree

from .test_apply_k8s import _k8s_deployment
from .test_case_964_supplied_hashes import _dex_config, _entry, _install, _invoke

REPO_ROOT = Path(__file__).resolve().parents[3]

PREVIOUS = {
    "namespace.yaml": "kind: Namespace\nmetadata: {name: wip-test}\n",
    "configmaps.yaml": "kind: ConfigMap\nmetadata: {name: wip-config}\ndata: {apps: old}\n",
    "services/registry.yaml": "kind: Deployment\nmetadata: {name: wip-registry}\n",
    "infrastructure/only-before.yaml": "kind: StatefulSet\nmetadata: {name: gone-in-new}\n",
    "secrets/api-key": "store file, not the render\n",
}


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _previous(root: Path) -> None:
    for rel, content in PREVIOUS.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(content)


def _new_tree() -> FileTree:
    tree = FileTree()
    tree.add("namespace.yaml", PREVIOUS["namespace.yaml"])
    tree.add("configmaps.yaml", "kind: ConfigMap\nmetadata: {name: wip-config}\ndata: {apps: clintrial}\n")
    tree.add("services/registry.yaml", PREVIOUS["services/registry.yaml"])
    tree.add("services/clintrial.yaml", "kind: Deployment\nmetadata: {name: clintrial}\n")
    return tree


def _disk(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): p.read_text() for p in sorted(root.rglob("*")) if p.is_file()}


def _run(
    tmp_path: Path, discovery: Discovery, answer: Any, *, scope: list[str] | None = None,
    raises: type[BaseException] = ApplyError,
) -> BaseException:
    """apply_k8s over the previous render, kubectl answered by `answer(cmd)`:
    a (exit code, stdout) pair, or an exception to raise."""

    def kubectl(cmd: list[str], *_: Any, **__: Any) -> Any:
        got = answer(cmd)
        if isinstance(got, BaseException):
            raise got
        code, stdout = got
        if code:
            raise subprocess.CalledProcessError(code, cmd, output=stdout, stderr="refused")
        return MagicMock(returncode=0, stdout=stdout, stderr="")

    _previous(tmp_path)
    d = _k8s_deployment()
    d.spec.apply.wait = True
    d.spec.apply.timeout_seconds = 1
    with (
        patch("wip_deploy.apply.shutil.which", return_value="/usr/bin/kubectl"),
        patch("wip_deploy.apply.subprocess.run", side_effect=kubectl),
        pytest.raises(raises) as raised,
    ):
        apply_k8s(
            deployment=d, components=discovery.components, apps=discovery.apps,
            tree=_new_tree(), install_dir=tmp_path, services_scope=scope,
        )
    return raised.value


def _new_disk() -> dict[str, str]:
    tree = _new_tree()
    new = {str(rel): entry.content for rel, entry in tree.files.items()}
    return dict(sorted({**new, "secrets/api-key": PREVIOUS["secrets/api-key"]}.items()))


# ── fail on the old code: a failure that saves no spec restores the render ──


def test_a_refused_tree_apply_that_changed_nothing_puts_the_previous_render_back(
    tmp_path: Path, discovery: Discovery,
) -> None:
    err = _run(tmp_path, discovery, lambda cmd: (1, "configmap/wip-config unchanged\n") if "-R" in cmd else (0, ""))
    assert getattr(err, "mutated", None) is False
    assert _disk(tmp_path) == dict(sorted(PREVIOUS.items()))


def test_a_refused_scoped_apply_that_changed_nothing_puts_the_previous_render_back(
    tmp_path: Path, discovery: Discovery,
) -> None:
    def answer(cmd: list[str]) -> Any:
        return (1, "") if any(str(a).endswith("clintrial.yaml") for a in cmd) else (0, "")

    err = _run(tmp_path, discovery, answer, scope=["clintrial"])
    assert getattr(err, "mutated", None) is False
    assert _disk(tmp_path) == dict(sorted(PREVIOUS.items()))


def test_an_interrupted_k8s_apply_puts_the_previous_render_back(tmp_path: Path, discovery: Discovery) -> None:
    _run(
        tmp_path, discovery, lambda cmd: KeyboardInterrupt() if "-R" in cmd else (0, ""),
        raises=KeyboardInterrupt,
    )
    assert _disk(tmp_path) == dict(sorted(PREVIOUS.items()))


# ── controls, pass on both: a failure that moved the cluster keeps the new render ──


def test_a_refused_tree_apply_that_changed_an_object_keeps_the_new_render(
    tmp_path: Path, discovery: Discovery,
) -> None:
    err = _run(tmp_path, discovery, lambda cmd: (1, "configmap/wip-config configured\n") if "-R" in cmd else (0, ""))
    assert getattr(err, "mutated", None) is True
    assert _disk(tmp_path) == _new_disk()


def test_a_failed_rollout_keeps_the_new_render(tmp_path: Path, discovery: Discovery) -> None:
    err = _run(tmp_path, discovery, lambda cmd: (1, "") if "rollout" in cmd else (0, ""))
    assert getattr(err, "mutated", None) is True
    assert _disk(tmp_path) == _new_disk()


# ── supplied password hashes stay exactly when the spec is saved ──


def _saved_then_failed(*_: Any, after_persist: Any = None, **__: Any) -> None:
    """The mutation of a failure that moved the install: the spec is saved,
    the after-persist step runs, then the verb exits 1."""
    after_persist()
    raise typer.Exit(1)


def _unsaved(*_: Any, **__: Any) -> None:
    raise typer.Exit(1)


@pytest.mark.parametrize(
    ("mutation", "kept"), [(_saved_then_failed, True), (_unsaved, False)], ids=["saved", "unsaved"],
)
def test_user_import_keeps_the_hashes_only_when_the_spec_is_saved(
    tmp_path: Path, mutation: Any, kept: bool,
) -> None:
    install = _install(tmp_path)
    config = _dex_config(tmp_path, _entry("alice"))
    with patch("wip_deploy.cli._apply_and_persist_mutation", side_effect=mutation):
        result = _invoke(install, "user", "import", str(config), "--group", "wip-viewers")
    assert result.exit_code == 1, result.output
    assert (tmp_path / "secrets" / "dex-password-alice.bcrypt").exists() is kept


# ── the evidence a failed compose up is judged by reaches the real runtime ──


@pytest.mark.skipif(
    not (shutil.which("podman") or shutil.which("docker")), reason="no container runtime on PATH",
)
def test_the_container_listing_is_accepted_by_the_runtime() -> None:
    """The listing that decides whether a failed `compose up` moved anything
    asked for a field podman does not have (`{{.Id}}`): podman refused the
    format, the listing was None, and every failed up counted as a change.
    The unit tests mock the listing, and a runtime formats nothing (and so
    refuses nothing) without a row, so this lists every container."""
    cli = "podman" if shutil.which("podman") else "docker"
    listing = subprocess.run(
        [cli, "ps", "-a", "--format", _CONTAINER_ROW_FORMAT], capture_output=True, text=True,
    )
    assert listing.returncode == 0, listing.stderr
    if not listing.stdout.strip():
        pytest.skip("no container to format")
    cid, _, name = listing.stdout.splitlines()[0].partition(" ")
    assert cid and name
