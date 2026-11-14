"""A module removal must not prune the install's volume claims.

`remove-module` drops workloads from the render: `remove-module garage` takes
the StatefulSet and its 50Gi claim out of the tree, and on k8s workloads leave
only through the full apply's prune. The verb never passed `removes_workloads`,
so it kept `prune_volume_claims` at its default True and that prune ran with
PersistentVolumeClaim on the allowlist — the module's volume went with it, the
storage class's reclaim policy deciding whether the bytes survived, while the
verb's own help says "without dropping anything". `remove-app` already refuses
to prune claims for exactly this reason; a removal is a removal.

Layers, each driven through the CLI verb so a call site that forgets the signal
fails rather than the helper that reads it:
- the verb's kwargs to the mutation lifecycle,
- the lifecycle's kwargs to `apply_k8s`,
- the real `apply_k8s` → the exact `kubectl apply --prune` allowlist, plus the
  kept claim named with the command that deletes it deliberately.

Guards keep the fix narrow: adding a module still prunes as before, and compose
has no prune and no such flag to pass.
"""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from wip_deploy.cli import _persist_deployment, app
from wip_deploy.renderers import FileTree
from wip_deploy.spec import (
    ComposePlatform,
    Deployment,
    PlatformSpec,
)

from .renderers.test_compose import REPO_ROOT
from .test_scoped_k8s_apply import _k8s_deployment, _synthetic_tree

runner = CliRunner()

MODULE = "garage"
# What the cluster holds before the removal: the core store's claim, and the
# module's claim the render no longer names once the module is disabled.
LIVE_CLAIMS = "wip-mongodb-data wip-garage-data"


def _render_without_module() -> FileTree:
    """The tree a `remove-module garage` apply writes: the front door and the
    core store remain; the module and its claim are gone from it."""
    tree = _synthetic_tree({"caddy": "services", "mongodb": "infrastructure"})
    tree.add(
        "infrastructure/mongodb.yaml",
        "kind: PersistentVolumeClaim\n"
        "metadata:\n"
        "  name: wip-mongodb-data\n"
        "---\n"
        "kind: StatefulSet\n",
    )
    return tree


def _deployment(target: str) -> Deployment:
    deployment = _k8s_deployment(apps=[])
    if target != "k8s":
        deployment = Deployment(
            metadata=deployment.metadata,
            spec=deployment.spec.model_copy(
                update={
                    "target": target,
                    "platform": PlatformSpec(compose=ComposePlatform(data_dir="/tmp/d")),
                },
            ),
        )
    deployment.spec.modules.optional = [MODULE]
    return deployment


def _install_dir(tmp_path: Path, deployment: Deployment) -> Path:
    """A saved state that runs the module, so the verb has something to remove."""
    _persist_deployment(deployment, tmp_path, repo_root=REPO_ROOT)
    return tmp_path


def _gates(tree: FileTree) -> list:
    """Stub everything around the verb's apply: the surrounding checks, the
    render, the save, the container sweep. The apply itself is left to the
    caller — stubbed to capture its kwargs, or run for real."""
    return [
        patch("wip_deploy.cli._validate_or_exit"),
        patch("wip_deploy.cli._select_cluster_or_exit"),
        patch("wip_deploy.cli._ensure_secrets_via_spec"),
        patch("wip_deploy.cli._refuse_runtime_reservation_collisions"),
        patch("wip_deploy.cli._refuse_attachment_collisions"),
        patch("wip_deploy.cli._render_tree", return_value=tree),
        patch("wip_deploy.cli._persist_deployment"),
        patch("wip_deploy.apply.stop_and_remove_container"),
    ]


def _run_verb(
    tmp_path: Path, *, target: str = "k8s", real_apply: bool = False
) -> tuple[MagicMock, str]:
    """`remove-module garage` for the given target; returns the mock standing at
    the apply seam — the kwargs the verb handed that target's apply, or, with
    `real_apply`, the `kubectl` invocations the real apply produced — and what
    the command told the operator."""
    install_dir = _install_dir(tmp_path, _deployment(target))
    helpers = _gates(_render_without_module())

    if real_apply:
        helpers += [
            patch("wip_deploy.apply.shutil.which", return_value="/usr/bin/kubectl"),
            patch("wip_deploy.apply.kubectl", return_value=["kubectl"]),
            patch("wip_deploy.apply._ensure_self_signed_tls_secret"),
            patch(
                "wip_deploy.apply.subprocess.run",
                return_value=MagicMock(returncode=0, stdout=LIVE_CLAIMS),
            ),
        ]
        seam = patch("wip_deploy.apply._kubectl_run")
    else:
        apply_name = "apply_k8s" if target == "k8s" else "apply_compose"
        seam = patch(
            f"wip_deploy.cli.{apply_name}",
            return_value=MagicMock(
                healthy=True, post_install_skipped=[], health_checked=True
            ),
        )

    with ExitStack() as stack:
        for p in helpers:
            stack.enter_context(p)
        captured = stack.enter_context(seam)
        res = runner.invoke(
            app,
            ["remove-module", MODULE, "--install-dir", str(install_dir),
             "--repo-root", str(REPO_ROOT)],
        )
    assert res.exit_code == 0, res.output + repr(res.exception)
    return captured, res.output


class TestModuleRemovalKeepsClaims:
    def test_the_apply_is_told_to_keep_volume_claims(self, tmp_path: Path) -> None:
        """A removal that does not say so runs the prune with claims allowed."""
        k8s, _ = _run_verb(tmp_path)
        assert k8s.call_args.kwargs["prune_volume_claims"] is False

    def test_the_apply_still_deletes_the_workloads(self, tmp_path: Path) -> None:
        """Keeping the claim must not cost the removal its prune: without the
        whole tree the module's StatefulSet and Service stay running."""
        k8s, _ = _run_verb(tmp_path)
        assert "services_scope" not in k8s.call_args.kwargs

    def test_kubectl_prunes_every_kind_but_claims(self, tmp_path: Path) -> None:
        """The end of the chain: the full-tree prune runs, PersistentVolumeClaim
        is off its allowlist, and the module's claim is named as kept."""
        kubectl_run, out = _run_verb(tmp_path, real_apply=True)

        prune = [c.args[0] for c in kubectl_run.call_args_list if "--prune" in c.args[0]]
        assert len(prune) == 1, kubectl_run.call_args_list
        assert "apps/v1/StatefulSet" in prune[0]
        assert "core/v1/PersistentVolumeClaim" not in prune[0]

        assert "kept volume claim 'wip-garage-data'" in out
        assert "kubectl delete pvc wip-garage-data" in out
        # A claim the render still names is not reported as left behind.
        assert "wip-mongodb-data" not in out


class TestNonRemovalsAreUnchanged:
    """The signal marks a removal. Adding a module deletes no workload, and
    compose has no prune and takes no such flag."""

    def test_add_module_keeps_default_pruning(self, tmp_path: Path) -> None:
        deployment = _k8s_deployment(apps=[])
        _persist_deployment(deployment, tmp_path, repo_root=REPO_ROOT)
        result = MagicMock(healthy=True, post_install_skipped=[], health_checked=True)
        with (
            patch("wip_deploy.cli._validate_or_exit"),
            patch("wip_deploy.cli._select_cluster_or_exit"),
            patch("wip_deploy.cli._ensure_secrets_via_spec"),
            patch("wip_deploy.cli._refuse_runtime_reservation_collisions"),
            patch("wip_deploy.cli._refuse_attachment_collisions"),
            patch("wip_deploy.cli._render_tree", return_value=_synthetic_tree({})),
            patch("wip_deploy.cli._persist_deployment"),
            patch("wip_deploy.cli.apply_k8s", return_value=result) as k8s,
            patch("wip_deploy.cli.apply_compose", return_value=result),
        ):
            res = runner.invoke(
                app,
                ["add-module", MODULE, "--install-dir", str(tmp_path),
                 "--repo-root", str(REPO_ROOT)],
            )
        assert res.exit_code == 0, res.output + repr(res.exception)
        assert "prune_volume_claims" not in k8s.call_args.kwargs

    def test_a_compose_module_removal_passes_no_prune_flag(self, tmp_path: Path) -> None:
        """`apply_compose` has no such parameter; the removal must not grow one."""
        compose, _ = _run_verb(tmp_path, target="compose")
        assert "prune_volume_claims" not in compose.call_args.kwargs
        assert compose.call_args.kwargs["services_scope"] is None
