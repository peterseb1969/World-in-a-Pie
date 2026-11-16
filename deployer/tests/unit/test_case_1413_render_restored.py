"""A compose apply that keeps the old spec puts the old render back.

apply_compose writes the new render into the install dir before any step
that can fail. When the failure leaves the running deployment where it
was (mutated False), the CLI keeps the previous spec as the last-known-
good, but `up`, `rebuild` and `start` deploy from the install dir's
rendered files: after a refused add-app, `wip-deploy up` would bring up
the app the spec refused, with its route. The previous render is now
restored on such a failure: files the new render changed get their old
content back, and files it added are removed. A failure that did move the
deployment keeps the new render, which matches the spec then persisted.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from wip_deploy.apply import ApplyError, apply_compose
from wip_deploy.renderers.base import FileTree

from . import test_case_443 as t443

OLD = {
    "docker-compose.yaml": "services: {registry: {image: wip-registry}}\n",
    "config/caddy/Caddyfile": "old routes\n",
    "config/gone-in-new.txt": "only the old render had me\n",
}


def _previous_render(root: Path) -> None:
    for rel, content in OLD.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(content)


def _new_tree() -> FileTree:
    tree = FileTree()
    tree.add("docker-compose.yaml", "services:\n  registry: {image: wip-registry}\n  clintrial: {build: {context: ./build/clintrial}}\n")
    tree.add("config/caddy/Caddyfile", "old routes\nclintrial route\n")
    tree.add("secrets/app-clintrial-api-key", "new-secret\n", mode=0o600)
    tree.obsolete.add(Path("config/gone-in-new.txt"))
    return tree


def _disk(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): p.read_text()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def _apply(root: Path, *, build_error=None, created_error=None, after=None) -> ApplyError:
    before = frozenset({("test-install_wip-registry", "id-1")})
    with (
        patch("wip_deploy.apply._detect_compose_cmd", return_value=["podman-compose"]),
        patch("wip_deploy.apply._run_build", side_effect=build_error),
        patch("wip_deploy.apply._run_up"),
        patch("wip_deploy.apply._remove_install_containers"),
        patch("wip_deploy.apply._check_containers_created", side_effect=created_error),
        patch("wip_deploy.apply._container_cli", return_value="podman"),
        patch("wip_deploy.apply._project_container_state",
              side_effect=[before, after if after is not None else before]),
        pytest.raises(ApplyError) as raised,
    ):
        apply_compose(
            deployment=t443._deployment("dev" if build_error else "compose"),
            components=[], apps=[], tree=_new_tree(), install_dir=root,
            services_scope=["clintrial"],
        )
    return raised.value


def test_a_refused_container_puts_the_previous_render_back(tmp_path: Path) -> None:
    _previous_render(tmp_path)
    err = _apply(tmp_path, created_error=ApplyError("compose up left clintrial without a container"))
    assert err.mutated is False
    assert _disk(tmp_path) == OLD


def test_a_failed_build_puts_the_previous_render_back(tmp_path: Path) -> None:
    _previous_render(tmp_path)
    err = _apply(tmp_path, build_error=ApplyError("podman-compose build failed (exit 125)"))
    assert err.mutated is False
    assert _disk(tmp_path) == OLD


def test_a_failed_first_install_leaves_no_render(tmp_path: Path) -> None:
    err = _apply(tmp_path, created_error=ApplyError("compose up left clintrial without a container"))
    assert err.mutated is False
    assert _disk(tmp_path) == {}


def test_a_failure_that_moved_the_deployment_keeps_the_new_render(tmp_path: Path) -> None:
    _previous_render(tmp_path)
    err = _apply(
        tmp_path,
        created_error=ApplyError("compose up left clintrial without a container"),
        after=frozenset({("test-install_wip-registry", "id-2")}),
    )
    assert err.mutated is True
    disk = _disk(tmp_path)
    assert "clintrial" in disk["docker-compose.yaml"]
    assert "config/gone-in-new.txt" not in disk
