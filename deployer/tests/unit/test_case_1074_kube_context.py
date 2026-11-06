"""A k8s install's verbs act on the cluster it lives on, or refuse (CASE-1074).

kubectl on its own talks to whatever context the shell points at, so a
redeploy run without the install's KUBECONFIG once applied a whole install
to another cluster. Now:

- `install` records the cluster: kubeconfig, context and the kube-system UID.
- Every verb selects the recorded cluster before its first kubectl call and
  refuses when the context reaches another cluster; every kubectl argv
  carries the recorded context and KUBECONFIG.
- An install made before the record refuses verbs that change the cluster
  until `redeploy --kube-context` pins it; read-only verbs warn and use the
  shell's context.

These tests put a fake kubectl on PATH, so the real selection code runs.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from wip_deploy import kube
from wip_deploy.cli import (
    _load_deployment,
    _persist_deployment,
    _record_cluster_for_install,
    app,
)
from wip_deploy.renderers.base import FileTree
from wip_deploy.spec.deployment import K8sCluster

from .renderers.test_compose import REPO_ROOT, _minimal_compose
from .renderers.test_k8s import _k8s_deployment

pytestmark = pytest.mark.fake_kubectl_binary
runner = CliRunner()

_FAKE_KUBECTL = '''#!{python}
"""Fake kubectl: a kubeconfig is JSON {{"current": ctx, "contexts": {{ctx: uid}}}}."""
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_KUBECTL_LOG"], "a") as log:
    log.write(json.dumps({{"argv": args, "kubeconfig": os.environ.get("KUBECONFIG")}}) + "\\n")
config = {{"current": None, "contexts": {{}}}}
for path in reversed((os.environ.get("KUBECONFIG") or "").split(os.pathsep)):
    if path and os.path.exists(path):
        loaded = json.load(open(path))
        config["contexts"].update(loaded.get("contexts", {{}}))
        config["current"] = loaded.get("current") or config["current"]
ctx = config["current"]
if "--context" in args:
    i = args.index("--context")
    ctx = args[i + 1]
    args = args[:i] + args[i + 2:]
if args[:2] == ["config", "current-context"]:
    if not config["current"]:
        sys.exit("error: current-context is not set")
    print(config["current"])
    sys.exit(0)
if ctx not in config["contexts"]:
    sys.exit(f'error: context "{{ctx}}" does not exist')
if args[:2] == ["config", "view"]:
    print(f"https://{{ctx}}.example:6443")
elif args[:3] == ["get", "namespace", "kube-system"]:
    print(config["contexts"][ctx], end="")
elif args[:2] == ["get", "namespace"]:
    sys.exit("Error from server (NotFound)")
'''


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Two clusters, each in its own kubeconfig: `a` (uid-a, the shell's
    default) and `b` (uid-b). Returns a small handle."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "kubectl"
    exe.write_text(_FAKE_KUBECTL.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "kubectl.log"
    log.touch()
    cfg_a = tmp_path / "a.json"
    cfg_b = tmp_path / "b.json"
    cfg_a.write_text(json.dumps({"current": "a", "contexts": {"a": "uid-a"}}))
    cfg_b.write_text(json.dumps({"current": "b", "contexts": {"b": "uid-b"}}))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_KUBECTL_LOG", str(log))
    monkeypatch.setenv("KUBECONFIG", str(cfg_a))

    class Fake:
        a, b = cfg_a, cfg_b

        @staticmethod
        def calls() -> list[dict]:
            return [json.loads(line) for line in log.read_text().splitlines()]

        @staticmethod
        def rebuild(cfg: Path, ctx: str, uid: str) -> None:
            data = json.loads(cfg.read_text())
            data["contexts"][ctx] = uid
            cfg.write_text(json.dumps(data))

    yield Fake
    kube.reset_for_tests()


# ── The record and the selection ──────────────────────────────────────


def test_record_names_the_context_the_kubeconfig_and_the_cluster(fake) -> None:  # type: ignore[no-untyped-def]
    rec = kube.record()
    assert (rec.context, rec.cluster_uid, rec.kubeconfig) == ("a", "uid-a", str(fake.a))
    other = kube.record("b", str(fake.b))
    assert (other.context, other.cluster_uid, other.kubeconfig) == ("b", "uid-b", str(fake.b))


def test_no_kubectl_argv_before_a_selection(fake) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(kube.KubeError, match="before the install's cluster was selected"):
        kube.kubectl("apply")


def test_a_selected_cluster_is_reached_whatever_the_shell_points_at(fake) -> None:  # type: ignore[no-untyped-def]
    on_b = kube.record("b", str(fake.b))
    assert os.environ["KUBECONFIG"] == str(fake.a)  # the shell points at cluster a
    assert kube.select(on_b, namespace="wip", mutating=True) is None
    assert kube.kubectl("apply", "-f", "x") == ["kubectl", "--context", "b", "apply", "-f", "x"]
    assert os.environ["KUBECONFIG"] == str(fake.b)
    kube.reset_for_tests()
    assert os.environ["KUBECONFIG"] == str(fake.a)  # restored


def test_a_context_that_reaches_another_cluster_is_refused(fake) -> None:  # type: ignore[no-untyped-def]
    on_b = kube.record("b", str(fake.b))
    fake.rebuild(fake.b, "b", "uid-rebuilt")
    with pytest.raises(kube.KubeError) as e:
        kube.select(on_b, namespace="wip", mutating=True)
    assert "uid-b" in str(e.value) and "uid-rebuilt" in str(e.value)
    with pytest.raises(kube.KubeError, match="before the install's cluster was selected"):
        kube.kubectl("apply")  # nothing was selected


def test_a_missing_context_is_refused_with_the_remedy(fake) -> None:  # type: ignore[no-untyped-def]
    gone = K8sCluster(kubeconfig=str(fake.a), context="b", cluster_uid="uid-b")
    with pytest.raises(kube.KubeError, match="cannot be reached.*--kube-context"):
        kube.select(gone, namespace="wip", mutating=True)


def test_without_a_record_a_change_is_refused_and_a_read_warns(fake) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(kube.KubeError) as e:
        kube.select(None, namespace="wip", mutating=True)
    assert "records no cluster" in str(e.value)
    assert "context 'a'" in str(e.value) and "--kube-context" in str(e.value)
    warning = kube.select(None, namespace="wip", mutating=False)
    assert warning and "records no cluster" in warning
    assert kube.kubectl("get", "pods") == ["kubectl", "get", "pods"]


def test_the_record_survives_a_persist(fake, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    d = _k8s_deployment()
    d.spec.platform.k8s.kube = kube.record("b", str(fake.b))
    _persist_deployment(d, tmp_path / "inst", repo_root=REPO_ROOT)
    assert _load_deployment(tmp_path / "inst").spec.platform.k8s.kube == d.spec.platform.k8s.kube


def test_no_kubectl_argv_is_built_outside_the_kube_module() -> None:
    src = Path(__file__).resolve().parents[2] / "src" / "wip_deploy"
    offenders = [
        f"{path.relative_to(src)}:{n}"
        for path in sorted(src.rglob("*.py"))
        if path.name != "kube.py"
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if re.search(r"""["']kubectl["']\s*[,\]]""", line)
    ]
    assert offenders == [], f"build kubectl argv with wip_deploy.kube.kubectl: {offenders}"


# ── install ───────────────────────────────────────────────────────────


def test_a_new_install_records_the_shells_cluster(fake) -> None:  # type: ignore[no-untyped-def]
    d = _k8s_deployment()
    _record_cluster_for_install(d, None)
    assert d.spec.platform.k8s.kube == K8sCluster(
        kubeconfig=str(fake.a), context="a", cluster_uid="uid-a",
    )


def test_an_install_over_an_existing_one_keeps_its_cluster(fake) -> None:  # type: ignore[no-untyped-def]
    previous = _k8s_deployment()
    previous.spec.platform.k8s.kube = kube.record("b", str(fake.b))
    d = _k8s_deployment()
    _record_cluster_for_install(d, previous)
    assert d.spec.platform.k8s.kube == previous.spec.platform.k8s.kube
    unpinned = _k8s_deployment()
    _record_cluster_for_install(d, unpinned)
    assert d.spec.platform.k8s.kube is None  # the selection then refuses


# ── The verbs ─────────────────────────────────────────────────────────


def _saved(tmp_path: Path, cluster: K8sCluster | None) -> Path:
    d = _k8s_deployment()
    d.spec.platform.k8s.kube = cluster
    target = tmp_path / "inst"
    _persist_deployment(d, target, repo_root=REPO_ROOT)
    return target


def _redeploy(target: Path, *extra: str):  # type: ignore[no-untyped-def]
    seen: dict = {}

    def fake_apply(**kwargs):  # type: ignore[no-untyped-def]
        seen["argv"] = kube.kubectl("apply")
        seen["kubeconfig"] = os.environ.get("KUBECONFIG")
        return MagicMock(healthy=True, post_install_skipped=[])

    with (
        patch("wip_deploy.cli._validate_or_exit"),
        patch("wip_deploy.cli._ensure_secrets_via_spec", return_value=MagicMock()),
        patch("wip_deploy.cli._refuse_runtime_reservation_collisions"),
        patch("wip_deploy.cli._refuse_attachment_collisions"),
        patch("wip_deploy.cli._render_tree", return_value=FileTree()),
        patch("wip_deploy.cli._persist_deployment") as persist,
        patch("wip_deploy.cli.apply_k8s", side_effect=fake_apply) as apply,
    ):
        result = runner.invoke(app, ["redeploy", "--install-dir", str(target), *extra])
    return result, apply, persist, seen


def test_redeploy_of_an_unpinned_install_refuses_before_any_apply(fake, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    result, apply, _, _ = _redeploy(_saved(tmp_path, None))
    assert result.exit_code == 2, result.output
    assert "records no cluster" in result.output
    assert not apply.called
    assert not [c for c in fake.calls() if c["argv"][:1] in (["apply"], ["create"])]


def test_redeploy_kube_context_pins_and_applies_there(fake, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    result, apply, persist, seen = _redeploy(
        _saved(tmp_path, None), "--kube-context", "b", "--kubeconfig", str(fake.b),
    )
    assert result.exit_code == 0, result.output
    assert seen == {"argv": ["kubectl", "--context", "b", "apply"], "kubeconfig": str(fake.b)}
    saved = persist.call_args.args[0].spec.platform.k8s.kube
    assert saved == K8sCluster(kubeconfig=str(fake.b), context="b", cluster_uid="uid-b")


def test_a_pinned_install_ignores_where_the_shell_points(fake, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    target = _saved(tmp_path, kube.record("b", str(fake.b)))
    assert os.environ["KUBECONFIG"] == str(fake.a)
    result, apply, _, seen = _redeploy(target)
    assert result.exit_code == 0, result.output
    assert seen == {"argv": ["kubectl", "--context", "b", "apply"], "kubeconfig": str(fake.b)}


def test_a_pinned_install_whose_cluster_changed_refuses(fake, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    target = _saved(tmp_path, kube.record("b", str(fake.b)))
    fake.rebuild(fake.b, "b", "uid-other")
    result, apply, _, _ = _redeploy(target)
    assert result.exit_code == 2, result.output
    assert "uid-b" in result.output and "uid-other" in result.output
    assert not apply.called


def test_kube_context_is_refused_off_k8s_and_kubeconfig_needs_it(fake, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    target = _saved(tmp_path, None)
    alone = runner.invoke(app, ["redeploy", "--install-dir", str(target), "--kubeconfig", str(fake.b)])
    assert alone.exit_code == 2 and "--kubeconfig goes with --kube-context" in alone.output
    compose = tmp_path / "compose"
    _persist_deployment(_minimal_compose(), compose, repo_root=REPO_ROOT)
    off = runner.invoke(app, ["redeploy", "--install-dir", str(compose), "--kube-context", "a"])
    assert off.exit_code == 2 and "k8s installs only" in off.output


def test_stop_of_an_unpinned_install_refuses(fake, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    target = _saved(tmp_path, None)
    with patch("wip_deploy.apply.stop_install") as stop:
        result = runner.invoke(app, ["stop", "--install-dir", str(target)])
    assert result.exit_code == 2, result.output
    assert not stop.called


def test_status_of_an_unpinned_install_reads_with_a_warning(fake, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    target = _saved(tmp_path, None)
    with patch("wip_deploy.status.read_k8s_status", return_value=[]) as read:
        result = runner.invoke(app, ["status", "--install-dir", str(target)])
    assert read.called
    assert "records no cluster" in result.output


def test_nuke_of_an_unpinned_install_refuses_before_asking(fake, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    target = _saved(tmp_path, None)
    with patch("wip_deploy.nuke.nuke_k8s_install_dir") as nuke:
        result = runner.invoke(app, ["nuke", "--install-dir", str(target), "--yes"])
    assert result.exit_code == 2, result.output
    assert "records no cluster" in result.output
    assert not nuke.called
