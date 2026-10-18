"""`wip-deploy render` is a dry run: it writes the tree it was asked to write
and nothing else.

Render used to share install's secret path, which persists every secret it
resolves into the store (default `~/.wip-deploy/<name>/secrets`) whatever
`--output-dir` said. A preview therefore created credential directories in
the home directory and added missing secrets to a live install's store.
Without `--output-dir` it also wrote a tree assembled from command-line
flags over the install's own rendered files, which `rebuild`/`restart`/`start`
then act on.

Contract under test: a render never creates or modifies a secret store,
never writes into a directory holding a live install, and still reads an
existing store's values so a preview stays faithful.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from wip_deploy.cli import app

REPO_ROOT = Path(__file__).resolve().parents[3]
runner = CliRunner()
_RENDER = [
    "render", "--registry", "ghcr.io/peterseb1969", "--tag", "t",
    "--repo-root", str(REPO_ROOT),
]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    assert Path.home() == home
    return home


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(directory)): p.read_bytes()
        for p in sorted(directory.rglob("*")) if p.is_file()
    }


def test_preview_leaves_the_home_directory_untouched(home: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = runner.invoke(app, [*_RENDER, "--name", "preview", "--output-dir", str(out)])
    assert result.exit_code == 0, result.output
    assert (out / "docker-compose.yaml").is_file()
    assert (out / ".env").is_file()
    assert not (home / ".wip-deploy").exists()
    assert "No secret store was created or changed" in result.output


def test_existing_store_is_read_but_never_modified(home: Path, tmp_path: Path) -> None:
    store = tmp_path / "store"
    store.mkdir()
    (store / "api-key").write_text("existing-api-key-value\n")
    before = _snapshot(store)

    out = tmp_path / "out"
    result = runner.invoke(app, [
        *_RENDER, "--name", "preview", "--secrets-location", str(store),
        "--output-dir", str(out),
    ])
    assert result.exit_code == 0, result.output
    assert _snapshot(store) == before  # nothing added, nothing rewritten
    assert "existing-api-key-value" in (out / ".env").read_text()  # still faithful


def test_refuses_to_render_over_a_live_install(home: Path, tmp_path: Path) -> None:
    install = tmp_path / "live"
    install.mkdir()
    (install / "deployment.deployer-state").write_text("{}\n")
    (install / "docker-compose.yaml").write_text("LIVE\n")

    result = runner.invoke(app, [*_RENDER, "--name", "live", "--output-dir", str(install)])
    assert result.exit_code == 2
    assert "holds a live install" in result.output
    assert (install / "docker-compose.yaml").read_text() == "LIVE\n"


def test_default_destination_of_a_live_install_is_refused_too(home: Path) -> None:
    install = home / ".wip-deploy" / "live"
    install.mkdir(parents=True)
    (install / "deployment.deployer-state").write_text("{}\n")
    (install / "docker-compose.yaml").write_text("LIVE\n")

    result = runner.invoke(app, [*_RENDER, "--name", "live"])
    assert result.exit_code == 2
    assert (install / "docker-compose.yaml").read_text() == "LIVE\n"
    assert not (install / "secrets").exists()
