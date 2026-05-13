"""Regression coverage for CASE-359 — apps-only deployment shape.

Production driver: Peter wants to deploy a barebones WIP in the cloud
and connect from multiple clients (laptop, etc.) running just the apps.
The distributed setup is intentional — Peter explicitly chose it over
deploying apps on the cloud, to exercise the "WIP-as-personal-cloud"
pattern (CASE-357 §"Where the framing weakens design pressure" rejected
the dev-loop framing; this is the real use case).

Coverage:

  1. **Spec layer** — `ModulesSpec.suppress_core` defaults False,
     accepts True. `ActivationSpec.requires_core` predicate evaluates
     correctly against deployment state.

  2. **Activation gates** — `is_component_active`:
     - core: respects `suppress_core` flag
     - requires_core=True infra: active iff core is active
     - existing predicates (`requires_auth_mode`, etc.) compose with
       `requires_core` correctly (AND of all set predicates).

  3. **CLI flag** — `--apps-only` plumbs through build → modules +
     auth defaults. Validation: requires --app. Yellow warning without
     --remote-wip.

  4. **Renderer integration** — apps-only compose has no
     wip-registry, wip-mongodb, wip-router, wip-auth-gateway,
     wip-dex; Caddy stays.

  5. **WIP_BASE_URL substitution** — apps that declare
     `from_component: router` resolve to network.external_base_url
     in apps-only mode (so cross-host apps don't need manifest edits).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.cli import app
from wip_deploy.config_gen import (
    Literal,
    make_spec_context,
    resolve_all_env,
)
from wip_deploy.spec import (
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
from wip_deploy.spec.activation import is_component_active
from wip_deploy.spec.component import (
    ActivationSpec,
    Component,
    ComponentMetadata,
    ComponentSpec,
    ImageRef,
)

runner = CliRunner()


REPO_ROOT = Path(__file__).parent.parent.parent.parent.resolve()


# ────────────────────────────────────────────────────────────────────
# Spec layer — ModulesSpec.suppress_core defaults + validation
# ────────────────────────────────────────────────────────────────────


class TestModulesSpecSuppressCore:
    def test_defaults_to_false(self) -> None:
        m = ModulesSpec()
        assert m.suppress_core is False

    def test_accepts_true(self) -> None:
        m = ModulesSpec(suppress_core=True)
        assert m.suppress_core is True

    def test_round_trips_through_model_dump(self) -> None:
        """Persistence needs to carry suppress_core."""
        m = ModulesSpec(suppress_core=True, optional=["mcp-server"])
        dumped = m.model_dump()
        rehydrated = ModulesSpec.model_validate(dumped)
        assert rehydrated.suppress_core is True
        assert rehydrated.optional == ["mcp-server"]


# ────────────────────────────────────────────────────────────────────
# is_component_active — gate behavior
# ────────────────────────────────────────────────────────────────────


def _full_stack_deployment(*, suppress_core: bool = False) -> Deployment:
    """Build a deployment with various module + auth shapes for predicate
    coverage. compose target keeps things simple."""
    return Deployment(
        metadata=DeploymentMetadata(name="t"),
        spec=DeploymentSpec(
            target="compose",
            modules=ModulesSpec(suppress_core=suppress_core),
            auth=AuthSpec(mode="api-key-only", gateway=False),
            network=NetworkSpec(hostname="localhost", tls="internal"),
            images=ImagesSpec(),
            platform=PlatformSpec(compose=ComposePlatform(data_dir=Path("/tmp/d"))),
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
        ),
    )


def _make_component(
    name: str,
    category: str,
    activation: ActivationSpec | None = None,
) -> Component:
    return Component(
        metadata=ComponentMetadata(
            name=name, category=category, description="test"  # type: ignore[arg-type]
        ),
        spec=ComponentSpec(
            image=ImageRef(name=name, tag="test"),
            ports=[],
            activation=activation,
        ),
    )


class TestActivationGates:
    def test_core_active_by_default(self) -> None:
        c = _make_component("registry", "core")
        d = _full_stack_deployment(suppress_core=False)
        assert is_component_active(c, d) is True

    def test_core_suppressed_when_flag_set(self) -> None:
        c = _make_component("registry", "core")
        d = _full_stack_deployment(suppress_core=True)
        assert is_component_active(c, d) is False

    def test_infra_requires_core_true_active_with_core(self) -> None:
        """mongodb has requires_core=true — active when core is active."""
        c = _make_component(
            "mongodb",
            "infrastructure",
            activation=ActivationSpec(requires_core=True),
        )
        d = _full_stack_deployment(suppress_core=False)
        assert is_component_active(c, d) is True

    def test_infra_requires_core_true_inactive_without_core(self) -> None:
        """In apps-only (suppress_core=True), mongodb auto-deactivates."""
        c = _make_component(
            "mongodb",
            "infrastructure",
            activation=ActivationSpec(requires_core=True),
        )
        d = _full_stack_deployment(suppress_core=True)
        assert is_component_active(c, d) is False

    def test_infra_no_requires_core_predicate_unaffected(self) -> None:
        """Other infrastructure (no requires_core set) is unaffected by
        suppress_core. E.g., postgres only fires when reporting-sync is
        listed in modules.optional — its predicate is requires_any_module."""
        c = _make_component(
            "postgres",
            "infrastructure",
            activation=ActivationSpec(requires_any_module=["reporting-sync"]),
        )
        d_apps_only = _full_stack_deployment(suppress_core=True)
        # postgres without reporting-sync requested → inactive (predicate
        # fails on requires_any_module), regardless of suppress_core.
        assert is_component_active(c, d_apps_only) is False

        # Same predicate, reporting-sync requested → active even in apps-
        # only (this is a contrived combination but verifies independence).
        d2 = _full_stack_deployment(suppress_core=True)
        d2.spec.modules.optional = ["reporting-sync"]
        assert is_component_active(c, d2) is True

    def test_real_mongodb_manifest_has_requires_core(self) -> None:
        """Sanity-check: mongodb's actual manifest declares
        requires_core=true (added as part of this CASE-359 work)."""
        from wip_deploy.discovery import discover

        d = discover(REPO_ROOT)
        mongodb = next(c for c in d.components if c.metadata.name == "mongodb")
        assert mongodb.spec.activation is not None
        assert mongodb.spec.activation.requires_core is True

    def test_real_router_manifest_has_requires_core(self) -> None:
        """Same for wip-router — its purpose is to proxy to core."""
        from wip_deploy.discovery import discover

        d = discover(REPO_ROOT)
        router = next(c for c in d.components if c.metadata.name == "router")
        assert router.spec.activation is not None
        assert router.spec.activation.requires_core is True


# ────────────────────────────────────────────────────────────────────
# BuildInputs — apps_only plumbing into the spec
# ────────────────────────────────────────────────────────────────────


class TestBuildInputsAppsOnly:
    def test_apps_only_flips_suppress_core(self) -> None:
        inputs = BuildInputs(
            name="t",
            preset="standard",
            target="dev",
            hostname="localhost",
            apps=["react-console"],
            apps_only=True,
            dev_mode="simple",
        )
        d = build_deployment(inputs)
        assert d.spec.modules.suppress_core is True

    def test_apps_only_disables_gateway_and_dex(self) -> None:
        """apps-only implies auth.gateway=False + auth.mode=api-key-only
        (no Dex makes sense without core services to protect)."""
        inputs = BuildInputs(
            name="t",
            preset="standard",
            target="dev",
            hostname="localhost",
            apps=["react-console"],
            apps_only=True,
            dev_mode="simple",
        )
        d = build_deployment(inputs)
        assert d.spec.auth.mode == "api-key-only"
        assert d.spec.auth.gateway is False
        assert d.spec.auth.users == []

    def test_default_is_full_stack(self) -> None:
        inputs = BuildInputs(
            name="t",
            preset="standard",
            target="dev",
            hostname="localhost",
            dev_mode="simple",
        )
        d = build_deployment(inputs)
        assert d.spec.modules.suppress_core is False


# ────────────────────────────────────────────────────────────────────
# CLI integration
# ────────────────────────────────────────────────────────────────────


class TestAppsOnlyCLI:
    @pytest.fixture(autouse=True)
    def isolated_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> Path:
        """Sandbox `Path.home()` so the CLI never touches the real
        ~/.wip-deploy/. Prevents real `~/.wip-deploy/apps/react-console.yaml`
        from triggering the CASE-356 shadow warning (which pollutes
        stdout — see CASE-366)."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        return fake_home

    def test_flag_lands_on_rendered_spec(self) -> None:
        import json

        r = runner.invoke(
            app,
            [
                "show-spec",
                "--preset", "standard",
                "--target", "dev",
                "--hostname", "localhost",
                "--app", "react-console",
                "--app-source", f"react-console={REPO_ROOT}",
                "--apps-only",
                "--remote-wip", "https://cloud-wip.example.com",
                "--format", "json",
                "--repo-root", str(REPO_ROOT),
            ],
        )
        assert r.exit_code == 0, r.output
        parsed = json.loads(r.stdout)
        assert parsed["spec"]["modules"]["suppress_core"] is True
        assert parsed["spec"]["auth"]["mode"] == "api-key-only"
        assert parsed["spec"]["auth"]["gateway"] is False

    def test_apps_only_requires_app(self) -> None:
        """An apps-only install with no --app is structurally useless —
        refuse before render with a clear actionable message."""
        r = runner.invoke(
            app,
            [
                "show-spec",
                "--preset", "standard",
                "--target", "dev",
                "--hostname", "localhost",
                "--apps-only",
                "--format", "yaml",
                "--repo-root", str(REPO_ROOT),
            ],
        )
        assert r.exit_code == 2, r.output
        assert "requires at least one --app" in r.output

    def test_apps_only_without_remote_wip_warns(self) -> None:
        """Yellow warning — apps will resolve external_base_url to
        localhost which probably isn't useful."""
        r = runner.invoke(
            app,
            [
                "show-spec",
                "--preset", "standard",
                "--target", "dev",
                "--hostname", "localhost",
                "--app", "react-console",
                "--app-source", f"react-console={REPO_ROOT}",
                "--apps-only",
                "--format", "yaml",
                "--repo-root", str(REPO_ROOT),
            ],
        )
        assert r.exit_code == 0, r.output
        assert "without --remote-wip" in r.stderr
        # And the case-358 warning that fires for remote-wip-without-
        # apps-only should NOT fire — apps_only IS set.
        assert "still deploys its full backend stack" not in r.stderr

    def test_remote_wip_without_apps_only_still_warns(self) -> None:
        """The CASE-358 warning fires when --remote-wip is set without
        --apps-only — keeping existing behavior intact."""
        r = runner.invoke(
            app,
            [
                "show-spec",
                "--preset", "standard",
                "--target", "dev",
                "--hostname", "localhost",
                "--remote-wip", "https://cloud.example.com",
                "--format", "yaml",
                "--repo-root", str(REPO_ROOT),
            ],
        )
        assert r.exit_code == 0, r.output
        assert "still deploys its full backend stack" in r.stderr
        # And the apps-only warning should NOT fire — apps_only is NOT set.
        assert "without --remote-wip" not in r.stderr


# ────────────────────────────────────────────────────────────────────
# WIP_BASE_URL substitution
# ────────────────────────────────────────────────────────────────────


class TestWipBaseUrlSubstitution:
    def test_from_component_router_resolves_to_external_base_url(self) -> None:
        """react-console (and other apps) declare WIP_BASE_URL via
        `from_component: router`. In apps-only mode, router is
        suppressed — but env resolution returns network.external_base_url
        instead of failing. So cross-host apps work without manifest edits."""
        from wip_deploy.discovery import discover

        discovery = discover(REPO_ROOT)
        inputs = BuildInputs(
            name="t",
            preset="standard",
            target="dev",
            hostname="localhost",
            apps=["react-console"],
            app_sources={"react-console": REPO_ROOT},
            apps_only=True,
            remote_wip_url="https://cloud-wip.example.com",
            dev_mode="simple",
        )
        d = build_deployment(inputs)
        ctx = make_spec_context(d, discovery.components)
        resolved = resolve_all_env(d, discovery.components, discovery.apps, ctx)

        wip_base = resolved["react-console"].required["WIP_BASE_URL"]
        assert isinstance(wip_base, Literal)
        assert wip_base.value == "https://cloud-wip.example.com"

    def test_normal_install_still_uses_internal_router(self) -> None:
        """Same-host install (no apps-only) → WIP_BASE_URL is the
        internal router URL. Unchanged behavior."""
        from wip_deploy.discovery import discover

        discovery = discover(REPO_ROOT)
        inputs = BuildInputs(
            name="t",
            preset="standard",
            target="dev",
            hostname="localhost",
            apps=["react-console"],
            app_sources={"react-console": REPO_ROOT},
            apps_only=False,
            dev_mode="simple",
        )
        d = build_deployment(inputs)
        ctx = make_spec_context(d, discovery.components)
        resolved = resolve_all_env(d, discovery.components, discovery.apps, ctx)

        wip_base = resolved["react-console"].required["WIP_BASE_URL"]
        assert isinstance(wip_base, Literal)
        assert wip_base.value == "http://wip-router:8080"


# ────────────────────────────────────────────────────────────────────
# Renderer shape — apps-only compose has NO core/infra-of-core services
# ────────────────────────────────────────────────────────────────────


class TestAppsOnlyRendererShape:
    @pytest.fixture
    def secrets(self, tmp_path: Path) -> object:
        """Minimal secrets for an apps-only render — no Dex / no postgres,
        just the api-key + minio passthroughs that the app might still touch."""
        from wip_deploy.discovery import discover
        from wip_deploy.secrets import ensure_secrets
        from wip_deploy.secrets_backend import FileSecretBackend

        discovery = discover(REPO_ROOT)
        inputs = BuildInputs(
            name="t",
            preset="standard",
            target="dev",
            hostname="localhost",
            apps=["react-console"],
            app_sources={"react-console": REPO_ROOT},
            apps_only=True,
            remote_wip_url="https://cloud-wip.example.com",
            dev_mode="simple",
        )
        d = build_deployment(inputs)
        return d, discovery, ensure_secrets(
            d,
            discovery.components,
            discovery.apps,
            FileSecretBackend(tmp_path / "secrets"),
        )

    def test_dev_compose_excludes_core_and_supporting_infra(
        self, secrets: object
    ) -> None:
        """Apps-only dev install renders ONLY the apps + Caddy + dependencies
        that aren't core-coupled. No wip-registry, no wip-mongodb, no
        wip-router, no wip-dex, no wip-auth-gateway."""
        import yaml

        from wip_deploy.renderers.dev_simple import render_dev_simple

        deployment, discovery, secret_bag = secrets  # type: ignore[misc]
        tree = render_dev_simple(
            deployment,
            discovery.components,
            discovery.apps,
            secret_bag,
            repo_root=REPO_ROOT,
        )
        compose_yaml = tree.files[Path("docker-compose.yaml")].content
        data = yaml.safe_load(compose_yaml)
        services = data["services"]

        # Apps + Caddy stay.
        assert "react-console" in services
        assert "caddy" in services

        # Everything core / requires_core / Dex-coupled goes away.
        for excluded in (
            "registry", "def-store", "template-store", "document-store",
            "mongodb", "router", "auth-gateway", "dex",
        ):
            assert excluded not in services, (
                f"apps-only render should not include {excluded!r} — "
                f"got services: {sorted(services.keys())}"
            )
