"""No k8s apply prunes a volume claim — and every whole-tree apply says what it kept.

`remove-module garage` was made to keep the module's 50Gi claim and print the
`kubectl delete pvc` command for it (test_case_1229_review2). The keep was a
per-verb opt-in: `prune_volume_claims=False` was set only where
`removes_workloads` was, so every other whole-tree apply still ran with
PersistentVolumeClaim on the prune allowlist. The operator who followed the
removal's own advice — delete the claim by hand, once the data is not needed —
was told the truth for one command and then upgraded with the k8s README's
`wip-deploy redeploy --name wip --tag <newtag>`: that re-renders without the
module's claim, and the prune takes it. With the usual
`reclaimPolicy: Delete` the volume and the objects in it go, one command after
the removal that promised to keep them. `install` is the same path.

The rule is therefore a property of the install, not of a verb: claims are off
the allowlist for good, and the claims a render no longer names are named on
every whole-tree apply. A claim leaves by that `kubectl delete pvc`, or by
`nuke --remove-data`, which deletes the namespace and the volumes bound to its
claims (test_nuke.py).
"""

from __future__ import annotations

import inspect
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from wip_deploy.apply import _kubectl_apply_tree, apply_k8s
from wip_deploy.cli import _apply_and_persist_mutation, app

from . import test_case_1229_review2 as t1
from . import test_scoped_k8s_apply as tk8s
from .renderers.test_compose import REPO_ROOT

runner = CliRunner()

# What the cluster holds when the upgrade runs: the core store's claim, and the
# module's claim its own removal left behind and the render no longer names.
LIVE_CLAIMS = "wip-mongodb-data wip-garage-data"
NOTICE = "Applying the whole install (with prune)"


def _stubbed_apply(tmp_path: Path, *, live_claims: str | None = LIVE_CLAIMS) -> list:
    """Everything around the real k8s apply: kubectl stands ready and answers the
    claim listing (or cannot, with None); the rollout and hooks pass."""
    listing = (
        MagicMock(returncode=1, stderr="Error from server (Forbidden): no")
        if live_claims is None
        else MagicMock(returncode=0, stdout=live_claims)
    )
    return [
        patch("wip_deploy.apply.shutil.which", return_value="/usr/bin/kubectl"),
        patch("wip_deploy.apply.kubectl", return_value=["kubectl"]),
        patch("wip_deploy.apply._ensure_self_signed_tls_secret"),
        patch("wip_deploy.apply.subprocess.run", return_value=listing),
    ]


def _run_redeploy(tmp_path: Path) -> tuple[MagicMock, str]:
    """`wip-deploy redeploy --name <install>` over an install whose saved spec
    already lacks the module: the upgrade path the k8s README gives. Returns the
    seam standing at `kubectl apply` and what the command told the operator."""
    install_dir = t1._install_dir(tmp_path, tk8s._k8s_deployment(apps=[]))
    with ExitStack() as stack:
        for p in t1._gates(t1._render_without_module()) + _stubbed_apply(tmp_path):
            stack.enter_context(p)
        kubectl_run = stack.enter_context(patch("wip_deploy.apply._kubectl_run"))
        res = runner.invoke(
            app,
            ["redeploy", "--install-dir", str(install_dir), "--repo-root", str(REPO_ROOT)],
        )
    assert res.exit_code == 0, res.output + repr(res.exception)
    return kubectl_run, res.output


class TestRedeployKeepsClaims:
    def test_the_upgrade_prune_holds_no_volume_claim(self, tmp_path: Path) -> None:
        """The exact `kubectl apply --prune` an upgrade runs: it still prunes the
        workloads a dropped module leaves, and never a claim."""
        kubectl_run, _ = _run_redeploy(tmp_path)

        prune = [c.args[0] for c in kubectl_run.call_args_list if "--prune" in c.args[0]]
        assert len(prune) == 1, kubectl_run.call_args_list
        assert "apps/v1/StatefulSet" in prune[0]
        assert "core/v1/PersistentVolumeClaim" not in prune[0]

    def test_the_upgrade_names_the_claim_the_removal_left_behind(
        self, tmp_path: Path
    ) -> None:
        """Keeping is not enough: the operator learns the claim is still there,
        with the command that takes it — which is now the only way it goes."""
        _, out = _run_redeploy(tmp_path)

        assert "kept volume claim 'wip-garage-data'" in out
        assert "kubectl delete pvc wip-garage-data" in out
        # A claim the render still names is not reported as left behind.
        assert "wip-mongodb-data" not in out


class TestNothingCarriesTheKnob:
    """Retention is not a per-verb opt-in any more, so no call site can leave a
    verb off it by forgetting a flag — the shape of the original bug."""

    def test_apply_takes_no_claim_pruning_flag(self) -> None:
        for fn in (apply_k8s, _kubectl_apply_tree):
            assert "prune_volume_claims" not in inspect.signature(fn).parameters

    def test_a_whole_tree_apply_with_nothing_to_report_says_nothing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A healthy install's apply stays quiet about claims."""
        with ExitStack() as stack:
            for p in _stubbed_apply(tmp_path, live_claims="wip-mongodb-data"):
                stack.enter_context(p)
            apply_k8s(
                deployment=tk8s._k8s_deployment(apps=[]), components=[], apps=[],
                tree=t1._render_without_module(), install_dir=tmp_path,
            )
        assert "volume claim" not in capsys.readouterr().err

    def test_a_claim_listing_that_failed_is_said_as_such(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Not being able to ask is reported as that, never as nothing left
        behind — and never as a failure of the apply, which did run."""
        with ExitStack() as stack:
            for p in _stubbed_apply(tmp_path, live_claims=None):
                stack.enter_context(p)
            apply_k8s(
                deployment=tk8s._k8s_deployment(apps=[]), components=[], apps=[],
                tree=t1._render_without_module(), install_dir=tmp_path,
            )
        err = capsys.readouterr().err
        assert "could not be listed" in err
        assert "No apply deletes a claim" in err


def _lifecycle(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    *,
    scope: list[str] | None,
    removes: bool,
    predates: bool = False,
) -> tuple[dict, str]:
    """The mutation lifecycle over a stubbed apply, for a verb that may or may
    not arrive scoped; returns what the apply got and what was echoed."""
    result = MagicMock(healthy=True, post_install_skipped=[], health_checked=True)
    with ExitStack() as stack:
        for p in [
            patch("wip_deploy.cli._validate_or_exit"),
            patch("wip_deploy.cli._select_cluster_or_exit"),
            patch("wip_deploy.cli._ensure_secrets_via_spec"),
            patch("wip_deploy.cli._refuse_runtime_reservation_collisions"),
            patch("wip_deploy.cli._refuse_attachment_collisions"),
            patch("wip_deploy.cli._render_tree", return_value=tk8s._synthetic_tree({})),
            patch("wip_deploy.cli._k8s_render_predates_front_door", return_value=predates),
            patch("wip_deploy.cli.changed_outside_scope", return_value=[]),
            patch("wip_deploy.cli._persist_deployment"),
        ]:
            stack.enter_context(p)
        apply_k8s_ = stack.enter_context(
            patch("wip_deploy.cli.apply_k8s", return_value=result)
        )
        _apply_and_persist_mutation(
            tk8s._k8s_deployment(apps=[]), [], [], tmp_path, "test",
            services_scope=scope, removes_workloads=removes,
        )
    return apply_k8s_.call_args.kwargs, capsys.readouterr().out


class TestTheWholeInstallIsAnnounced:
    def test_an_unscoped_removal_says_so(self, tmp_path: Path, capsys) -> None:
        """remove-module hands the lifecycle no scope at all: gated on a scope
        being present, the notice never fired and the operator watched a
        whole-install apply with no word of warning."""
        kwargs, out = _lifecycle(tmp_path, capsys, scope=None, removes=True)
        assert "services_scope" not in kwargs
        assert out.count(NOTICE) == 1

    def test_a_scoped_removal_says_it_once(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A removal on an install that predates the front door is already told
        about as a whole-install apply: the removal adds its own notice to that,
        it does not repeat it."""
        kwargs, out = _lifecycle(
            tmp_path, capsys, scope=[], removes=True, predates=True
        )
        assert "services_scope" not in kwargs
        assert "last applied before wip-caddy became the k8s front door" in out
        assert out.count("whole install") == 1

    def test_a_config_only_verb_prints_no_removal_notice(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Nothing is removed, so the whole install stays unapplied and the
        notice stays silent."""
        kwargs, out = _lifecycle(tmp_path, capsys, scope=[], removes=False)
        assert kwargs["services_scope"] == []
        assert NOTICE not in out
