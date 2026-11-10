"""CASE-1230 item 2 — a repo root that is wrong must stop the command, not green-light it.

Four shapes were broken, all of them "it looked fine and was not":

  - The stamp in `deployment.deployer-state` was returned unchecked, so a
    `or find_repo_root()` fallback at the call site was unreachable. With the
    stamped checkout deleted, `rebuild` discovered nothing, refreshed nothing,
    and still printed `✓ Rebuilt`.
  - With no stamp and no WIP repo in scope, `find_repo_root` raised
    `FileNotFoundError` straight through to the operator as a traceback — where
    `verify` documents `2 — couldn't start`.
  - A dead stamp fell back to the working directory *silently*, and the verb
    persisted the substitute — permanently re-stamping the install to whichever
    checkout the operator happened to be standing in.
  - `validate-manifest` walked up from cwd although its own help names "the repo
    containing the running wip-deploy installation", and a root that discovers
    no manifests passed vacuously.

The item-2 fix for that last one created a fifth shape, covered at the bottom of
this file (CASE-1230 review #3, finding 2): only an *editable* install puts the
package inside a checkout, so starting the walk there stopped wheel and pipx
installs even when run from a WIP repo. The package walk now falls back to the
cwd walk — announced, because the root it lands on is not the one the help names.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from wip_deploy.cli import app

runner = CliRunner()
WIP_ROOT = Path(__file__).resolve().parents[3]

_DEPLOYMENT_DICT = {
    "metadata": {"name": "test-install"},
    "spec": {
        "target": "compose",
        "apps": [{"name": "wip-kb", "enabled": True}],
        "modules": {"optional": []},
        "auth": {"mode": "api-key-only", "gateway": False, "users": []},
        "network": {"hostname": "wip.localhost"},
        "images": {"tag_overrides": {}},
        "platform": {"compose": {"data_dir": "/tmp/wip-test-data"}, "dev": {}},
        "secrets": {"backend": "file", "location": "/tmp/wip-test-secrets"},
        "apply": {},
    },
}


def _write_state(
    install_dir: Path, repo_root: str | None, *, target: str = "compose"
) -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    payload: dict = {"wip_deploy_format_version": 1, "deployment": {
        **_DEPLOYMENT_DICT, "spec": {**_DEPLOYMENT_DICT["spec"], "target": target},
    }}
    if repo_root is not None:
        payload["repo_root"] = repo_root
    (install_dir / "deployment.deployer-state").write_text(json.dumps(payload, indent=2))


def _foreign_git_repo(tmp_path: Path) -> Path:
    """Looks like an app checkout: has `.git`, holds no WIP manifests."""
    foreign = tmp_path / "some-app-repo"
    (foreign / ".git").mkdir(parents=True)
    return foreign


def _no_repo_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("WIP_REPO_ROOT", raising=False)
    bare = tmp_path / "no-git-here"
    bare.mkdir(parents=True)
    monkeypatch.chdir(bare)
    return bare


# ── the stamp is checked, and says so when it is dead ──────────────


def test_a_live_stamp_is_used_as_is(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from wip_deploy.cli import _install_repo_root

    checkout = tmp_path / "wip-checkout"
    checkout.mkdir()
    install = tmp_path / "install"
    _write_state(install, str(checkout))
    assert _install_repo_root(install) == checkout
    assert capsys.readouterr().err == ""


def test_no_stamp_is_silent_because_there_is_nothing_to_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from wip_deploy.cli import _install_repo_root

    install = tmp_path / "install"
    _write_state(install, None)
    assert _install_repo_root(install) is None
    assert capsys.readouterr().err == ""


def test_a_dead_stamp_is_reported_and_treated_as_unknown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The fallback gets persisted, so the substitution must be on the record."""
    from wip_deploy.cli import _install_repo_root

    install = tmp_path / "install"
    _write_state(install, str(tmp_path / "already-deleted"))
    assert _install_repo_root(install) is None
    err = capsys.readouterr().err
    assert "already-deleted" in err
    assert "no longer exists" in err
    assert "--repo-root" in err


# ── rebuild: the leg that exists to pick up a Dockerfile edit ──────


def test_rebuild_falls_back_from_a_dead_stamp_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pre-fix this read the dead path, discovered nothing, and returned [] in
    silence — after which `rebuild` printed `✓ Rebuilt`. The fallback must be
    on the record, because the verb persists the substitute it landed on.
    """
    from wip_deploy.cli import _refresh_dev_build_contexts

    install = tmp_path / "install"
    _write_state(install, str(tmp_path / "gone-checkout"), target="dev")
    monkeypatch.delenv("WIP_REPO_ROOT", raising=False)
    monkeypatch.chdir(WIP_ROOT)

    assert _refresh_dev_build_contexts(install, ["dex"]) == []
    err = capsys.readouterr().err
    assert "gone-checkout" in err and "no longer exists" in err


def test_a_dev_install_with_no_repo_anywhere_stops_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A traceback is not an error message.

    `rebuild` publishes no exit-code contract of its own (unlike `verify`, whose
    docstring promises 2 for "couldn't start"), so it reports a failed operation
    as 1 — the code it already uses when the apply itself fails.
    """
    from wip_deploy.cli import _refresh_dev_build_contexts

    install = tmp_path / "install"
    _write_state(install, None, target="dev")
    _no_repo_dir(tmp_path, monkeypatch)

    with pytest.raises(typer.Exit) as exc_info:
        _refresh_dev_build_contexts(install, ["dex"])
    assert exc_info.value.exit_code == 1


# ── validate-manifest: the root its help promises ─────────────────


def test_validate_manifest_defaults_to_the_installed_repo_not_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run from an app checkout, it still validates against WIP's own manifests.

    Pre-fix the cwd walk matched `some-app-repo/.git`, discovery came back empty,
    and every reference in the manifest was reported against nothing.
    """
    monkeypatch.delenv("WIP_REPO_ROOT", raising=False)
    monkeypatch.chdir(_foreign_git_repo(tmp_path))
    manifest = WIP_ROOT / "apps" / "dev-kb" / "wip-app.yaml"

    result = runner.invoke(app, ["validate-manifest", str(manifest)])
    assert result.exit_code == 0, result.output


def test_a_root_holding_no_manifests_is_not_a_pass(tmp_path: Path) -> None:
    manifest = WIP_ROOT / "apps" / "dev-kb" / "wip-app.yaml"
    result = runner.invoke(
        app, ["validate-manifest", str(manifest), "--repo-root", str(_foreign_git_repo(tmp_path))]
    )
    assert result.exit_code == 1, result.output
    assert "not a World-in-a-Pie checkout" in result.output


# ── the package walk has a floor: wheel and pipx installs are not in a checkout ──


def _package_is_not_in_a_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the walk that starts at the package, leave the cwd walk working.

    That is exactly where a wheel or pipx install sits: `Path(__file__)` resolves
    under site-packages, no ancestor has the WIP shape, and the walk raises there
    even though the operator is standing in a WIP checkout.
    """
    import wip_deploy.cli as cli_mod

    real = cli_mod.find_repo_root

    def fake(start: Path | None = None) -> Path:
        if start is not None:
            raise FileNotFoundError("could not locate the WIP repo root above site-packages")
        return real()

    monkeypatch.setattr(cli_mod, "find_repo_root", fake)


def test_a_site_packages_install_falls_back_to_the_cwd_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix this exited 2 with "pass --repo-root" from inside a WIP checkout."""
    monkeypatch.delenv("WIP_REPO_ROOT", raising=False)
    _package_is_not_in_a_checkout(monkeypatch)
    monkeypatch.chdir(WIP_ROOT)

    result = runner.invoke(app, ["validate-manifest", str(WIP_ROOT / "apps" / "dev-kb" / "wip-app.yaml")])

    assert result.exit_code == 0, result.output
    assert "note:" in result.output and "working directory" in result.output
    assert "--repo-root" in result.output


def test_an_editable_install_does_not_announce_a_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The note is for the substitute, not for every run: from a checkout in the
    package's own ancestry there is nothing to report."""
    monkeypatch.delenv("WIP_REPO_ROOT", raising=False)
    monkeypatch.chdir(_foreign_git_repo(tmp_path))

    result = runner.invoke(app, ["validate-manifest", str(WIP_ROOT / "apps" / "dev-kb" / "wip-app.yaml")])

    assert result.exit_code == 0, result.output
    assert "note:" not in result.output


def test_a_fallback_onto_a_non_wip_cwd_is_still_caught_by_the_root_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Falling back is not a rubber stamp: if the cwd is not a WIP repo either,
    the discovery guard fails the run instead of validating references against
    nothing.
    """
    monkeypatch.delenv("WIP_REPO_ROOT", raising=False)
    _package_is_not_in_a_checkout(monkeypatch)
    monkeypatch.chdir(_foreign_git_repo(tmp_path))

    result = runner.invoke(app, ["validate-manifest", str(WIP_ROOT / "apps" / "dev-kb" / "wip-app.yaml")])

    assert result.exit_code == 1, result.output
    assert "not a World-in-a-Pie checkout" in result.output


def test_when_neither_walk_finds_a_repo_the_command_stops_at_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both walks empty is still the documented `2 — could not find a repo root`."""
    monkeypatch.delenv("WIP_REPO_ROOT", raising=False)
    import wip_deploy.cli as cli_mod

    def fake(start: Path | None = None) -> Path:
        raise FileNotFoundError("could not locate the WIP repo root")

    monkeypatch.setattr(cli_mod, "find_repo_root", fake)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["validate-manifest", str(WIP_ROOT / "apps" / "dev-kb" / "wip-app.yaml")])

    assert result.exit_code == 2, result.output
    assert "--repo-root" in result.output


# ── a mutation verb re-stamps loudly, never quietly ───────────────


def test_a_verb_falling_back_from_a_dead_stamp_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WIP_REPO_ROOT", raising=False)
    install = tmp_path / "install"
    _write_state(install, str(tmp_path / "gone-checkout"))
    monkeypatch.chdir(WIP_ROOT)

    from unittest.mock import patch

    with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
        result = runner.invoke(
            app, ["app-deploy", "wip-kb", "--tag", "sha-new", "--install-dir", str(install)]
        )
    assert result.exit_code == 0, result.output
    assert mutate.called
    assert "gone-checkout" in result.output
    assert "no longer exists" in result.output


def test_an_unfindable_root_stops_a_mutation_verb_before_it_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install = tmp_path / "install"
    _write_state(install, None)
    _no_repo_dir(tmp_path, monkeypatch)

    from unittest.mock import patch

    with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
        result = runner.invoke(app, ["app-deploy", "wip-kb", "--tag", "sha-new", "--install-dir", str(install)])
    assert result.exit_code == 1, result.output
    assert "--repo-root" in result.output
    mutate.assert_not_called()
