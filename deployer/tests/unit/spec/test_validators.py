"""Tests for cross-cutting validators (validate_all).

These validators run after discovery and span Deployment + Components + Apps.
"""

from __future__ import annotations

from wip_deploy.spec import (
    AppRef,
    Deployment,
    EnvSource,
    EnvSpec,
    EnvVar,
    OidcClientSpec,
    Port,
)
from wip_deploy.spec.app import App, AppMetadata
from wip_deploy.spec.component import ComponentMetadata, ComponentSpec, ImageRef
from wip_deploy.spec.validators import validate_all

from .conftest import make_component


def _make_app(
    name: str,
    *,
    with_oidc: bool = False,
    depends_on: list[str] | None = None,
) -> App:
    spec_kwargs: dict[str, object] = {
        "image": ImageRef(name=f"wip-{name}"),
        "ports": [Port(name="http", container_port=80)],
    }
    if depends_on:
        spec_kwargs["depends_on"] = depends_on
    if with_oidc:
        spec_kwargs["oidc_client"] = OidcClientSpec(client_id=name)
    return App(
        metadata=ComponentMetadata(
            name=name,
            category="optional",
            description=f"Test app {name}",
        ),
        spec=ComponentSpec(**spec_kwargs),  # type: ignore[arg-type]
        app_metadata=AppMetadata(
            display_name=name.title(),
            route_prefix=f"/apps/{name}",
        ),
    )


# ────────────────────────────────────────────────────────────────────
# modules.optional resolution
# ────────────────────────────────────────────────────────────────────


class TestModulesExist:
    def test_known_optional_module_ok(self, minimal_compose_deployment: Deployment) -> None:
        d = minimal_compose_deployment.model_copy(deep=True)
        d.spec.modules.optional = ["reporting-sync"]
        report = validate_all(
            d,
            [make_component("reporting-sync", category="optional")],
            [],
        )
        assert report.ok

    def test_unknown_module_reported(self, minimal_compose_deployment: Deployment) -> None:
        d = minimal_compose_deployment.model_copy(deep=True)
        d.spec.modules.optional = ["nonexistent"]
        report = validate_all(d, [], [])
        assert not report.ok
        assert any("nonexistent" in str(e) for e in report.errors)

    def test_core_component_not_in_optional_list(
        self, minimal_compose_deployment: Deployment
    ) -> None:
        """A core component in modules.optional should be rejected (it's
        implicit, shouldn't be listed)."""
        d = minimal_compose_deployment.model_copy(deep=True)
        d.spec.modules.optional = ["registry"]
        report = validate_all(
            d,
            [make_component("registry", category="core")],
            [],
        )
        assert not report.ok
        assert any("registry" in str(e) for e in report.errors)


# ────────────────────────────────────────────────────────────────────
# apps resolution
# ────────────────────────────────────────────────────────────────────


class TestAppsExist:
    def test_known_app_ok(self, minimal_compose_deployment: Deployment) -> None:
        d = minimal_compose_deployment.model_copy(deep=True)
        d.spec.apps = [AppRef(name="dnd")]
        report = validate_all(d, [], [_make_app("dnd")])
        assert report.ok

    def test_unknown_app_reported(
        self, minimal_compose_deployment: Deployment
    ) -> None:
        d = minimal_compose_deployment.model_copy(deep=True)
        d.spec.apps = [AppRef(name="nonexistent")]
        report = validate_all(d, [], [])
        assert not report.ok


# ────────────────────────────────────────────────────────────────────
# OIDC client vs auth.mode
# ────────────────────────────────────────────────────────────────────


class TestOidcClientRequiresOidc:
    def _api_key_only_deployment(self) -> Deployment:
        from wip_deploy.spec import (
            AuthSpec,
            ComposePlatform,
            DeploymentMetadata,
            DeploymentSpec,
            NetworkSpec,
            PlatformSpec,
            SecretsSpec,
        )

        return Deployment(
            metadata=DeploymentMetadata(name="t"),
            spec=DeploymentSpec(
                target="compose",
                auth=AuthSpec(mode="api-key-only", gateway=False, users=[]),
                network=NetworkSpec(hostname="wip.local"),
                platform=PlatformSpec(compose=ComposePlatform(data_dir="/tmp/d")),
                secrets=SecretsSpec(backend="file", location="/tmp/s"),
            ),
        )

    def test_component_with_oidc_under_api_key_rejected(self) -> None:
        d = self._api_key_only_deployment()
        comp = make_component(
            "console",
            category="optional",
            image=ImageRef(name="wip-console"),
            ports=[Port(name="http", container_port=80)],
            oidc_client=OidcClientSpec(client_id="wip-console"),
        )
        d.spec.modules.optional = ["console"]
        report = validate_all(d, [comp], [])
        assert not report.ok
        assert any("oidc_client" in str(e) for e in report.errors)

    def test_app_with_oidc_under_api_key_rejected(self) -> None:
        d = self._api_key_only_deployment()
        d.spec.apps = [AppRef(name="dnd")]
        report = validate_all(d, [], [_make_app("dnd", with_oidc=True)])
        assert not report.ok

    def test_component_with_oidc_under_oidc_ok(
        self, minimal_compose_deployment: Deployment
    ) -> None:
        d = minimal_compose_deployment.model_copy(deep=True)
        d.spec.modules.optional = ["console"]
        comp = make_component(
            "console",
            category="optional",
            image=ImageRef(name="wip-console"),
            ports=[Port(name="http", container_port=80)],
            oidc_client=OidcClientSpec(client_id="wip-console"),
        )
        report = validate_all(d, [comp], [])
        assert report.ok

    def test_disabled_app_with_oidc_under_api_key_ok(self) -> None:
        """Disabled apps should not trigger OIDC validation."""
        d = self._api_key_only_deployment()
        d.spec.apps = [AppRef(name="dnd", enabled=False)]
        report = validate_all(d, [], [_make_app("dnd", with_oidc=True)])
        assert report.ok


# ────────────────────────────────────────────────────────────────────
# depends_on resolution
# ────────────────────────────────────────────────────────────────────


class TestDependsOn:
    def test_known_dep_ok(self, minimal_compose_deployment: Deployment) -> None:
        a = make_component("a", category="core")
        b = make_component("b", category="core", depends_on=["a"])
        report = validate_all(minimal_compose_deployment, [a, b], [])
        assert report.ok

    def test_unknown_dep_reported(
        self, minimal_compose_deployment: Deployment
    ) -> None:
        b = make_component("b", category="core", depends_on=["ghost"])
        report = validate_all(minimal_compose_deployment, [b], [])
        assert not report.ok
        assert any("ghost" in str(e) for e in report.errors)

    def test_app_depends_on_component(
        self, minimal_compose_deployment: Deployment
    ) -> None:
        a = make_component("doc-store", category="core")
        app = _make_app("dnd", depends_on=["doc-store"])
        d = minimal_compose_deployment.model_copy(deep=True)
        d.spec.apps = [AppRef(name="dnd")]
        report = validate_all(d, [a], [app])
        assert report.ok


# ────────────────────────────────────────────────────────────────────
# env from_component resolution
# ────────────────────────────────────────────────────────────────────


class TestEnvFromComponent:
    def test_resolvable_component_ok(
        self, minimal_compose_deployment: Deployment
    ) -> None:
        mongo = make_component("mongodb", category="infrastructure")
        doc_store = make_component(
            "document-store",
            category="core",
            env=EnvSpec(
                required=[
                    EnvVar(
                        name="MONGO_URI",
                        source=EnvSource(from_component="mongodb"),
                    )
                ]
            ),
        )
        report = validate_all(minimal_compose_deployment, [mongo, doc_store], [])
        assert report.ok

    def test_unresolvable_component_reported(
        self, minimal_compose_deployment: Deployment
    ) -> None:
        doc_store = make_component(
            "document-store",
            category="core",
            env=EnvSpec(
                required=[
                    EnvVar(
                        name="MONGO_URI",
                        source=EnvSource(from_component="mongodb-ghost"),
                    )
                ]
            ),
        )
        report = validate_all(minimal_compose_deployment, [doc_store], [])
        assert not report.ok
        assert any("mongodb-ghost" in str(e) for e in report.errors)


# ────────────────────────────────────────────────────────────────────
# OIDC client uniqueness
# ────────────────────────────────────────────────────────────────────


class TestOidcClientUniqueness:
    def test_unique_client_ids_ok(
        self, minimal_compose_deployment: Deployment
    ) -> None:
        d = minimal_compose_deployment.model_copy(deep=True)
        d.spec.modules.optional = ["console"]
        d.spec.apps = [AppRef(name="dnd")]
        console = make_component(
            "console",
            category="optional",
            image=ImageRef(name="wip-console"),
            ports=[Port(name="http", container_port=80)],
            oidc_client=OidcClientSpec(client_id="wip-console"),
        )
        app = _make_app("dnd", with_oidc=True)  # client_id=dnd
        report = validate_all(d, [console], [app])
        assert report.ok

    def test_duplicate_client_ids_reported(
        self, minimal_compose_deployment: Deployment
    ) -> None:
        d = minimal_compose_deployment.model_copy(deep=True)
        d.spec.modules.optional = ["console"]
        d.spec.apps = [AppRef(name="dnd")]
        console = make_component(
            "console",
            category="optional",
            image=ImageRef(name="wip-console"),
            ports=[Port(name="http", container_port=80)],
            oidc_client=OidcClientSpec(client_id="dnd"),  # collides with app
        )
        app = _make_app("dnd", with_oidc=True)
        report = validate_all(d, [console], [app])
        assert not report.ok
        assert any("duplicate OIDC client_id" in str(e) for e in report.errors)

    def test_disabled_app_does_not_collide(
        self, minimal_compose_deployment: Deployment
    ) -> None:
        """Disabled apps must not participate in uniqueness checks — you
        should be able to define an app whose client_id overlaps with an
        active component if the app is disabled."""
        d = minimal_compose_deployment.model_copy(deep=True)
        d.spec.modules.optional = ["console"]
        d.spec.apps = [AppRef(name="dnd", enabled=False)]
        console = make_component(
            "console",
            category="optional",
            image=ImageRef(name="wip-console"),
            ports=[Port(name="http", container_port=80)],
            oidc_client=OidcClientSpec(client_id="dnd"),
        )
        app = _make_app("dnd", with_oidc=True)
        report = validate_all(d, [console], [app])
        assert report.ok


# ────────────────────────────────────────────────────────────────────
# Aggregate behavior
# ────────────────────────────────────────────────────────────────────


class TestValidationReport:
    def test_collects_multiple_errors(
        self, minimal_compose_deployment: Deployment
    ) -> None:
        d = minimal_compose_deployment.model_copy(deep=True)
        d.spec.modules.optional = ["ghost1"]
        d.spec.apps = [AppRef(name="ghost2")]
        report = validate_all(d, [], [])
        assert len(report.errors) >= 2


class TestImagesResolvable:
    """Compose/k8s targets need a registry for short-name images. Dev
    target builds from source so the guard doesn't apply.

    Without this validator, users who forget `--registry` get a
    confusing `manifest unknown` from podman-compose at apply time
    instead of a clear error up front.
    """

    def test_compose_without_registry_fails(
        self, minimal_compose_deployment: Deployment
    ) -> None:
        d = minimal_compose_deployment.model_copy(deep=True)
        d.spec.images.registry = None
        short = make_component(
            "short-svc",
            category="optional",
            image=ImageRef(name="short-svc"),  # no "/"
            ports=[Port(name="http", container_port=8000)],
        )
        d.spec.modules.optional = ["short-svc"]
        report = validate_all(d, [short], [])
        assert not report.ok
        err_str = "\n".join(str(e) for e in report.errors)
        assert "needs pre-built images" in err_str
        assert "short-svc" in err_str
        assert "--registry" in err_str
        assert "--target dev" in err_str

    def test_compose_with_registry_passes(
        self, minimal_compose_deployment: Deployment
    ) -> None:
        d = minimal_compose_deployment.model_copy(deep=True)
        # Fixture already sets images.registry = "ghcr.io/test"
        assert d.spec.images.registry is not None
        short = make_component(
            "short-svc",
            category="optional",
            image=ImageRef(name="short-svc"),
            ports=[Port(name="http", container_port=8000)],
        )
        d.spec.modules.optional = ["short-svc"]
        report = validate_all(d, [short], [])
        assert report.ok, [str(e) for e in report.errors]

    def test_k8s_without_registry_fails(
        self, minimal_k8s_deployment: Deployment
    ) -> None:
        d = minimal_k8s_deployment.model_copy(deep=True)
        d.spec.images.registry = None
        short = make_component(
            "short-svc",
            category="optional",
            image=ImageRef(name="short-svc"),
            ports=[Port(name="http", container_port=8000)],
        )
        d.spec.modules.optional = ["short-svc"]
        report = validate_all(d, [short], [])
        assert not report.ok
        assert any("needs pre-built images" in str(e) for e in report.errors)

    def test_fully_qualified_image_does_not_need_registry(
        self, minimal_compose_deployment: Deployment
    ) -> None:
        """MongoDB, Postgres etc. have image.name like
        'docker.io/library/mongo' — they never hit the registry-prefix
        path."""
        d = minimal_compose_deployment.model_copy(deep=True)
        d.spec.images.registry = None
        mongo_like = make_component(
            "mongo-like",
            category="optional",
            image=ImageRef(name="docker.io/library/mongo", tag="7"),
            ports=[Port(name="http", container_port=27017)],
        )
        d.spec.modules.optional = ["mongo-like"]
        report = validate_all(d, [mongo_like], [])
        assert report.ok, [str(e) for e in report.errors]

    def test_inactive_short_name_component_not_flagged(
        self, minimal_compose_deployment: Deployment
    ) -> None:
        """Only active components need resolvable images. An optional
        component that isn't enabled shouldn't block the deployment."""
        d = minimal_compose_deployment.model_copy(deep=True)
        d.spec.images.registry = None
        d.spec.modules.optional = []  # nothing active
        short = make_component(
            "unused-svc",
            category="optional",
            image=ImageRef(name="unused-svc"),
            ports=[Port(name="http", container_port=8000)],
        )
        report = validate_all(d, [short], [])
        assert report.ok, [str(e) for e in report.errors]
