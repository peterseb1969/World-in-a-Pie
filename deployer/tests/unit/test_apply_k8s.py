"""Tests for `apply_k8s` and its helpers.

Exercises the pure-logic helpers with synthetic data and the
subprocess-driven entry points with mocks. We don't spin up a real
cluster — k8s integration is validated end-to-end on the actual
kubi5-1 cluster by human runs of `wip-deploy install --target k8s`.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wip_deploy.apply import (
    ApplyError,
    _clean_rendered_tree,
    _count_k8s_workloads,
    _expected_workloads,
    _pod_for_component,
    apply_k8s,
)
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import FileTree, render_k8s
from wip_deploy.secrets import ensure_secrets
from wip_deploy.secrets_backend import FileSecretBackend
from wip_deploy.spec import (
    AppRef,
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

REPO_ROOT = Path(__file__).parent.parent.parent.parent.resolve()


@pytest.fixture(scope="module")
def real_discovery() -> Discovery:
    return discover(REPO_ROOT)


def _k8s_deployment(**overrides: object) -> Deployment:
    defaults = {
        "target": "k8s",
        "modules": {"optional": ["mcp-server"]},
        "apps": [],
        "auth": AuthSpec(mode="oidc", gateway=True),
        "network": NetworkSpec(hostname="wip-test.local"),
        "images": ImagesSpec(registry="ghcr.io/peterseb1969", tag="v1.1.0"),
        "platform": PlatformSpec(k8s=K8sPlatform(namespace="wip-test")),
        "secrets": SecretsSpec(backend="file", location="/tmp/s"),
    }
    defaults.update(overrides)
    return Deployment(
        metadata=DeploymentMetadata(name="k8s-test"),
        spec=DeploymentSpec(**defaults),  # type: ignore[arg-type]
    )


# ────────────────────────────────────────────────────────────────────
# _expected_workloads
# ────────────────────────────────────────────────────────────────────


class TestExpectedWorkloads:
    def test_storage_component_is_statefulset(
        self, real_discovery: Discovery
    ) -> None:
        # `reporting-sync` module activates postgres (storage-bearing).
        d = _k8s_deployment(modules={"optional": ["reporting-sync"]})
        kinds: dict[str, str] = {
            name: kind
            for kind, name in _expected_workloads(
                real_discovery.components, real_discovery.apps, d
            )
        }
        # postgres has storage → StatefulSet
        assert kinds.get("wip-postgres") == "StatefulSet"
        # registry is stateless → Deployment
        assert kinds.get("wip-registry") == "Deployment"

    def test_inactive_components_excluded(
        self, real_discovery: Discovery
    ) -> None:
        # NATS requires reporting-sync or ingest-gateway — neither enabled here.
        d = _k8s_deployment(modules={"optional": []})
        workloads = _expected_workloads(
            real_discovery.components, real_discovery.apps, d
        )
        names = {n for _, n in workloads}
        assert "wip-nats" not in names

    def test_disabled_apps_excluded(self, real_discovery: Discovery) -> None:
        d = _k8s_deployment(apps=[])  # no apps enabled
        workloads = _expected_workloads(
            real_discovery.components, real_discovery.apps, d
        )
        names = {n for _, n in workloads}
        assert not any(n.startswith("wip-clintrial") for n in names)


# ────────────────────────────────────────────────────────────────────
# _count_k8s_workloads
# ────────────────────────────────────────────────────────────────────


class TestCountK8sWorkloads:
    def test_counts_deployments_and_statefulsets(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _k8s_deployment()
        secrets = ensure_secrets(
            d,
            real_discovery.components,
            real_discovery.apps,
            FileSecretBackend(tmp_path / "secrets"),
        )
        tree = render_k8s(
            d, real_discovery.components, real_discovery.apps, secrets
        )
        count = _count_k8s_workloads(tree)
        # At minimum, the standard preset renders several workloads.
        assert count >= 5

    def test_ignores_namespace_and_secrets_files(self) -> None:
        tree = FileTree()
        tree.add("namespace.yaml", "kind: Namespace\n")
        tree.add("secrets.yaml", "kind: Secret\n")
        tree.add("services/foo.yaml", "kind: Deployment\nmetadata: {name: foo}\n")
        assert _count_k8s_workloads(tree) == 1

    def test_handles_multi_doc_yaml(self) -> None:
        tree = FileTree()
        tree.add(
            "services/multi.yaml",
            "kind: Service\n---\nkind: Deployment\n---\nkind: StatefulSet\n",
        )
        assert _count_k8s_workloads(tree) == 2


# ────────────────────────────────────────────────────────────────────
# apply_k8s — subprocess integration (mocked)
# ────────────────────────────────────────────────────────────────────


class TestApplyK8s:
    def _fake_tree(self) -> FileTree:
        tree = FileTree()
        tree.add("namespace.yaml", "kind: Namespace\nmetadata: {name: wip-test}\n")
        tree.add(
            "services/registry.yaml",
            "kind: Deployment\nmetadata: {name: wip-registry}\n",
        )
        return tree

    @patch("wip_deploy.apply.shutil.which", return_value=None)
    def test_missing_kubectl_raises(
        self, _which: MagicMock, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _k8s_deployment()
        with pytest.raises(ApplyError, match="kubectl not on PATH"):
            apply_k8s(
                deployment=d,
                components=real_discovery.components,
                apps=real_discovery.apps,
                tree=self._fake_tree(),
                install_dir=tmp_path,
            )

    @patch("wip_deploy.apply.subprocess.run")
    @patch("wip_deploy.apply.shutil.which", return_value="/usr/bin/kubectl")
    def test_happy_path_writes_tree_and_applies(
        self,
        _which: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
        real_discovery: Discovery,
    ) -> None:
        # Pod-lookup must return a name; everything else just needs rc=0.
        def side_effect(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
            if "get" in cmd and "pod" in cmd:
                return MagicMock(returncode=0, stdout="wip-mock-pod", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        d = _k8s_deployment()
        d.spec.apply.wait = False

        tree = self._fake_tree()
        result = apply_k8s(
            deployment=d,
            components=real_discovery.components,
            apps=real_discovery.apps,
            tree=tree,
            install_dir=tmp_path,
        )

        # Tree got written.
        assert (tmp_path / "namespace.yaml").exists()
        assert (tmp_path / "services" / "registry.yaml").exists()

        # kubectl got called at least twice: namespace-first, then recursive apply.
        apply_calls = [
            c for c in mock_run.call_args_list if c.args[0][:2] == ["kubectl", "apply"]
        ]
        assert len(apply_calls) >= 2

        # Recursive apply must carry --prune so stale resources get cleaned up.
        recursive = next(c for c in apply_calls if "-R" in c.args[0])
        assert "--prune" in recursive.args[0]
        assert "--selector=app.kubernetes.io/part-of=wip" in recursive.args[0]
        # Namespace must not be in the prune allowlist (cluster-scoped safety).
        allowlisted = [
            recursive.args[0][i + 1]
            for i, a in enumerate(recursive.args[0])
            if a == "--prune-allowlist"
        ]
        assert "apps/v1/Deployment" in allowlisted
        assert not any("Namespace" in a for a in allowlisted)

        assert result.healthy is True

    @patch("wip_deploy.apply.subprocess.run")
    @patch("wip_deploy.apply.shutil.which", return_value="/usr/bin/kubectl")
    def test_on_timeout_fail_raises(
        self,
        _which: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
        real_discovery: Discovery,
    ) -> None:
        import subprocess

        # Apply succeeds; rollout status fails (timeout).
        def side_effect(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
            if "rollout" in cmd:
                raise subprocess.CalledProcessError(1, cmd, stderr="timed out")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        d = _k8s_deployment()
        d.spec.apply.wait = True
        d.spec.apply.timeout_seconds = 1
        d.spec.apply.on_timeout = "fail"

        with pytest.raises(ApplyError, match="timed out"):
            apply_k8s(
                deployment=d,
                components=real_discovery.components,
                apps=real_discovery.apps,
                tree=self._fake_tree(),
                install_dir=tmp_path,
            )


# ────────────────────────────────────────────────────────────────────
# _pod_for_component
# ────────────────────────────────────────────────────────────────────


class TestCleanRenderedTree:
    def test_removes_top_level_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "namespace.yaml").write_text("kind: Namespace\n")
        (tmp_path / "ingress.yaml").write_text("kind: Ingress\n")
        _clean_rendered_tree(tmp_path)
        assert not (tmp_path / "namespace.yaml").exists()
        assert not (tmp_path / "ingress.yaml").exists()

    def test_removes_services_and_infrastructure(self, tmp_path: Path) -> None:
        (tmp_path / "services").mkdir()
        (tmp_path / "services" / "registry.yaml").write_text("kind: Deployment\n")
        (tmp_path / "infrastructure").mkdir()
        (tmp_path / "infrastructure" / "postgres.yaml").write_text("kind: StatefulSet\n")
        _clean_rendered_tree(tmp_path)
        assert not (tmp_path / "services").exists()
        assert not (tmp_path / "infrastructure").exists()

    def test_preserves_secrets_subdirectory(self, tmp_path: Path) -> None:
        """The secret backend often lives under install_dir/secrets/ —
        nuking it would force secret regeneration on every apply."""
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        (secrets_dir / "api-key").write_text("super-secret-value")
        (tmp_path / "namespace.yaml").write_text("kind: Namespace\n")
        _clean_rendered_tree(tmp_path)
        assert (secrets_dir / "api-key").read_text() == "super-secret-value"

    def test_noop_on_missing_dir(self, tmp_path: Path) -> None:
        _clean_rendered_tree(tmp_path / "does-not-exist")  # must not raise

    def test_leaves_unknown_files_alone(self, tmp_path: Path) -> None:
        (tmp_path / "notes.txt").write_text("keep me")
        (tmp_path / "unknown-dir").mkdir()
        (tmp_path / "unknown-dir" / "data").write_text("keep")
        _clean_rendered_tree(tmp_path)
        assert (tmp_path / "notes.txt").exists()
        assert (tmp_path / "unknown-dir" / "data").exists()


class TestPodForComponent:
    @patch("wip_deploy.apply.subprocess.run")
    def test_returns_pod_name(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="wip-registry-abc123", stderr=""
        )
        assert _pod_for_component("wip-test", "registry") == "wip-registry-abc123"

    @patch("wip_deploy.apply.subprocess.run")
    def test_empty_output_raises(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with pytest.raises(ApplyError, match="no running pod"):
            _pod_for_component("wip-test", "ghost-component")
