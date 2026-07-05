"""Golden-snapshot tests over the scaffold scripts (CASE-612, step 1).

Coverage matrix per docs/design/scaffold-revamp.md (post-CASE-610):
- backend: local x {create, refresh} x {tier2, tier3}   (4 — ssh/http are
  ported by inspection per the OQ3 resolution recorded in CASE-612)
- app:     {create, refresh} x {tier2, tier3} x {standard} + create x query
  (query x refresh / query x tier3 excluded: the preset only affects
  create-time file copies, refresh never re-copies preset files, and tier
  is orthogonal to preset — the standard-preset refresh/tier fixtures
  cover those code paths)
- modifier flags (--force-claude-md, --with-bootstrap) as targeted
  single-purpose fixtures, not a cross-product.

Run modes:
    WIP_GOLDEN=1                      compare against committed fixtures
    WIP_GOLDEN=1 WIP_GOLDEN_UPDATE=1  (re)capture fixtures
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

import golden_lib as G

pytestmark = pytest.mark.golden

UPDATE = os.environ.get("WIP_GOLDEN_UPDATE") == "1"


def _check(name: str, snap: dict) -> None:
    if UPDATE or not G.fixture_path(name).exists():
        G.save_fixture(name, snap)
        if not UPDATE:
            pytest.fail(
                f"fixture {name} did not exist — captured now; review + commit "
                f"{G.fixture_path(name)}, then re-run"
            )
        return
    deltas = G.diff_snapshots(G.load_fixture(name), snap)
    assert not deltas, f"{name} drifted from fixture:\n" + "\n\n".join(deltas)


@pytest.fixture()
def tmp() -> Path:
    d = Path(tempfile.mkdtemp(prefix="wip-golden-"))
    yield d
    G.cleanup_tmp(d)


# --- backend: local x {create,refresh} x {tier2,tier3} ---------------------

@pytest.mark.parametrize("tier3", [False, True], ids=["tier2", "tier3"])
@pytest.mark.parametrize("refresh", [False, True], ids=["create", "refresh"])
def test_backend(tmp: Path, tier3: bool, refresh: bool):
    name = f"backend-local-{'refresh' if refresh else 'create'}-{'tier3' if tier3 else 'tier2'}"
    snap = G.run_backend_scenario(tmp, tier3=tier3, refresh=refresh)
    _check(name, snap)


# --- app: {create,refresh} x {tier2,tier3} x standard + create x query -----

@pytest.mark.parametrize("tier3", [False, True], ids=["tier2", "tier3"])
@pytest.mark.parametrize("refresh", [False, True], ids=["create", "refresh"])
def test_app_standard(tmp: Path, tier3: bool, refresh: bool):
    mode = "refresh" if refresh else "create"
    tier = "tier3" if tier3 else "tier2"
    name = f"app-standard-{mode}-{tier}"
    snap = G.run_app_scenario(
        tmp, preset="standard", tier3=tier3, refresh=refresh,
        slug=f"{mode}-{tier}",
    )
    _check(name, snap)


def test_app_query_create(tmp: Path):
    snap = G.run_app_scenario(
        tmp, preset="query", tier3=False, refresh=False, slug="query"
    )
    _check("app-query-create-tier2", snap)


# --- targeted modifier fixtures --------------------------------------------

def test_app_force_claude_md(tmp: Path):
    """--force-claude-md on refresh overwrites CLAUDE.md instead of writing
    the .refresh sidecar."""
    import subprocess

    app_dir = tmp / "force"
    base = [
        "bash", str(G.REPO_ROOT / "scripts/create-app-project.sh"),
        str(app_dir), "--name", "Golden force", "--prefix", "APP-GLD",
    ]
    r1 = G._run(base, cwd=G.REPO_ROOT)
    assert r1.returncode == 0, r1.stdout + r1.stderr
    # Mutate CLAUDE.md the way an app would, then force-refresh.
    claude = app_dir / "CLAUDE.md"
    claude.write_text(claude.read_text() + "\n## App-authored section\n")
    r2 = G._run(base + ["--force-claude-md"], cwd=G.REPO_ROOT)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    G._cleanup_namespace("dev-golden-force")

    assert not (app_dir / "CLAUDE.md.refresh").exists(), (
        "--force-claude-md must overwrite in place, not write the sidecar"
    )
    assert "App-authored section" not in claude.read_text(), (
        "--force-claude-md must discard app-authored content"
    )
    snap = G.snapshot_tree(app_dir, G._norm_roots(__APP_DIR__=str(app_dir)))
    _check("app-modifier-force-claude-md", snap)


def test_app_with_bootstrap_retrofit(tmp: Path):
    """--with-bootstrap retrofits templates/bootstrap/ ONLY when absent
    (never resurrects a deliberately-deleted dir without the flag; with the
    flag + present dir it is left as-is)."""
    import shutil as sh

    app_dir = tmp / "boot"
    base = [
        "bash", str(G.REPO_ROOT / "scripts/create-app-project.sh"),
        str(app_dir), "--name", "Golden boot", "--prefix", "APP-GLD",
    ]
    r1 = G._run(base, cwd=G.REPO_ROOT)
    assert r1.returncode == 0, r1.stdout + r1.stderr
    assert (app_dir / "templates/bootstrap").is_dir(), "create must seed genesis templates"

    # Simulate the genesis-banner discipline: app built bootstrap.ts, deleted the dir.
    sh.rmtree(app_dir / "templates/bootstrap")

    # Plain refresh must NOT resurrect.
    r2 = G._run(base, cwd=G.REPO_ROOT)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert not (app_dir / "templates/bootstrap").exists(), (
        "plain refresh must not resurrect a deleted templates/bootstrap/"
    )

    # --with-bootstrap retrofits when absent.
    r3 = G._run(base + ["--with-bootstrap"], cwd=G.REPO_ROOT)
    assert r3.returncode == 0, r3.stdout + r3.stderr
    assert (app_dir / "templates/bootstrap").is_dir(), (
        "--with-bootstrap must retrofit when the dir is absent"
    )
    G._cleanup_namespace("dev-golden-boot")

    snap = G.snapshot_tree(app_dir, G._norm_roots(__APP_DIR__=str(app_dir)))
    _check("app-modifier-with-bootstrap", snap)
