"""CASE-698 — `wip-deploy rotate-key` guards.

The happy path (remove secret → re-render → re-apply) reuses the
already-tested `_apply_and_persist_mutation` machinery and needs a live
apply, so the unit surface here is the guard: rotate-key must refuse a
name that is not a spec-declared config key (unknown, or a runtime key
that lives only in Mongo), before touching anything.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.cli import _persist_deployment, app

REPO_ROOT = Path(__file__).resolve().parents[3]
runner = CliRunner()


def _install(tmp_path: Path, api_keys: list[dict]) -> Path:
    d = build_deployment(
        BuildInputs(
            target="compose",
            variant="dev",
            hostname="localhost",
            tls="internal",
            compose_data_dir=tmp_path / "data",
            api_keys=api_keys,
        )
    )
    secrets = d.spec.secrets.model_copy(
        update={"location": str(tmp_path / "secrets")}
    )
    spec = d.spec.model_copy(update={"secrets": secrets})
    d = d.model_copy(update={"spec": spec})
    _persist_deployment(d, tmp_path, repo_root=REPO_ROOT)
    return tmp_path


class TestRotateKeyGuard:
    def test_unknown_key_refused(self, tmp_path: Path) -> None:
        install = _install(
            tmp_path,
            [{"name": "web-yac", "namespaces": ["library", "kb"], "grants": {"kb": "write"}}],
        )
        result = runner.invoke(
            app,
            [
                "rotate-key",
                "no-such-key",
                "--install-dir",
                str(install),
                "--repo-root",
                str(REPO_ROOT),
            ],
        )
        assert result.exit_code == 2
        # Names the declared config keys and points runtime keys elsewhere.
        assert "web-yac" in result.output
        assert "revoke" in result.output.lower()

    def test_no_keys_declared_refused(self, tmp_path: Path) -> None:
        install = _install(tmp_path, [])
        result = runner.invoke(
            app,
            [
                "rotate-key",
                "web-yac",
                "--install-dir",
                str(install),
                "--repo-root",
                str(REPO_ROOT),
            ],
        )
        assert result.exit_code == 2
        assert "(none)" in result.output
