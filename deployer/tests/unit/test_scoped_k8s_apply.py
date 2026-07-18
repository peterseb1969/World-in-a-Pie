"""Contract tests for the scoped k8s apply.

A single-service mutation on a k8s install (an app tag roll via
app-deploy, an add-app) must apply ONLY the shared cross-cutting files
plus the named services' manifests — never the full tree, never with
--prune. "kubectl apply is already incremental" is not a scope: the
full-tree apply recreates any unrelated manifest that drifted from live
(renderer churn between invocations), which once bounced mongodb and
flashed every Mongo-backed service unhealthy during a one-app tag roll;
prune against a partial apply set would delete what isn't in it.

Deletion still requires the full apply's prune, so on k8s only a
NON-EMPTY scope narrows the apply — the empty scope (remove-app's
compose config-only mode) and None fall back to the full tree.

Layers:
- `_kubectl_apply_scoped` over a synthetic install dir, `_kubectl_run`
  mocked — pins the exact apply set and the absence of -R/--prune.
- `apply_k8s` with a synthetic tree and wait disabled — pins that the
  scope narrows the apply, the workload count, and the hook set.
- `_apply_and_persist_mutation` forwarding — pins the non-empty-only
  rule on k8s and unconditional forwarding on compose.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wip_deploy.apply import ApplyError, _kubectl_apply_scoped, apply_k8s
from wip_deploy.renderers import FileTree
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

SHARED = ("secrets.yaml", "configmaps.yaml", "ingress.yaml", "network-policies.yaml")


def _write_install_dir(tmp_path: Path, services: dict[str, str]) -> Path:
    """Materialize a minimal rendered tree: shared files + per-service
    manifests. `services` maps name → subdir ('services' or
    'infrastructure')."""
    (tmp_path / "namespace.yaml").write_text("kind: Namespace\n")
    for rel in SHARED:
        (tmp_path / rel).write_text("{}\n")
    for name, subdir in services.items():
        d = tmp_path / subdir
        d.mkdir(exist_ok=True)
        (d / f"{name}.yaml").write_text("kind: Deployment\n")
    return tmp_path


def _applied_files(mock_run: MagicMock) -> list[str]:
    """Extract the -f argument of every kubectl apply call."""
    out = []
    for call in mock_run.call_args_list:
        args = call.args[0]
        assert args[0] == "apply", args
        out.append(Path(args[args.index("-f") + 1]).name)
    return out


class TestKubectlApplyScoped:
    def test_applies_shared_plus_named_service_only(self, tmp_path: Path) -> None:
        _write_install_dir(
            tmp_path,
            {"wip-kb": "services", "mongodb": "infrastructure"},
        )
        with patch("wip_deploy.apply._kubectl_run") as mock_run:
            _kubectl_apply_scoped(tmp_path, "kb", ["wip-kb"])

        applied = _applied_files(mock_run)
        assert applied == [
            "namespace.yaml", *SHARED, "wip-kb.yaml",
        ]
        # The drifted-collateral regression: an unrelated manifest that
        # exists in the rendered tree must NOT be in the apply set.
        assert "mongodb.yaml" not in applied

    def test_never_recursive_never_prune(self, tmp_path: Path) -> None:
        _write_install_dir(tmp_path, {"wip-kb": "services"})
        with patch("wip_deploy.apply._kubectl_run") as mock_run:
            _kubectl_apply_scoped(tmp_path, "kb", ["wip-kb"])

        for call in mock_run.call_args_list:
            args = call.args[0]
            assert "-R" not in args, args
            assert not any(a.startswith("--prune") for a in args), args

    def test_infrastructure_manifest_is_found(self, tmp_path: Path) -> None:
        _write_install_dir(tmp_path, {"statefulapp": "infrastructure"})
        with patch("wip_deploy.apply._kubectl_run") as mock_run:
            _kubectl_apply_scoped(tmp_path, "kb", ["statefulapp"])
        assert "statefulapp.yaml" in _applied_files(mock_run)

    def test_missing_manifest_is_a_hard_error(self, tmp_path: Path) -> None:
        _write_install_dir(tmp_path, {"wip-kb": "services"})
        with patch("wip_deploy.apply._kubectl_run"):
            with pytest.raises(ApplyError, match="no rendered manifest"):
                _kubectl_apply_scoped(tmp_path, "kb", ["nonexistent"])


# ────────────────────────────────────────────────────────────────────
# apply_k8s with a scope
# ────────────────────────────────────────────────────────────────────


def _k8s_deployment(apps: list[str]) -> Deployment:
    return Deployment(
        metadata=DeploymentMetadata(name="scoped-test"),
        spec=DeploymentSpec(
            target="k8s",
            apps=[AppRef(name=a, enabled=True) for a in apps],
            auth=AuthSpec(mode="api-key-only", gateway=False),
            network=NetworkSpec(hostname="wip-test.local"),
            images=ImagesSpec(registry="ghcr.io/x", tag="t1"),
            platform=PlatformSpec(k8s=K8sPlatform(namespace="kb")),
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
            apply={"wait": False},
        ),
    )


def _fake_app(name: str) -> MagicMock:
    app = MagicMock()
    app.metadata.name = name
    app.spec.storage = None
    app.spec.post_install = []
    return app


def _synthetic_tree(services: dict[str, str]) -> FileTree:
    tree = FileTree()
    tree.add("namespace.yaml", "kind: Namespace\n")
    for rel in SHARED:
        tree.add(rel, "{}\n")
    for name, subdir in services.items():
        tree.add(f"{subdir}/{name}.yaml", "kind: Deployment\n")
    return tree


class TestApplyK8sScoped:
    def test_scope_narrows_apply_and_summary(self, tmp_path: Path) -> None:
        deployment = _k8s_deployment(apps=["wip-kb", "react-console"])
        tree = _synthetic_tree(
            {
                "wip-kb": "services",
                "react-console": "services",
                "mongodb": "infrastructure",
            }
        )
        with (
            patch("wip_deploy.apply.shutil.which", return_value="/usr/bin/kubectl"),
            patch("wip_deploy.apply._kubectl_run") as mock_run,
        ):
            result = apply_k8s(
                deployment=deployment,
                components=[],
                apps=[_fake_app("wip-kb"), _fake_app("react-console")],
                tree=tree,
                install_dir=tmp_path,
                services_scope=["wip-kb"],
            )

        applied = _applied_files(mock_run)
        assert "wip-kb.yaml" in applied
        assert "react-console.yaml" not in applied
        assert "mongodb.yaml" not in applied
        # The summary reflects the scoped set, not the whole tree.
        assert result.services_up == 1

    def test_no_scope_applies_full_tree(self, tmp_path: Path) -> None:
        deployment = _k8s_deployment(apps=["wip-kb"])
        tree = _synthetic_tree({"wip-kb": "services", "mongodb": "infrastructure"})
        with (
            patch("wip_deploy.apply.shutil.which", return_value="/usr/bin/kubectl"),
            patch("wip_deploy.apply._kubectl_run") as mock_run,
        ):
            apply_k8s(
                deployment=deployment,
                components=[],
                apps=[_fake_app("wip-kb")],
                tree=tree,
                install_dir=tmp_path,
            )

        # Full-tree path: recursive apply with prune, exactly as before.
        full_apply = mock_run.call_args_list[-1].args[0]
        assert "-R" in full_apply
        assert "--prune" in full_apply


# ────────────────────────────────────────────────────────────────────
# _apply_and_persist_mutation forwarding
# ────────────────────────────────────────────────────────────────────


class TestScopeForwarding:
    def _run(self, deployment: Deployment, scope: list[str] | None) -> MagicMock:
        from wip_deploy.cli import _apply_and_persist_mutation

        result = MagicMock(healthy=True, post_install_skipped=[])
        with (
            patch("wip_deploy.cli._validate_or_exit"),
            patch("wip_deploy.cli._ensure_secrets_via_spec"),
            patch("wip_deploy.cli._render_tree"),
            patch("wip_deploy.cli._persist_deployment"),
            patch("wip_deploy.cli.apply_k8s", return_value=result) as mock_apply,
            patch("wip_deploy.cli.apply_compose", return_value=result),
        ):
            _apply_and_persist_mutation(
                deployment, [], [], Path("/tmp/x"), "test", services_scope=scope,
            )
        return mock_apply

    def test_nonempty_scope_is_forwarded_on_k8s(self) -> None:
        mock_apply = self._run(_k8s_deployment(apps=["wip-kb"]), ["wip-kb"])
        assert mock_apply.call_args.kwargs["services_scope"] == ["wip-kb"]

    def test_empty_scope_falls_back_to_full_tree_on_k8s(self) -> None:
        """Empty scope is remove-app's compose config-only mode; on k8s
        deletion happens via the full apply's prune, so the scope must
        NOT be forwarded."""
        mock_apply = self._run(_k8s_deployment(apps=[]), [])
        assert "services_scope" not in mock_apply.call_args.kwargs

    def test_none_scope_falls_back_to_full_tree_on_k8s(self) -> None:
        mock_apply = self._run(_k8s_deployment(apps=[]), None)
        assert "services_scope" not in mock_apply.call_args.kwargs