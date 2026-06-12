"""Regression coverage for CASE-455 — post-mutation ApplyError persistence.

A health-timeout used to exit `wip-deploy install` before
`_persist_deployment` ran. Correct when the apply failed before touching
the world; wrong after `compose up` recreated every container — the
persisted state then described the OLD deployment while reality ran the
NEW one (observed: state said target=compose while the stack ran dev, so
`add-app --app-source` refused and a flagless re-install would have
silently reverted the dev conversion).

The contract under test: ApplyError carries `mutated`, stamped True for
any failure raised at-or-after the materializing step (`_run_up` /
`_kubectl_apply_tree`) and False for pre-mutation failures. The CLI
persists the applied spec when `mutated` is True (cli.py install +
additive-verbs except-branches; exercised here at the apply layer where
the stamping lives).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wip_deploy.apply import ApplyError, apply_compose
from wip_deploy.spec.deployment import (
    ApplySpec,
    AuthSpec,
    ComposePlatform,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    ImagesSpec,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)


def _deployment(*, wait: bool = True, on_timeout: str = "fail") -> Deployment:
    return Deployment(
        metadata=DeploymentMetadata(name="t"),
        spec=DeploymentSpec(
            target="compose",
            auth=AuthSpec(mode="api-key-only", gateway=False),
            network=NetworkSpec(hostname="wip.local", tls="internal"),
            images=ImagesSpec(),
            platform=PlatformSpec(compose=ComposePlatform(data_dir=Path("/tmp/d"))),
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
            apply=ApplySpec(wait=wait, on_timeout=on_timeout),
        ),
    )


class TestApplyErrorMutatedFlag:
    def test_defaults_to_false(self) -> None:
        assert ApplyError("x").mutated is False

    def test_explicit_true(self) -> None:
        assert ApplyError("x", mutated=True).mutated is True


class TestApplyComposeMutationStamping:
    @patch("wip_deploy.apply._run_post_install")
    @patch("wip_deploy.apply._wait_healthy", return_value=False)
    @patch("wip_deploy.apply._count_services", return_value=3)
    @patch("wip_deploy.apply._run_up")
    @patch("wip_deploy.apply._remove_stale_wip_containers")
    @patch("wip_deploy.apply._detect_compose_cmd", return_value=["podman-compose"])
    def test_health_timeout_is_post_mutation(
        self, *_mocks: MagicMock, tmp_path: Path = Path("/tmp")
    ) -> None:
        """compose up succeeded, health wait timed out → mutated=True."""
        tree = MagicMock()
        with pytest.raises(ApplyError) as exc_info:
            apply_compose(
                deployment=_deployment(),
                components=[],
                apps=[],
                tree=tree,
                install_dir=Path("/tmp/case455"),
            )
        assert "timed out" in str(exc_info.value)
        assert exc_info.value.mutated is True

    @patch(
        "wip_deploy.apply._run_up",
        side_effect=ApplyError("compose up failed (exit 1)"),
    )
    @patch("wip_deploy.apply._remove_stale_wip_containers")
    @patch("wip_deploy.apply._detect_compose_cmd", return_value=["podman-compose"])
    def test_mid_up_failure_is_post_mutation(self, *_mocks: MagicMock) -> None:
        """A failure DURING compose up may have partially recreated
        containers — the old state is no longer trustworthy either."""
        with pytest.raises(ApplyError) as exc_info:
            apply_compose(
                deployment=_deployment(),
                components=[],
                apps=[],
                tree=MagicMock(),
                install_dir=Path("/tmp/case455"),
            )
        assert exc_info.value.mutated is True

    @patch(
        "wip_deploy.apply._detect_compose_cmd",
        side_effect=ApplyError("neither podman-compose nor docker is available on PATH"),
    )
    def test_pre_mutation_failure_keeps_flag_false(self, *_mocks: MagicMock) -> None:
        """No compose binary → nothing ran → old state is still the
        truthful last-known-good."""
        with pytest.raises(ApplyError) as exc_info:
            apply_compose(
                deployment=_deployment(),
                components=[],
                apps=[],
                tree=MagicMock(),
                install_dir=Path("/tmp/case455"),
            )
        assert exc_info.value.mutated is False

    @patch("wip_deploy.apply._run_post_install")
    @patch("wip_deploy.apply._wait_healthy", return_value=False)
    @patch("wip_deploy.apply._count_services", return_value=3)
    @patch("wip_deploy.apply._run_up")
    @patch("wip_deploy.apply._remove_stale_wip_containers")
    @patch("wip_deploy.apply._detect_compose_cmd", return_value=["podman-compose"])
    def test_on_timeout_warn_still_returns_result(
        self, *_mocks: MagicMock
    ) -> None:
        """warn mode: no raise, result.healthy=False — the CLI's normal
        post-apply persist runs (unchanged behaviour)."""
        result = apply_compose(
            deployment=_deployment(on_timeout="warn"),
            components=[],
            apps=[],
            tree=MagicMock(),
            install_dir=Path("/tmp/case455"),
        )
        assert result.healthy is False
