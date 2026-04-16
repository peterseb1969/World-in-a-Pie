"""Tests for is_component_active — the single source of truth for
whether a component participates in a given deployment."""

from __future__ import annotations

from wip_deploy.spec import ActivationSpec, ImageRef
from wip_deploy.spec.activation import is_component_active
from wip_deploy.spec.component import Component, ComponentMetadata, ComponentSpec


def _comp(
    name: str,
    *,
    category: str = "infrastructure",
    activation: ActivationSpec | None = None,
) -> Component:
    return Component(
        metadata=ComponentMetadata(
            name=name,
            category=category,  # type: ignore[arg-type]
            description=f"test {name}",
        ),
        spec=ComponentSpec(
            image=ImageRef(name=f"wip-{name}"),
            activation=activation,
        ),
    )


class TestCoreAlwaysActive:
    def test_core_component_always_active(self, minimal_compose_deployment) -> None:  # type: ignore[no-untyped-def]
        c = _comp("registry", category="core")
        assert is_component_active(c, minimal_compose_deployment) is True


class TestOptional:
    def test_optional_listed_is_active(self, minimal_compose_deployment) -> None:  # type: ignore[no-untyped-def]
        d = minimal_compose_deployment.model_copy(deep=True)
        d.spec.modules.optional = ["reporting"]
        c = _comp("reporting", category="optional")
        assert is_component_active(c, d) is True

    def test_optional_unlisted_is_inactive(
        self, minimal_compose_deployment
    ) -> None:  # type: ignore[no-untyped-def]
        c = _comp("reporting", category="optional")
        assert is_component_active(c, minimal_compose_deployment) is False


class TestInfrastructureUnconditional:
    def test_infra_without_activation_is_always_active(
        self, minimal_compose_deployment
    ) -> None:  # type: ignore[no-untyped-def]
        c = _comp("mongodb", category="infrastructure")
        assert is_component_active(c, minimal_compose_deployment) is True


class TestInfrastructureRequiresAnyModule:
    def test_single_module_match_activates(
        self, minimal_compose_deployment
    ) -> None:  # type: ignore[no-untyped-def]
        d = minimal_compose_deployment.model_copy(deep=True)
        d.spec.modules.optional = ["reporting"]
        c = _comp(
            "postgres",
            activation=ActivationSpec(requires_any_module=["reporting"]),
        )
        assert is_component_active(c, d) is True

    def test_single_module_no_match_deactivates(
        self, minimal_compose_deployment
    ) -> None:  # type: ignore[no-untyped-def]
        c = _comp(
            "postgres",
            activation=ActivationSpec(requires_any_module=["reporting"]),
        )
        assert is_component_active(c, minimal_compose_deployment) is False

    def test_any_of_multiple_modules_activates(
        self, minimal_compose_deployment
    ) -> None:  # type: ignore[no-untyped-def]
        d = minimal_compose_deployment.model_copy(deep=True)
        d.spec.modules.optional = ["ingest"]
        c = _comp(
            "nats",
            activation=ActivationSpec(requires_any_module=["reporting", "ingest"]),
        )
        assert is_component_active(c, d) is True

    def test_neither_of_multiple_modules_deactivates(
        self, minimal_compose_deployment
    ) -> None:  # type: ignore[no-untyped-def]
        c = _comp(
            "nats",
            activation=ActivationSpec(requires_any_module=["reporting", "ingest"]),
        )
        assert is_component_active(c, minimal_compose_deployment) is False


class TestInfrastructureRequiresAuthMode:
    def test_matching_mode_activates(
        self, minimal_compose_deployment
    ) -> None:  # type: ignore[no-untyped-def]
        # minimal_compose_deployment has auth.mode="oidc"
        c = _comp(
            "dex",
            activation=ActivationSpec(requires_auth_mode=["oidc", "hybrid"]),
        )
        assert is_component_active(c, minimal_compose_deployment) is True

    def test_non_matching_mode_deactivates(
        self, minimal_compose_deployment
    ) -> None:  # type: ignore[no-untyped-def]
        d = minimal_compose_deployment.model_copy(deep=True)
        d.spec.auth.gateway = False
        d.spec.auth.mode = "api-key-only"
        c = _comp(
            "dex",
            activation=ActivationSpec(requires_auth_mode=["oidc", "hybrid"]),
        )
        assert is_component_active(c, d) is False


class TestInfrastructureRequiresAuthGateway:
    def test_gateway_true_when_required_true(
        self, minimal_compose_deployment
    ) -> None:  # type: ignore[no-untyped-def]
        # minimal_compose_deployment has gateway=True
        c = _comp(
            "wip-auth-gateway",
            activation=ActivationSpec(requires_auth_gateway=True),
        )
        assert is_component_active(c, minimal_compose_deployment) is True

    def test_gateway_false_when_required_true(
        self, minimal_compose_deployment
    ) -> None:  # type: ignore[no-untyped-def]
        d = minimal_compose_deployment.model_copy(deep=True)
        d.spec.auth.gateway = False
        c = _comp(
            "wip-auth-gateway",
            activation=ActivationSpec(requires_auth_gateway=True),
        )
        assert is_component_active(c, d) is False


class TestInfrastructureCombinedPredicates:
    def test_all_predicates_must_hold(
        self, minimal_compose_deployment
    ) -> None:  # type: ignore[no-untyped-def]
        """Combined predicates are AND'd."""
        d = minimal_compose_deployment.model_copy(deep=True)
        d.spec.modules.optional = ["reporting"]
        # dex hypothetical: oidc AND gateway=true AND reporting active
        c = _comp(
            "hypothetical",
            activation=ActivationSpec(
                requires_any_module=["reporting"],
                requires_auth_mode=["oidc"],
                requires_auth_gateway=True,
            ),
        )
        assert is_component_active(c, d) is True

    def test_one_predicate_failing_deactivates(
        self, minimal_compose_deployment
    ) -> None:  # type: ignore[no-untyped-def]
        d = minimal_compose_deployment.model_copy(deep=True)
        # reporting NOT in optional — this should fail even though auth is oidc + gateway
        c = _comp(
            "hypothetical",
            activation=ActivationSpec(
                requires_any_module=["reporting"],
                requires_auth_mode=["oidc"],
                requires_auth_gateway=True,
            ),
        )
        assert is_component_active(c, d) is False
