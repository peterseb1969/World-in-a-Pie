"""Regression coverage for CASE-455 — post-mutation ApplyError persistence.

A health-timeout used to exit `wip-deploy install` before
`_persist_deployment` ran. Correct when the apply failed before touching
the world; wrong after `compose up` recreated every container — the
persisted state then described the OLD deployment while reality ran the
NEW one (observed: state said target=compose while the stack ran dev, so
`add-app --app-source` refused and a flagless re-install would have
silently reverted the dev conversion).

The contract under test: ApplyError carries `mutated`, stamped True for any
failure raised at-or-after the materializing step (`_run_up` /
`_kubectl_apply_tree`) and False for pre-mutation failures. The CLI persists
the applied spec when `mutated` is True (cli.py install + additive-verbs
except-branches; exercised here at the apply layer where the stamping lives).

CASE-1228 item 5b narrowed the compose half of that rule, and #3 narrowed the
narrowing. `compose up` pulls before it creates, so a failure raised by the up
itself does not imply a change: a pull that never resolved creates no container
and stops none, and persisting the new spec there pins an image that never ran —
the stale override item 5a refuses at the verb, arriving by the back door. So a
failure raised by the up is decided by evidence, comparing this install's
(name, id) container set before the wipe with after: moved → True, identical →
False, unverifiable → True. Past the up step the blanket stamp stands, including
for `services_scope=[]` — a proxy reload rewrites routing, and hooks write state,
with no container touched, none of which identity can see.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any
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


def _apply(*args: Any, **kwargs: Any) -> Any:
    kwargs.setdefault("deployment", _deployment())
    kwargs.setdefault("components", [])
    kwargs.setdefault("apps", [])
    kwargs.setdefault("tree", MagicMock())
    kwargs.setdefault("install_dir", Path("/tmp/case455"))
    return apply_compose(*args, **kwargs)


# Patches shared by every up-stage test: the runtime is always pinned so the
# evidence check never reaches the host's real podman, whose answer would decide
# the assertion on a whim of whatever this machine happens to be running.
_DETECT = patch(
    "wip_deploy.apply._detect_compose_cmd", return_value=["podman-compose"],
)
_RM = patch("wip_deploy.apply._remove_install_containers")
_UP_FAILS = patch(
    "wip_deploy.apply._run_up", side_effect=ApplyError("compose up failed (exit 1)"),
)


class TestApplyErrorMutatedFlag:
    def test_defaults_to_false(self) -> None:
        assert ApplyError("x").mutated is False

    def test_explicit_true(self) -> None:
        assert ApplyError("x", mutated=True).mutated is True


class TestApplyComposeMutationStamping:
    @_DETECT
    @_RM
    @patch("wip_deploy.apply._container_cli", return_value=None)
    @patch("wip_deploy.apply._run_up")
    @patch("wip_deploy.apply._count_services", return_value=3)
    @patch("wip_deploy.apply._wait_healthy", return_value=False)
    @patch("wip_deploy.apply._run_post_install")
    def test_health_timeout_is_post_mutation(
        self, *_mocks: MagicMock, tmp_path: Path = Path("/tmp")
    ) -> None:
        """compose up succeeded, health wait timed out → mutated=True."""
        with pytest.raises(ApplyError) as exc_info:
            _apply()
        assert "timed out" in str(exc_info.value)
        assert exc_info.value.mutated is True

    @_DETECT
    @_RM
    @patch(
        "wip_deploy.apply._project_container_state",
        side_effect=[
            frozenset({("t-registry", "aaa111")}),
            frozenset({("t-registry", "bbb222")}),
        ],
    )
    @patch("wip_deploy.apply._container_cli", return_value="podman")
    @_UP_FAILS
    def test_up_failure_after_containers_moved_is_post_mutation(self, *_mocks: MagicMock) -> None:
        """Same names, different ids: the stack came down or was recreated before
        the up failed. Reality matches neither spec, and the applied one is what a
        re-run produces — CASE-455's protection stays."""
        with pytest.raises(ApplyError) as exc_info:
            _apply()
        assert exc_info.value.mutated is True

    @_DETECT
    @_RM
    @patch(
        "wip_deploy.apply._project_container_state",
        return_value=frozenset({("t-registry", "aaa111")}),
    )
    @patch("wip_deploy.apply._container_cli", return_value="podman")
    @_UP_FAILS
    def test_up_failure_with_the_stack_untouched_is_not_post_mutation(
        self, *_mocks: MagicMock
    ) -> None:
        """CASE-1228 item 5b. An unresolvable image reference fails `up` at the
        pull: nothing was created, nothing was stopped. The running deployment
        still matches the persisted spec, so stamping would have the CLI record a
        tag that never ran."""
        with pytest.raises(ApplyError) as exc_info:
            _apply()
        assert exc_info.value.mutated is False

    @_DETECT
    @_RM
    @patch(
        "wip_deploy.apply._project_container_state",
        side_effect=[frozenset({("t-registry", "aaa111")}), None],
    )
    @patch("wip_deploy.apply._container_cli", return_value="podman")
    @_UP_FAILS
    def test_up_failure_without_a_post_listing_stays_post_mutation(self, *_mocks: MagicMock) -> None:
        """The engine answered before the wipe and not after. An unasked question
        is not evidence that nothing changed, so the blanket stamp stands."""
        with pytest.raises(ApplyError) as exc_info:
            _apply()
        assert exc_info.value.mutated is True

    @_DETECT
    @_RM
    @patch("wip_deploy.apply._container_cli", return_value=None)
    @_UP_FAILS
    def test_up_failure_with_no_runtime_to_ask_stays_post_mutation(self, *_mocks: MagicMock) -> None:
        """No podman/docker on PATH: the comparison cannot be made, and
        CASE-455's assumption is the safe one."""
        with pytest.raises(ApplyError) as exc_info:
            _apply()
        assert exc_info.value.mutated is True

    @_DETECT
    @_RM
    @patch(
        "wip_deploy.apply._project_container_state",
        return_value=frozenset({("t-registry", "aaa111")}),
    )
    @patch("wip_deploy.apply._container_cli", return_value="podman")
    @patch("wip_deploy.apply._run_up")
    @patch("wip_deploy.apply._count_services", return_value=1)
    @patch("wip_deploy.apply._wait_healthy", return_value=False)
    @patch("wip_deploy.apply._run_post_install")
    def test_config_only_scope_past_the_up_step_keeps_the_blanket_stamp(
        self, *_mocks: MagicMock,
    ) -> None:
        """CASE-1228 #3 finding 1. services_scope=[] runs no `compose up`, but the
        steps that follow it — a proxy reload, a post-install hook, the health
        wait — change the install in ways container identity cannot observe: a
        reloaded Caddy serves the new routing with no container created, removed
        or recreated. So the up step counts as done for the empty scope too, and a
        failure there stays mutated=True even though the snapshot is identical.

        The evidence rule applies only to a failure raised by the up itself, where
        a pull that never resolved genuinely moved nothing."""
        with pytest.raises(ApplyError) as exc_info:
            _apply(services_scope=[])
        assert "timed out" in str(exc_info.value)
        assert exc_info.value.mutated is True

    @_DETECT
    @_RM
    @patch(
        "wip_deploy.apply._project_container_state",
        return_value=frozenset({("t-registry", "aaa111")}),
    )
    @patch("wip_deploy.apply._container_cli", return_value="podman")
    @patch("wip_deploy.apply._run_up")
    @patch("wip_deploy.apply._count_services", return_value=1)
    @patch("wip_deploy.apply._run_post_install")
    @patch("wip_deploy.apply._reload_proxy")
    @patch("wip_deploy.apply._wait_healthy", return_value=False)
    def test_a_proxy_reload_then_failure_is_post_mutation(
        self,
        wait_mock: MagicMock,
        reload_mock: MagicMock,
        *_mocks: MagicMock,
    ) -> None:
        """CASE-1228 #3 finding 1, in the shape that actually breaks.

        `services_scope=[]` runs no `compose up`, yet the gate must still read
        done: otherwise a proxy reload — which rewrites routing without creating,
        removing or recreating a container — is judged by container identity, sees
        an unchanged snapshot, and stamps mutated=False. The CLI would then keep
        the old spec while Caddy is already serving the new one, which is the
        CASE-455 fiction reintroduced by the narrowing."""
        install_dir = Path(tempfile.mkdtemp(prefix="case1228-3-"))
        # Nothing exists at the pre-write snapshot; the renderer's write is what
        # makes the config differ, so the reload branch really fires.
        tree = MagicMock()

        def write_tree(dest: Path) -> None:
            target = Path(dest) / "config" / "caddy" / "Caddyfile"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("handle /api { ... }")

        tree.write.side_effect = write_tree

        with pytest.raises(ApplyError) as exc_info:
            _apply(services_scope=[], tree=tree, install_dir=install_dir)

        reload_mock.assert_called_once()
        assert "timed out" in str(exc_info.value)
        assert exc_info.value.mutated is True
        shutil.rmtree(install_dir, ignore_errors=True)

    @patch(
        "wip_deploy.apply._detect_compose_cmd",
        side_effect=ApplyError("neither podman-compose nor docker is available on PATH"),
    )
    def test_pre_mutation_failure_keeps_flag_false(self, *_mocks: MagicMock) -> None:
        """No compose binary → nothing ran → old state is still the
        truthful last-known-good."""
        with pytest.raises(ApplyError) as exc_info:
            _apply()
        assert exc_info.value.mutated is False

    @_DETECT
    @_RM
    @patch("wip_deploy.apply._container_cli", return_value=None)
    @patch("wip_deploy.apply._run_up")
    @patch("wip_deploy.apply._count_services", return_value=3)
    @patch("wip_deploy.apply._wait_healthy", return_value=False)
    @patch("wip_deploy.apply._run_post_install")
    def test_on_timeout_warn_still_returns_result(self, *_mocks: MagicMock) -> None:
        """warn mode: no raise, result.healthy=False — the CLI's normal
        post-apply persist runs (unchanged behaviour)."""
        result = _apply(deployment=_deployment(on_timeout="warn"))
        assert result.healthy is False
