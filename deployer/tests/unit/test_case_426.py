"""Regression coverage for CASE-426 — post-install hooks must not run
unless health was established.

Post-install hooks declare `after: healthy` (PostInstallHook spec default;
both shipping hooks — registry's namespace init and minio's bucket create —
set it). The runners used to fire unconditionally right after apply, so on a
k8s `--no-wait` install the `--field-selector=status.phase=Running` pod
lookup hit empty `items` milliseconds after apply and the install died on the
jsonpath `array index out of bounds`. The compose path had the same
unconditional shape.

The fix gates post-install on `wait AND healthy`:
- `--no-wait` (health never established) → skip.
- waited + timed-out + on_timeout != "fail" (warn/continue) → skip.
- waited + healthy → run (unchanged happy path).

Skipped hooks are recorded on `ApplyResult.post_install_skipped` so the CLI
can surface them — skipping leaves setup incomplete (bucket/namespace
bootstrap), which must be visible, not silent.

These exercise the gating + accounting at the apply layer with mocked
runners; real k8s exec is validated by human cluster runs.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from wip_deploy.apply import (
    _post_install_owners,
    _skipped_post_install_labels,
    apply_compose,
    apply_k8s,
)
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import FileTree
from wip_deploy.spec import (
    AuthSpec,
    ComposePlatform,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    ImagesSpec,
    K8sPlatform,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)
from wip_deploy.spec.deployment import ApplySpec

REPO_ROOT = Path(__file__).parent.parent.parent.parent.resolve()

# registry is core → always active; its hook is the floor that guarantees
# the skip list is non-empty regardless of preset.
REGISTRY_HOOK_LABEL = "initialize-wip-namespaces on registry"


def _discovery() -> Discovery:
    return discover(REPO_ROOT)


def _k8s_deployment(*, wait: bool, on_timeout: str = "fail") -> Deployment:
    return Deployment(
        metadata=DeploymentMetadata(name="k8s-test"),
        spec=DeploymentSpec(
            target="k8s",
            auth=AuthSpec(mode="oidc", gateway=True),
            network=NetworkSpec(hostname="wip-test.local"),
            images=ImagesSpec(registry="ghcr.io/peterseb1969", tag="v1.1.0"),
            platform=PlatformSpec(k8s=K8sPlatform(namespace="wip-test")),
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
            apply=ApplySpec(wait=wait, on_timeout=on_timeout),
        ),
    )


def _compose_deployment(*, wait: bool, on_timeout: str = "fail") -> Deployment:
    return Deployment(
        metadata=DeploymentMetadata(name="compose-test"),
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


# ────────────────────────────────────────────────────────────────────
# Hook accounting helpers
# ────────────────────────────────────────────────────────────────────


class TestSkippedPostInstallLabels:
    def test_labels_cover_every_owned_hook(self) -> None:
        d = _compose_deployment(wait=True)
        disc = _discovery()
        owners = _post_install_owners(disc.components, disc.apps, d)
        expected = [f"{h.name} on {owner}" for owner, hooks in owners for h in hooks]
        labels = _skipped_post_install_labels(disc.components, disc.apps, d)
        assert labels == expected
        assert REGISTRY_HOOK_LABEL in labels
        # registry is core; there is always at least one hook to skip.
        assert labels


# ────────────────────────────────────────────────────────────────────
# apply_k8s gating
# ────────────────────────────────────────────────────────────────────


class TestApplyK8sPostInstallGating:
    @patch("wip_deploy.apply._run_post_install_k8s")
    @patch("wip_deploy.apply._count_k8s_workloads", return_value=1)
    @patch("wip_deploy.apply._kubectl_apply_tree")
    @patch("wip_deploy.apply.shutil.which", return_value="/usr/bin/kubectl")
    def test_no_wait_skips_and_records(
        self,
        _which: MagicMock,
        _apply: MagicMock,
        _count: MagicMock,
        run_hooks: MagicMock,
        tmp_path: Path,
    ) -> None:
        disc = _discovery()
        result = apply_k8s(
            deployment=_k8s_deployment(wait=False),
            components=disc.components,
            apps=disc.apps,
            tree=FileTree(),
            install_dir=tmp_path,
        )
        # The race fix: with --no-wait the runner is never entered, so the
        # empty-items pod lookup that killed the install can't fire.
        run_hooks.assert_not_called()
        assert result.healthy is True
        assert REGISTRY_HOOK_LABEL in result.post_install_skipped

    @patch("wip_deploy.apply._run_post_install_k8s")
    @patch("wip_deploy.apply._wait_k8s_rollout", return_value=True)
    @patch("wip_deploy.apply._count_k8s_workloads", return_value=1)
    @patch("wip_deploy.apply._kubectl_apply_tree")
    @patch("wip_deploy.apply.shutil.which", return_value="/usr/bin/kubectl")
    def test_waited_healthy_runs(
        self,
        _which: MagicMock,
        _apply: MagicMock,
        _count: MagicMock,
        _wait: MagicMock,
        run_hooks: MagicMock,
        tmp_path: Path,
    ) -> None:
        result = apply_k8s(
            deployment=_k8s_deployment(wait=True),
            components=(disc := _discovery()).components,
            apps=disc.apps,
            tree=FileTree(),
            install_dir=tmp_path,
        )
        run_hooks.assert_called_once()
        assert result.healthy is True
        assert result.post_install_skipped == []

    @patch("wip_deploy.apply._run_post_install_k8s")
    @patch("wip_deploy.apply._wait_k8s_rollout", return_value=False)
    @patch("wip_deploy.apply._count_k8s_workloads", return_value=1)
    @patch("wip_deploy.apply._kubectl_apply_tree")
    @patch("wip_deploy.apply.shutil.which", return_value="/usr/bin/kubectl")
    def test_waited_timeout_warn_skips(
        self,
        _which: MagicMock,
        _apply: MagicMock,
        _count: MagicMock,
        _wait: MagicMock,
        run_hooks: MagicMock,
        tmp_path: Path,
    ) -> None:
        # warn/continue: no raise, but health was not reached — the narrower
        # race the case's Notes flagged. Hooks must still be skipped.
        result = apply_k8s(
            deployment=_k8s_deployment(wait=True, on_timeout="warn"),
            components=(disc := _discovery()).components,
            apps=disc.apps,
            tree=FileTree(),
            install_dir=tmp_path,
        )
        run_hooks.assert_not_called()
        assert result.healthy is False
        assert REGISTRY_HOOK_LABEL in result.post_install_skipped


# ────────────────────────────────────────────────────────────────────
# apply_compose gating (sibling race)
# ────────────────────────────────────────────────────────────────────


class TestApplyComposePostInstallGating:
    @patch("wip_deploy.apply._run_post_install")
    @patch("wip_deploy.apply._count_services", return_value=1)
    @patch("wip_deploy.apply._run_up")
    @patch("wip_deploy.apply._remove_stale_wip_containers")
    @patch("wip_deploy.apply._detect_compose_cmd", return_value=["podman-compose"])
    def test_no_wait_skips_and_records(
        self, *_mocks: MagicMock
    ) -> None:
        run_hooks = _mocks[-1]  # innermost decorator = _run_post_install
        disc = _discovery()
        result = apply_compose(
            deployment=_compose_deployment(wait=False),
            components=disc.components,
            apps=disc.apps,
            tree=MagicMock(),
            install_dir=Path("/tmp/case426"),
        )
        run_hooks.assert_not_called()
        assert result.healthy is True
        assert REGISTRY_HOOK_LABEL in result.post_install_skipped

    @patch("wip_deploy.apply._run_post_install")
    @patch("wip_deploy.apply._wait_healthy", return_value=True)
    @patch("wip_deploy.apply._count_services", return_value=1)
    @patch("wip_deploy.apply._run_up")
    @patch("wip_deploy.apply._remove_stale_wip_containers")
    @patch("wip_deploy.apply._detect_compose_cmd", return_value=["podman-compose"])
    def test_waited_healthy_runs(
        self, *_mocks: MagicMock
    ) -> None:
        run_hooks = _mocks[-1]
        disc = _discovery()
        result = apply_compose(
            deployment=_compose_deployment(wait=True),
            components=disc.components,
            apps=disc.apps,
            tree=MagicMock(),
            install_dir=Path("/tmp/case426"),
        )
        run_hooks.assert_called_once()
        assert result.healthy is True
        assert result.post_install_skipped == []
