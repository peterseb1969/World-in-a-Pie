"""Contract tests for the existing-install guard on `wip-deploy install`.

`install` builds its spec from CLI flags alone — it never loads the
saved spec — so a re-run with reduced flags renders a defaults-shaped
config over whatever is deployed. The guard compares the new spec
against the persisted previous one and aborts (without --reconcile)
when the re-run would drop apps/modules or change identity fields
(target, hostname, TLS, ports, registry, auth mode). It must run
BEFORE spec validation, so its actionable message wins over incidental
validation errors — on a live install, a missing --registry error once
fired first and masked exactly this situation.

Two layers:

- `_diff_spec_for_identity_drift` as a pure function over two
  Deployments (mirror of TestDiffSpecForDrops in test_case_331.py).
- CLI-level: a persisted state file + a diverging re-run must abort
  with the exists message — including when the re-run would ALSO fail
  validation (ordering regression).
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wip_deploy.cli import (
    _DEPLOYMENT_JSON_VERSION,
    _diff_spec_for_identity_drift,
    app,
)
from wip_deploy.spec.deployment import (
    ApplySpec,
    AppRef,
    AuthSpec,
    ComposePlatform,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    ImagesSpec,
    ModulesSpec,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)

runner = CliRunner()


def _make_deployment(
    *,
    hostname: str = "localhost",
    tls: str = "internal",
    https_port: int = 8443,
    http_port: int = 8080,
    registry: str | None = None,
    auth_mode: str = "api-key-only",
    apps: list[tuple[str, bool]] | None = None,
    modules: list[str] | None = None,
) -> Deployment:
    """Minimal compose-target Deployment with overridable identity fields."""
    return Deployment(
        metadata=DeploymentMetadata(name="test"),
        spec=DeploymentSpec(
            target="compose",
            apps=[AppRef(name=n, enabled=e) for n, e in (apps or [])],
            modules=ModulesSpec(optional=modules or []),
            auth=AuthSpec(mode=auth_mode, gateway=False),
            network=NetworkSpec(
                hostname=hostname,
                tls=tls,
                https_port=https_port,
                http_port=http_port,
            ),
            images=ImagesSpec(registry=registry),
            platform=PlatformSpec(
                compose=ComposePlatform(data_dir=Path("/tmp/wip-test-data")),
            ),
            secrets=SecretsSpec(backend="file", location="/tmp/wip-test-secrets"),
            apply=ApplySpec(),
        ),
    )


def _persist(install_dir: Path, deployment: Deployment) -> None:
    """Write a deployer-state file the way _persist_deployment does."""
    install_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "wip_deploy_format_version": _DEPLOYMENT_JSON_VERSION,
        "deployment": deployment.model_dump(mode="json"),
    }
    (install_dir / "deployment.deployer-state").write_text(
        json.dumps(payload, indent=2, default=str) + "\n"
    )


# ────────────────────────────────────────────────────────────────────
# _diff_spec_for_identity_drift — pure-function contract
# ────────────────────────────────────────────────────────────────────


class TestDiffSpecForIdentityDrift:
    def test_identical_specs_report_no_drift(self) -> None:
        prev = _make_deployment(hostname="wip.example.com", registry="ghcr.io/x")
        curr = _make_deployment(hostname="wip.example.com", registry="ghcr.io/x")
        assert _diff_spec_for_identity_drift(prev, curr) == []

    def test_hostname_change_is_drift(self) -> None:
        prev = _make_deployment(hostname="cloud.example.com")
        curr = _make_deployment(hostname="localhost")
        drift = _diff_spec_for_identity_drift(prev, curr)
        assert drift == [("network.hostname", "cloud.example.com", "localhost")]

    def test_registry_reverting_to_none_is_drift(self) -> None:
        prev = _make_deployment(registry="ghcr.io/peterseb1969")
        curr = _make_deployment(registry=None)
        drift = _diff_spec_for_identity_drift(prev, curr)
        assert drift == [("images.registry", "ghcr.io/peterseb1969", "(none)")]

    def test_tls_and_ports_changes_are_drift(self) -> None:
        # letsencrypt requires a public hostname, so hold hostname fixed
        # at one and vary only tls + ports between the two specs.
        prev = _make_deployment(
            hostname="wip.example.com",
            tls="letsencrypt",
            https_port=443,
            http_port=80,
        )
        curr = _make_deployment(
            hostname="wip.example.com",
            tls="internal",
            https_port=8443,
            http_port=8080,
        )
        fields = {f for f, _, _ in _diff_spec_for_identity_drift(prev, curr)}
        assert fields == {
            "network.tls",
            "network.https_port",
            "network.http_port",
        }

    def test_auth_mode_change_is_drift(self) -> None:
        prev = _make_deployment(auth_mode="hybrid")
        curr = _make_deployment(auth_mode="api-key-only")
        drift = _diff_spec_for_identity_drift(prev, curr)
        assert drift == [("auth.mode", "hybrid", "api-key-only")]

    def test_image_tag_change_is_not_drift(self) -> None:
        """Tag rolls are the routine re-run intent — never flagged."""
        prev = _make_deployment(registry="ghcr.io/x")
        curr = _make_deployment(registry="ghcr.io/x")
        prev.spec.images.tag = "20260701a"
        curr.spec.images.tag = "20260712c"
        assert _diff_spec_for_identity_drift(prev, curr) == []


# ────────────────────────────────────────────────────────────────────
# CLI-level: the guard aborts a diverging re-run, before validation
# ────────────────────────────────────────────────────────────────────


class TestInstallExistingGuardCLI:
    def test_diverging_rerun_aborts_with_exists_message(
        self, tmp_path: Path
    ) -> None:
        """Saved spec has a real hostname + registry; a bare re-run
        (defaults) must abort on the guard, naming what would change."""
        _persist(
            tmp_path,
            _make_deployment(
                hostname="cloud.example.com",
                registry="ghcr.io/peterseb1969",
                apps=[("wip-kb", True)],
            ),
        )
        result = runner.invoke(
            app,
            [
                "install",
                "--name", "guard-test",
                "--install-dir", str(tmp_path),
                "--registry", "ghcr.io/peterseb1969",
            ],
        )
        assert result.exit_code == 2
        assert "already exists" in result.output
        assert "network.hostname" in result.output
        assert "redeploy" in result.output

    def test_guard_fires_before_validation_errors(self, tmp_path: Path) -> None:
        """The ordering regression: a re-run that BOTH diverges AND fails
        validation (compose without --registry) must surface the
        actionable exists message, not the registry validation error —
        the validation error firing first is what masked the guard on a
        live production install."""
        _persist(
            tmp_path,
            _make_deployment(
                hostname="cloud.example.com",
                registry="ghcr.io/peterseb1969",
            ),
        )
        result = runner.invoke(
            app,
            [
                "install",
                "--name", "guard-test",
                "--install-dir", str(tmp_path),
                # no --registry: validation would reject compose without it
            ],
        )
        assert result.exit_code == 2
        assert "already exists" in result.output
        assert "images.registry" in result.output
        # The masking error must NOT be what the operator sees.
        assert "needs pre-built images" not in result.output

    def test_identical_rerun_passes_the_guard(self, tmp_path: Path) -> None:
        """A re-run that fully re-specifies the saved identity must not
        abort on the guard. The invocation then proceeds into the
        normal flow — here validation (exit 1), proving the guard
        stayed silent."""
        _persist(
            tmp_path,
            _make_deployment(
                hostname="localhost",
                registry=None,
                auth_mode="api-key-only",
            ),
        )
        result = runner.invoke(
            app,
            [
                "install",
                "--name", "guard-test",
                "--install-dir", str(tmp_path),
                "--hostname", "localhost",
                "--auth-mode", "api-key-only",
                "--no-auth-gateway",
                # compose + no registry fails validation — AFTER the guard
            ],
        )
        assert result.exit_code == 1
        assert "already exists" not in result.output
        assert "needs pre-built images" in result.output

    def test_reconcile_overrides_the_guard(self, tmp_path: Path) -> None:
        """--reconcile is the deliberate reshape opt-in: the guard stays
        silent and the run proceeds to the next gate (validation here,
        exit 1)."""
        _persist(
            tmp_path,
            _make_deployment(hostname="cloud.example.com"),
        )
        result = runner.invoke(
            app,
            [
                "install",
                "--name", "guard-test",
                "--install-dir", str(tmp_path),
                "--reconcile",
                # no --registry → validation error, proving we got past the guard
            ],
        )
        assert result.exit_code == 1
        assert "already exists" not in result.output
        assert "needs pre-built images" in result.output