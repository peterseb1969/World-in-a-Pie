"""A service compose up never created is judged by the same evidence as a
failed up (CASE-1229, review of 7603f47f, R2).

podman-compose can exit 0 while podman refused to create a container, and
_check_containers_created then raises. That failure belongs to the up step:
when this install's containers stand exactly as they did before, the running
deployment did not move, and the error must say so (mutated False), so the
old spec stays the last-known-good. Only when something did change is the
new spec persisted.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from wip_deploy.apply import ApplyError, apply_compose

from . import test_case_443 as t443


def _apply_with_a_missing_container(tmp_path: Path, after: frozenset) -> ApplyError:
    before = frozenset({("default_wip-registry", "id-1")})
    with (
        patch("wip_deploy.apply._detect_compose_cmd", return_value=["podman-compose"]),
        patch("wip_deploy.apply._run_build"),
        patch("wip_deploy.apply._run_up"),
        patch("wip_deploy.apply._remove_install_containers"),
        patch(
            "wip_deploy.apply._check_containers_created",
            side_effect=ApplyError("compose up left wip-x without a container"),
        ),
        patch("wip_deploy.apply._container_cli", return_value="podman"),
        patch("wip_deploy.apply._project_container_state", side_effect=[before, after]),
        pytest.raises(ApplyError) as raised,
    ):
        apply_compose(
            deployment=t443._deployment(), components=[], apps=[],
            tree=t443._tree({}), install_dir=tmp_path, services_scope=None,
        )
    return raised.value


def test_a_missing_container_with_nothing_moved_is_not_a_mutation(tmp_path: Path) -> None:
    err = _apply_with_a_missing_container(
        tmp_path, frozenset({("default_wip-registry", "id-1")}),
    )
    assert err.mutated is False


def test_a_missing_container_after_others_moved_is_a_mutation(tmp_path: Path) -> None:
    err = _apply_with_a_missing_container(
        tmp_path, frozenset({("default_wip-registry", "id-2")}),
    )
    assert err.mutated is True
