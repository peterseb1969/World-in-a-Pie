"""CASE-1243 (7-secrets R4): the optional-module list in --help reads
WIP_REPO_ROOT when it is set, so on a host with several clones it describes the
one you deploy; it is built before --repo-root is parsed and cannot follow it."""

from __future__ import annotations

from pathlib import Path

import pytest

from wip_deploy import module_help


def test_wip_repo_root_decides_which_clone_the_help_lists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    comp = tmp_path / "components" / "only-here"
    comp.mkdir(parents=True)
    (comp / "wip-component.yaml").write_text(
        "metadata: {name: only-here, category: optional, description: 'A module only this clone has.'}\n"
    )
    monkeypatch.setenv("WIP_REPO_ROOT", str(tmp_path))
    module_help.optional_modules.cache_clear()
    try:
        assert module_help.optional_modules() == (("only-here", "A module only this clone has"),)
    finally:
        module_help.optional_modules.cache_clear()
