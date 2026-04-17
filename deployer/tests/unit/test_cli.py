"""Tests for the CLI: validate + show-spec verbs.

Uses typer's CliRunner against the real repo (manifests are real, not
fixtures) — the tests double as smoke tests that the whole pipeline
(build → discovery → validate) lines up end-to-end.
"""

from __future__ import annotations

from pathlib import Path

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
        assert "console" in r.output  # active component

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

    def test_standard_does_not_activate_postgres(self) -> None:
        r = _invoke_valid(
            "validate",
            "--preset", "standard",
            "--target", "compose",
            "--hostname", "wip.local",
            "--repo-root", str(REPO_ROOT),
        )
        assert r.exit_code == 0, r.output
        # postgres shouldn't be listed
        component_line = next(
            line for line in r.output.splitlines() if line.startswith("Components:")
        )
        assert "postgres" not in component_line

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
