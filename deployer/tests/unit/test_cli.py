"""Tests for the CLI: validate + show-spec verbs.

Uses typer's CliRunner against the real repo (manifests are real, not
fixtures) — the tests double as smoke tests that the whole pipeline
(build → discovery → validate) lines up end-to-end.
"""

from __future__ import annotations

import json
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
        assert parsed["spec"]["auth"]["mode"] == "hybrid"  # CASE-374 default

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

    def test_k8s_name_without_namespace_back_derives_namespace(self) -> None:
        """CASE-365: passing --name X --target k8s (without --namespace)
        renders the deployment with k8s namespace=X, not the default 'wip'.

        End-to-end through the CLI: the case body's exact reproducer
        scenario, asserted on the assembled spec.
        """
        r = _invoke(
            "show-spec",
            "--preset", "standard",
            "--target", "k8s",
            "--hostname", "wip-kb.local",
            "--name", "wip-kb",
            "--repo-root", str(REPO_ROOT),
        )
        assert r.exit_code == 0, r.output
        parsed = yaml.safe_load(r.output)
        assert parsed["spec"]["platform"]["k8s"]["namespace"] == "wip-kb"

    def test_k8s_namespace_only_still_works(self) -> None:
        """CASE-365 regression: the original one-directional rule still
        applies — --namespace X (without --name) → namespace=X."""
        r = _invoke(
            "show-spec",
            "--preset", "standard",
            "--target", "k8s",
            "--hostname", "wip-kb.local",
            "--namespace", "wip-kb",
            "--repo-root", str(REPO_ROOT),
        )
        assert r.exit_code == 0, r.output
        parsed = yaml.safe_load(r.output)
        assert parsed["spec"]["platform"]["k8s"]["namespace"] == "wip-kb"
        assert parsed["metadata"]["name"] == "wip-kb"

    def test_k8s_name_and_namespace_diverge_explicitly(self) -> None:
        """CASE-365 intentional-asymmetry: passing both --name foo
        --namespace bar yields foo as install-dir name AND bar as
        k8s namespace. Operators who want them to differ pass both
        explicitly."""
        r = _invoke(
            "show-spec",
            "--preset", "standard",
            "--target", "k8s",
            "--hostname", "wip-kb.local",
            "--name", "foo",
            "--namespace", "bar",
            "--repo-root", str(REPO_ROOT),
        )
        assert r.exit_code == 0, r.output
        parsed = yaml.safe_load(r.output)
        assert parsed["metadata"]["name"] == "foo"
        assert parsed["spec"]["platform"]["k8s"]["namespace"] == "bar"


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

    @pytest.fixture(autouse=True)
    def isolated_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> Path:
        """Sandbox `Path.home()` so the CLI never touches the real
        ~/.wip-deploy/. Without this, real-machine state in
        ~/.wip-deploy/apps/ (the CASE-356 registry) leaks into tests
        and trips the shadow-warning code path. CASE-366."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        return fake_home

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

    def test_app_source_implicitly_enables_app(self, tmp_path: Path) -> None:
        """CASE-313 (related): --app-source NAME=PATH must enable NAME, even
        when --app NAME isn't passed separately. Bind-mounting source for an
        app you didn't enable has no coherent meaning, and the help-text
        example shows --app-source on its own — past behaviour silently
        dropped the app from the deployment, which is what produced the
        react-console regression on 2026-05-08.
        """
        rc_src = tmp_path / "WIP-ReactConsole"
        rc_src.mkdir()

        r = _invoke(
            "show-spec",
            "--preset", "standard",
            "--target", "dev",
            "--hostname", "localhost",
            "--format", "json",
            "--app-source", f"react-console={rc_src}",
            "--repo-root", str(REPO_ROOT),
        )
        assert r.exit_code == 0, r.output
        parsed = json.loads(r.output)

        # react-console should be in the apps list with enabled=True even
        # though we didn't pass --app react-console.
        app_entries = parsed["spec"]["apps"]
        assert any(
            a["name"] == "react-console" and a.get("enabled", True) is True
            for a in app_entries
        ), f"react-console not auto-enabled in apps list: {app_entries}"

        # And the source mount is recorded, of course.
        app_sources = parsed["spec"]["platform"]["dev"]["app_sources"]
        assert "react-console" in app_sources

    def test_app_source_does_not_duplicate_existing_app(self, tmp_path: Path) -> None:
        """When the user passes BOTH --app NAME and --app-source NAME=PATH,
        NAME appears once in the apps list, not twice. The implicit-enable
        is a set-union, not a list-append.
        """
        rc_src = tmp_path / "WIP-ReactConsole"
        rc_src.mkdir()

        r = _invoke(
            "show-spec",
            "--preset", "standard",
            "--target", "dev",
            "--hostname", "localhost",
            "--format", "json",
            "--app", "react-console",
            "--app-source", f"react-console={rc_src}",
            "--repo-root", str(REPO_ROOT),
        )
        assert r.exit_code == 0, r.output
        parsed = json.loads(r.output)

        rc_entries = [a for a in parsed["spec"]["apps"] if a["name"] == "react-console"]
        assert len(rc_entries) == 1, f"react-console duplicated: {rc_entries}"


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


class TestResolveName:
    """`--name` defaults to `--namespace` for k8s installs (CASE-287 follow-up).

    Avoids the convention gotcha where APP-YAC consumers expect
    `~/.wip-deploy/<namespace>/secrets/api-key` but the deployer wrote
    them to `~/.wip-deploy/default/secrets/`.
    """

    def test_explicit_name_overrides(self) -> None:
        from wip_deploy.cli import _resolve_name
        assert _resolve_name("custom") == "custom"
        assert _resolve_name("custom", target="k8s", namespace="wip-kb") == "custom"

    def test_k8s_with_namespace_uses_namespace(self) -> None:
        from wip_deploy.cli import _resolve_name
        assert _resolve_name(None, target="k8s", namespace="wip-kb") == "wip-kb"

    def test_k8s_without_namespace_falls_through_to_default(self) -> None:
        from wip_deploy.cli import _resolve_name
        assert _resolve_name(None, target="k8s", namespace=None) == "default"
        assert _resolve_name(None, target="k8s", namespace="") == "default"

    def test_compose_target_uses_default(self) -> None:
        from wip_deploy.cli import _resolve_name
        # Even when a namespace value is somehow passed for compose,
        # the convention is that compose installs use 'default'.
        assert _resolve_name(None, target="compose") == "default"
        assert _resolve_name(None, target="compose", namespace="wip-kb") == "default"

    def test_dev_target_uses_default(self) -> None:
        from wip_deploy.cli import _resolve_name
        assert _resolve_name(None, target="dev") == "default"

    def test_no_target_no_namespace_uses_default(self) -> None:
        # Verbs like rebuild/restart/nuke don't have target/namespace.
        from wip_deploy.cli import _resolve_name
        assert _resolve_name(None) == "default"


class TestResolveNamespace:
    """Reciprocal of TestResolveName (CASE-365). `--name X --target k8s`
    without `--namespace` back-derives the namespace to X.

    The pre-existing one-directional rule (--namespace → name) is
    covered by TestResolveName above; this class covers the new
    name → namespace direction.
    """

    def test_name_provided_default_namespace_k8s_back_derives(self) -> None:
        from wip_deploy.cli import _resolve_namespace
        # The case body's exact reproducer scenario.
        assert (
            _resolve_namespace("wip-kb", target="k8s", namespace="wip")
            == "wip-kb"
        )

    def test_name_provided_with_explicit_non_default_namespace_preserves(
        self,
    ) -> None:
        from wip_deploy.cli import _resolve_namespace
        # Intentional asymmetry: --name foo --namespace bar.
        assert (
            _resolve_namespace("foo", target="k8s", namespace="bar") == "bar"
        )

    def test_name_unspecified_returns_namespace_unchanged(self) -> None:
        from wip_deploy.cli import _resolve_namespace
        assert _resolve_namespace(None, target="k8s", namespace="wip") == "wip"
        assert (
            _resolve_namespace(None, target="k8s", namespace="wip-kb")
            == "wip-kb"
        )

    def test_name_equals_wip_returns_namespace_unchanged(self) -> None:
        from wip_deploy.cli import _resolve_namespace
        # --name wip with namespace=wip → no back-derivation (would be a
        # no-op anyway; the guard skips the work).
        assert _resolve_namespace("wip", target="k8s", namespace="wip") == "wip"

    def test_non_k8s_target_returns_namespace_unchanged(self) -> None:
        from wip_deploy.cli import _resolve_namespace
        # The reciprocal only fires for k8s — compose/dev installs have
        # no concept of a k8s namespace.
        assert (
            _resolve_namespace("wip-kb", target="compose", namespace="wip")
            == "wip"
        )
        assert (
            _resolve_namespace("wip-kb", target="dev", namespace="wip")
            == "wip"
        )

    def test_namespace_already_non_default_returns_unchanged(self) -> None:
        from wip_deploy.cli import _resolve_namespace
        # If the operator explicitly set namespace to something other
        # than "wip", treat that as authoritative — don't second-guess.
        assert (
            _resolve_namespace("foo", target="k8s", namespace="bar") == "bar"
        )


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


class TestExamplesAndDiscoverability:
    """The `examples` verb plus footer hint and per-verb Examples blocks
    that make wip-deploy's surface discoverable from `--help` alone."""

    def test_examples_command_runs_and_exits_zero(self) -> None:
        r = _invoke("examples")
        assert r.exit_code == 0
        # Spot-check that the curated workflow categories rendered.
        assert "GETTING STARTED" in r.output
        assert "DEV LOOP" in r.output
        assert "TEARDOWN" in r.output

    def test_examples_command_includes_install_recipes(self) -> None:
        r = _invoke("examples")
        assert r.exit_code == 0
        assert "wip-deploy install --target dev" in r.output
        assert "--app-source react-console=" in r.output
        assert "--tls letsencrypt" in r.output

    def test_examples_command_lists_presets(self) -> None:
        r = _invoke("examples")
        assert r.exit_code == 0
        # All five presets should appear with descriptions.
        for preset in ("core", "headless", "standard", "analytics", "full"):
            assert preset in r.output

    def test_main_help_advertises_examples_verb(self) -> None:
        """Main --help must point lazy users at the examples command."""
        r = _invoke("--help")
        assert r.exit_code == 0
        # Epilog appears at the bottom.
        assert "wip-deploy examples" in r.output

    def test_main_help_lists_examples_command(self) -> None:
        """The examples verb must show up in the Commands list."""
        r = _invoke("--help")
        assert r.exit_code == 0
        assert "examples" in r.output

    def test_install_help_includes_examples_block(self) -> None:
        """The install verb (most flag-heavy) must surface recipes inline."""
        r = _invoke("install", "--help")
        assert r.exit_code == 0
        assert "Examples:" in r.output
        # At least one concrete invocation should be visible. Strip ANSI
        # codes AND normalize whitespace, because Rich highlights "--option"
        # as two separately-styled spans ("-" + "-target") with escape codes
        # in between — the literal substring "--target" never appears
        # contiguously in the raw output. Plus, panel rendering wraps lines
        # differently across environments (Gitea CI vs interactive terms).
        import re
        flat = re.sub(r"\x1b\[[0-9;]*m", "", r.output)
        flat = " ".join(flat.split())
        assert "--target dev" in flat

    def test_validate_help_includes_examples_block(self) -> None:
        r = _invoke("validate", "--help")
        assert r.exit_code == 0
        assert "Examples:" in r.output

    def test_show_spec_help_includes_examples_block(self) -> None:
        r = _invoke("show-spec", "--help")
        assert r.exit_code == 0
        assert "Examples:" in r.output

    def test_render_help_includes_examples_block(self) -> None:
        r = _invoke("render", "--help")
        assert r.exit_code == 0
        assert "Examples:" in r.output

    def test_status_help_includes_examples_block(self) -> None:
        r = _invoke("status", "--help")
        assert r.exit_code == 0
        assert "Examples:" in r.output

    def test_nuke_help_includes_examples_block(self) -> None:
        r = _invoke("nuke", "--help")
        assert r.exit_code == 0
        assert "Examples:" in r.output


# ────────────────────────────────────────────────────────────────────
# CASE-294: deployment.json persistence + status --diff
# ────────────────────────────────────────────────────────────────────


class TestStatusDiff:
    """Spec persistence on install + diff-against-live via the status verb."""

    def _make_k8s_deployment(self):  # type: ignore[no-untyped-def]
        from wip_deploy.spec import (
            AuthSpec,
            Deployment,
            DeploymentMetadata,
            DeploymentSpec,
            ImagesSpec,
            K8sPlatform,
            NetworkSpec,
            PlatformSpec,
            SecretsSpec,
        )

        return Deployment(
            metadata=DeploymentMetadata(name="diff-test"),
            spec=DeploymentSpec(
                target="k8s",
                modules={"optional": ["mcp-server"]},
                auth=AuthSpec(mode="oidc", gateway=True),
                network=NetworkSpec(hostname="wip.local"),
                images=ImagesSpec(registry="ghcr.io/test", tag="v0.1.0"),
                platform=PlatformSpec(k8s=K8sPlatform(namespace="wip-test")),
                secrets=SecretsSpec(backend="file", location="/tmp/s"),
            ),
        )

    def test_persist_and_load_round_trip(self, tmp_path: Path) -> None:
        """Persisted Deployment loads back equal — Pydantic round-trip."""
        from wip_deploy.cli import _load_deployment, _persist_deployment

        d = self._make_k8s_deployment()
        _persist_deployment(d, tmp_path)

        target = tmp_path / "deployment.deployer-state"
        assert target.exists()
        assert target.read_text().endswith("\n")

        # Round-trip
        d2 = _load_deployment(tmp_path)
        assert d2.metadata.name == d.metadata.name
        assert d2.spec.target == d.spec.target
        assert d2.spec.platform.k8s.namespace == d.spec.platform.k8s.namespace
        assert d2.spec.images.registry == d.spec.images.registry
        assert d2.spec.images.tag == d.spec.images.tag

    def test_persist_writes_versioned_envelope(self, tmp_path: Path) -> None:
        """The on-disk format is a versioned envelope so future schema
        evolution can refuse old files cleanly."""
        from wip_deploy.cli import _persist_deployment

        d = self._make_k8s_deployment()
        _persist_deployment(d, tmp_path)

        import json
        payload = json.loads((tmp_path / "deployment.deployer-state").read_text())
        assert payload["wip_deploy_format_version"] == 1
        assert "deployment" in payload
        assert payload["deployment"]["metadata"]["name"] == "diff-test"

    def test_load_deployment_refuses_missing(self, tmp_path: Path) -> None:
        """Missing state file → exit 2 with a message that points the
        user at `wip-deploy install`."""
        r = _invoke("status", "--install-dir", str(tmp_path), "--diff")
        assert r.exit_code == 2
        assert "deployment.deployer-state" in r.output
        assert "wip-deploy install" in r.output

    def test_load_deployment_refuses_wrong_version(self, tmp_path: Path) -> None:
        """Old/unknown schema version → exit 2 with a clear message."""
        import json
        (tmp_path / "deployment.deployer-state").write_text(
            json.dumps({"wip_deploy_format_version": 999, "deployment": {}})
        )
        r = _invoke("status", "--install-dir", str(tmp_path), "--diff")
        assert r.exit_code == 2
        assert "version" in r.output.lower()

    def test_legacy_deployment_json_loads_and_migrates(
        self, tmp_path: Path
    ) -> None:
        """Backwards compat: a deployment.json from the earlier CASE-294
        build still loads via --diff (read-fall-back), and the next
        install rewrites to the new filename + removes the legacy."""
        import json

        from wip_deploy.cli import _load_deployment, _persist_deployment

        # Simulate a stale install: legacy filename, no new filename.
        d = self._make_k8s_deployment()
        legacy = tmp_path / "deployment.json"
        legacy.write_text(
            json.dumps(
                {
                    "wip_deploy_format_version": 1,
                    "deployment": d.model_dump(mode="json"),
                }
            )
        )

        # Read path falls back to legacy.
        d2 = _load_deployment(tmp_path)
        assert d2.metadata.name == d.metadata.name

        # Next persist migrates: new file present, legacy removed.
        _persist_deployment(d, tmp_path)
        assert (tmp_path / "deployment.deployer-state").exists()
        assert not (tmp_path / "deployment.json").exists()

    def test_diff_help_advertises_kubectl(self) -> None:
        """The --diff flag's help text should make the kubectl shell-out
        explicit so operators don't need to read the source."""
        r = _invoke("status", "--help")
        assert r.exit_code == 0
        import re
        flat = re.sub(r"\x1b\[[0-9;]*m", "", r.output)
        flat = " ".join(flat.split())
        assert "--diff" in flat
        assert "kubectl diff" in flat


# ────────────────────────────────────────────────────────────────────
# nuke --purge-all flag wiring (CASE-387)
# ────────────────────────────────────────────────────────────────────


class TestNukePurgeAllWiring:
    """Regression guard for CASE-387: the `--purge-all` branch must forward
    `--remove-secrets` to `_nuke_purge_all`. The bug was a silently-dropped
    flag at the call site — these tests assert the kwarg reaches the helper.
    """

    def _patch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> dict[str, object]:
        captured: dict[str, object] = {}

        def fake_purge_all(**kwargs: object) -> None:
            captured.update(kwargs)

        # has_k8s_install is imported inside the command from wip_deploy.nuke.
        monkeypatch.setattr("wip_deploy.nuke.has_k8s_install", lambda root: False)
        monkeypatch.setattr("wip_deploy.cli._nuke_purge_all", fake_purge_all)
        return captured

    def test_remove_secrets_forwarded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = self._patch(monkeypatch)
        r = _invoke("nuke", "--purge-all", "--remove-secrets", "--yes")
        assert r.exit_code == 0
        assert captured["remove_secrets"] is True

    def test_remove_secrets_defaults_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = self._patch(monkeypatch)
        r = _invoke("nuke", "--purge-all", "--yes")
        assert r.exit_code == 0
        assert captured["remove_secrets"] is False


# ────────────────────────────────────────────────────────────────────
# CASE-364: `status` auto-detects k8s target from saved deployer-state
# ────────────────────────────────────────────────────────────────────


class TestStatusK8sAutoDetect:
    """`status --name <k8s-install>` (no --namespace) must read the saved
    deployer-state, see target=k8s, and query kubectl with the saved
    namespace — instead of falling to the compose path and erroring
    'not a compose install' (the failure CASE-364 reproduced)."""

    def _persist_k8s_state(self, tmp_path: Path) -> None:
        # Reuse the k8s deployment shape from TestStatusDiff (namespace
        # "wip-test", backend file) and persist it as the install dir's state.
        from wip_deploy.cli import _persist_deployment
        _persist_deployment(TestStatusDiff()._make_k8s_deployment(), tmp_path)

    def test_name_only_routes_to_k8s_via_saved_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._persist_k8s_state(tmp_path)

        from wip_deploy import status as status_mod
        called: dict[str, str] = {}

        def fake_k8s(ns: str):  # type: ignore[no-untyped-def]
            called["ns"] = ns
            return []

        def boom_compose(install_dir):  # type: ignore[no-untyped-def]
            raise AssertionError("compose path used for a k8s install")

        monkeypatch.setattr(status_mod, "read_k8s_status", fake_k8s)
        monkeypatch.setattr(status_mod, "read_compose_status", boom_compose)

        # No --namespace — detection must come from the saved state.
        r = _invoke("status", "--install-dir", str(tmp_path))
        assert r.exit_code == 0
        assert called.get("ns") == "wip-test"
        assert "Namespace: wip-test" in r.output

    def test_compose_install_still_uses_compose_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No deployer-state at all → detection returns (None, None) → compose
        # path (the historical default) is used, not k8s.
        from wip_deploy import status as status_mod
        called: dict[str, bool] = {}

        def fake_compose(install_dir):  # type: ignore[no-untyped-def]
            called["compose"] = True
            return []

        def boom_k8s(ns):  # type: ignore[no-untyped-def]
            raise AssertionError("k8s path used without a k8s deployer-state")

        monkeypatch.setattr(status_mod, "read_compose_status", fake_compose)
        monkeypatch.setattr(status_mod, "read_k8s_status", boom_k8s)

        r = _invoke("status", "--install-dir", str(tmp_path))
        assert r.exit_code == 0
        assert called.get("compose") is True
