"""Tests for the CLI: validate + show-spec verbs.

Uses typer's CliRunner against the real repo (manifests are real, not
fixtures) — the tests double as smoke tests that the whole pipeline
(build → discovery → validate) lines up end-to-end.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from wip_deploy.cli import app

REPO_ROOT = Path(__file__).parent.parent.parent.parent.resolve()

runner = CliRunner()


def _invoke(*args: str) -> object:
    return runner.invoke(app, list(args))


def _invoke_valid(*args: str) -> object:
    """Like _invoke but injects --registry unless one is already passed.
    Most validate tests want the image-resolvability happy path."""
    arglist = list(args)
    if "--registry" not in arglist and "--target" in arglist:
        target_idx = arglist.index("--target")
        if target_idx + 1 < len(arglist) and arglist[target_idx + 1] in ("compose", "k8s"):
            arglist.extend(["--registry", "ghcr.io/test"])
    return runner.invoke(app, arglist)


# ────────────────────────────────────────────────────────────────────
# validate
# ────────────────────────────────────────────────────────────────────


class TestValidateHappyPath:
    def test_standard_compose_succeeds(self) -> None:
        r = _invoke_valid(
            "validate",
            "--preset", "standard",
            "--target", "compose",
            "--hostname", "wip.local",
            "--repo-root", str(REPO_ROOT),
        )
        assert r.exit_code == 0, r.output
        assert "Deployment valid" in r.output
        assert "mcp-server" in r.output  # active component

    def test_headless_compose_succeeds(self) -> None:
        r = _invoke_valid(
            "validate",
            "--preset", "headless",
            "--target", "compose",
            "--hostname", "localhost",
            "--repo-root", str(REPO_ROOT),
        )
        assert r.exit_code == 0, r.output

    def test_full_compose_succeeds(self) -> None:
        r = _invoke_valid(
            "validate",
            "--preset", "full",
            "--target", "compose",
            "--hostname", "wip.local",
            "--repo-root", str(REPO_ROOT),
        )
        assert r.exit_code == 0, r.output
        # Full preset enables files → MinIO active
        assert "minio" in r.output
        assert "postgres" in r.output
        assert "nats" in r.output

    def test_k8s_target_succeeds(self) -> None:
        r = _invoke_valid(
            "validate",
            "--preset", "standard",
            "--target", "k8s",
            "--hostname", "wip-kubi.local",
            "--repo-root", str(REPO_ROOT),
        )
        assert r.exit_code == 0, r.output


class TestValidateActivationRules:
    def test_reporting_activates_postgres_and_nats(self) -> None:
        r = _invoke_valid(
            "validate",
            "--preset", "standard",
            "--add", "reporting-sync",
            "--target", "compose",
            "--hostname", "wip.local",
            "--repo-root", str(REPO_ROOT),
        )
        assert r.exit_code == 0, r.output
        assert "postgres" in r.output
        assert "nats" in r.output

    def test_standard_activates_postgres_but_not_ingest_gateway(self) -> None:
        # CASE-171: `standard` was restored to v1 semantics — everything
        # except ingest-gateway. So reporting-sync is in (postgres
        # therefore active), but ingest-gateway is not (lazy NATS-side
        # opt-in). Pinned to catch a regression like the one that
        # silently shipped in v2 where standard had only mcp-server.
        r = _invoke_valid(
            "validate",
            "--preset", "standard",
            "--target", "compose",
            "--hostname", "wip.local",
            "--repo-root", str(REPO_ROOT),
        )
        assert r.exit_code == 0, r.output
        component_line = next(
            line for line in r.output.splitlines() if line.startswith("Components:")
        )
        assert "postgres" in component_line, component_line
        assert "ingest-gateway" not in component_line, component_line

    def test_headless_does_not_activate_dex(self) -> None:
        r = _invoke_valid(
            "validate",
            "--preset", "headless",
            "--target", "compose",
            "--hostname", "localhost",
            "--repo-root", str(REPO_ROOT),
        )
        assert r.exit_code == 0, r.output
        component_line = next(
            line for line in r.output.splitlines() if line.startswith("Components:")
        )
        assert "dex" not in component_line
        assert "auth-gateway" not in component_line


class TestValidateAppsEnabling:
    def test_app_appears_in_enabled_apps(self) -> None:
        r = _invoke_valid(
            "validate",
            "--preset", "standard",
            "--target", "compose",
            "--hostname", "wip.local",
            "--app", "dnd",
            "--app", "react-console",
            "--repo-root", str(REPO_ROOT),
        )
        assert r.exit_code == 0, r.output
        apps_line = next(
            line for line in r.output.splitlines() if line.startswith("Apps:")
        )
        assert "dnd" in apps_line
        assert "react-console" in apps_line


class TestValidateFailure:
    def test_unknown_module_fails(self) -> None:
        r = _invoke(
            "validate",
            "--preset", "standard",
            "--target", "compose",
            "--hostname", "wip.local",
            "--add", "nosuch",
            "--repo-root", str(REPO_ROOT),
        )
        assert r.exit_code == 1
        assert "nosuch" in r.output

    def test_unknown_app_fails(self) -> None:
        r = _invoke(
            "validate",
            "--preset", "standard",
            "--target", "compose",
            "--hostname", "wip.local",
            "--app", "imaginary",
            "--repo-root", str(REPO_ROOT),
        )
        assert r.exit_code == 1
        assert "imaginary" in r.output

    def test_unknown_preset_fails(self) -> None:
        r = _invoke(
            "validate",
            "--preset", "nosuch",
            "--target", "compose",
            "--hostname", "wip.local",
            "--repo-root", str(REPO_ROOT),
        )
        assert r.exit_code == 2

    def test_letsencrypt_with_dot_local_fails(self) -> None:
        r = _invoke(
            "validate",
            "--preset", "standard",
            "--target", "compose",
            "--hostname", "wip.local",
            "--tls", "letsencrypt",
            "--repo-root", str(REPO_ROOT),
        )
        assert r.exit_code == 2
        assert "public hostname" in r.output


# ────────────────────────────────────────────────────────────────────
# show-spec
# ────────────────────────────────────────────────────────────────────


class TestShowSpec:
    def test_yaml_output_is_parseable(self) -> None:
        r = _invoke(
            "show-spec",
            "--preset", "standard",
            "--target", "compose",
            "--hostname", "wip.local",
            "--repo-root", str(REPO_ROOT),
        )
        assert r.exit_code == 0, r.output
        # Parse the YAML to confirm it's syntactically valid
        parsed = yaml.safe_load(r.output)
        assert parsed["kind"] == "Deployment"
        assert parsed["spec"]["target"] == "compose"
        assert parsed["spec"]["auth"]["mode"] == "oidc"

    def test_json_output_is_parseable(self) -> None:
        import json

        r = _invoke(
            "show-spec",
            "--preset", "standard",
            "--target", "compose",
            "--hostname", "wip.local",
            "--format", "json",
            "--repo-root", str(REPO_ROOT),
        )
        assert r.exit_code == 0, r.output
        parsed = json.loads(r.output)
        assert parsed["kind"] == "Deployment"

    def test_show_spec_does_not_require_valid_preset_against_manifests(self) -> None:
        """show-spec skips discovery, so it should work even if it would
        generate a technically-invalid deployment (e.g., unknown module)."""
        r = _invoke(
            "show-spec",
            "--preset", "standard",
            "--target", "compose",
            "--hostname", "wip.local",
            "--add", "this-module-does-not-exist-anywhere",
            "--repo-root", str(REPO_ROOT),
        )
        # build_deployment only validates the spec structure, not existence
        # of module manifests. Discovery is skipped. So this succeeds.
        assert r.exit_code == 0, r.output


# ────────────────────────────────────────────────────────────────────
# Root / version
# ────────────────────────────────────────────────────────────────────


class TestVersion:
    def test_version_flag(self) -> None:
        r = _invoke("--version")
        assert r.exit_code == 0
        assert "wip-deploy" in r.output


class TestAppSourceFlag:
    """CASE-55: --app-source NAME=PATH for hot-reload dev against a full WIP stack."""

    def test_parse_single(self, tmp_path: Path) -> None:
        from wip_deploy.cli import _parse_app_sources
        result = _parse_app_sources([f"rc={tmp_path}"])
        assert result == {"rc": tmp_path}

    def test_parse_multiple(self, tmp_path: Path) -> None:
        from wip_deploy.cli import _parse_app_sources
        d1 = tmp_path / "app1"
        d1.mkdir()
        d2 = tmp_path / "app2"
        d2.mkdir()
        result = _parse_app_sources([f"a1={d1}", f"a2={d2}"])
        assert result == {"a1": d1, "a2": d2}

    def test_missing_equals_raises(self) -> None:
        from wip_deploy.cli import _parse_app_sources
        with pytest.raises(ValueError, match="missing '='"):
            _parse_app_sources(["rc/path/with/no/equals"])

    def test_empty_name_raises(self, tmp_path: Path) -> None:
        from wip_deploy.cli import _parse_app_sources
        with pytest.raises(ValueError, match="non-empty"):
            _parse_app_sources([f"={tmp_path}"])

    def test_nonexistent_path_raises(self) -> None:
        from wip_deploy.cli import _parse_app_sources
        with pytest.raises(ValueError, match="not a directory"):
            _parse_app_sources(["rc=/definitely/not/a/real/path/xyz"])


# CASE-171 Phase A — restart verb, hostname default resolver, standard preset shape


class TestResolveHostname:
    """Hostname default depends on target (CASE-171 #7)."""

    def test_explicit_hostname_overrides(self) -> None:
        from wip_deploy.cli import _resolve_hostname
        assert _resolve_hostname("custom.host", "dev") == "custom.host"
        assert _resolve_hostname("custom.host", "compose") == "custom.host"

    def test_dev_target_defaults_to_localhost(self) -> None:
        from wip_deploy.cli import _resolve_hostname
        assert _resolve_hostname(None, "dev") == "localhost"

    def test_compose_target_defaults_to_wip_local(self) -> None:
        from wip_deploy.cli import _resolve_hostname
        assert _resolve_hostname(None, "compose") == "wip.local"

    def test_k8s_target_defaults_to_wip_local(self) -> None:
        from wip_deploy.cli import _resolve_hostname
        assert _resolve_hostname(None, "k8s") == "wip.local"


class TestRestartVerb:
    """The restart verb (CASE-171 #4)."""

    def test_restart_help_renders(self) -> None:
        from typer.testing import CliRunner

        from wip_deploy.cli import app
        r = CliRunner().invoke(app, ["restart", "--help"])
        assert r.exit_code == 0
        # Body content rather than option names — option names truncate
        # in narrow terminals; content stays stable.
        assert "Restart one or more services" in r.output

    def test_restart_requires_at_least_one_service(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from wip_deploy.cli import app
        # No services and a fake install dir → typer surfaces "missing argument"
        r = CliRunner().invoke(
            app, ["restart", "--install-dir", str(tmp_path)]
        )
        assert r.exit_code != 0


class TestStandardPresetShape:
    """CASE-171 #1 — restored v1 semantics."""

    def test_standard_includes_reporting_minio_mcp(self) -> None:
        from wip_deploy.presets import PRESETS
        opt = set(PRESETS["standard"]["modules"]["optional"])
        assert {"reporting-sync", "minio", "mcp-server"} <= opt

    def test_standard_does_not_include_ingest_gateway(self) -> None:
        from wip_deploy.presets import PRESETS
        opt = set(PRESETS["standard"]["modules"]["optional"])
        assert "ingest-gateway" not in opt
