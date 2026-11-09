"""The golden harness guards the real venv (CASE-1114).

A backend golden scenario runs the setup script in a scratch clone whose
.venv is the real venv behind a symlink; an editable install there records
the scratch path in the real venv and dangles once the clone is deleted.
The harness checks after each scenario that no package points into the
scratch directory. These tests exercise that check against a fake venv, so
they never touch the real one and run without WIP_GOLDEN.
"""

from __future__ import annotations

import json
from pathlib import Path

import golden_lib as G
import pytest


def _fake_venv(root: Path) -> Path:
    site = root / ".venv" / "lib" / "python3.13" / "site-packages"
    site.mkdir(parents=True)
    return site


def test_a_clean_venv_passes(tmp_path, monkeypatch):
    site = _fake_venv(tmp_path)
    (site / "wip_archive-0.38.0.dist-info").mkdir()
    (site / "wip_archive-0.38.0.dist-info" / "direct_url.json").write_text(
        json.dumps({"url": f"file://{tmp_path}/repo/libs/wip-archive", "dir_info": {"editable": True}})
    )
    monkeypatch.setattr(G, "REPO_ROOT", tmp_path)
    G.assert_real_venv_untouched(tmp_path / "wip-golden-x")


def test_an_install_from_the_scratch_clone_is_named(tmp_path, monkeypatch):
    site = _fake_venv(tmp_path)
    scratch = tmp_path / "wip-golden-x"
    info = site / "wip_toolkit-0.26.2.dist-info"
    info.mkdir()
    (info / "direct_url.json").write_text(
        json.dumps({"url": f"file://{scratch}/wip/WIP-Toolkit", "dir_info": {"editable": True}})
    )
    (site / "__editable__.wip_mcp-1.0.pth").write_text(f"{scratch}/wip/components/mcp-server/src\n")
    monkeypatch.setattr(G, "REPO_ROOT", tmp_path)
    with pytest.raises(AssertionError) as exc:
        G.assert_real_venv_untouched(scratch)
    assert "wip_toolkit-0.26.2.dist-info" in str(exc.value)
    assert "__editable__.wip_mcp-1.0.pth" in str(exc.value)


def test_the_harness_tells_the_script_to_install_nothing():
    assert G.HOST_ISOLATION_ENV["WIP_SKIP_VENV_INSTALL"] == "1"


def test_every_module_the_script_installs_is_a_precondition():
    script = (G.REPO_ROOT / "scripts" / "setup-backend-agent.sh").read_text()
    for module in G.VENV_MODULES:
        assert f"import {module}" in script or f", {module}" in script, module
