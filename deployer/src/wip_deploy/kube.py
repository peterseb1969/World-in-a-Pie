"""Which cluster a k8s install's kubectl calls reach.

kubectl on its own talks to whatever context the shell points at. An install
records its cluster (`platform.k8s.kube`, a K8sCluster), and every verb
selects it before the first kubectl call: `select` checks that the recorded
context still reaches the recorded cluster and makes it the process's target,
and `kubectl` builds every argv against that target. A kubectl argv built
before a selection is an error, so no call can fall back to the shell's
context.

The kubeconfig reaches kubectl through KUBECONFIG, set in this process's
environment at selection (it may name several files, which `--kubeconfig`
cannot); the context goes on every argv as `--context`. One CLI process acts
on one install, so the selection is process-wide.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from wip_deploy.spec.deployment import K8sCluster

# kube-system exists on every cluster and its UID never changes: the cluster's
# identity, where a server URL is only an address.
_IDENTITY_NAMESPACE = "kube-system"
_TIMEOUT_SECONDS = 30
# A read that does not answer in this long is a dead or unreachable cluster.
QUERY_TIMEOUT_SECONDS = _TIMEOUT_SECONDS
# A delete with --wait waits on finalizers; generous, but never unbounded.
DELETE_TIMEOUT_SECONDS = 600


class KubeError(Exception):
    """The install's cluster cannot be selected; the message says why and
    what to do."""


class _Ambient:
    """Selected for a read-only verb on an install that records no cluster."""


_selected: K8sCluster | _Ambient | None = None
_saved_env: tuple[bool, str | None] | None = None


def kubectl(*args: str) -> list[str]:
    """The argv for one kubectl call against the selected cluster."""
    if _selected is None:
        raise KubeError(
            "internal error: a kubectl call before the install's cluster was "
            "selected (kube.select)"
        )
    if isinstance(_selected, _Ambient):
        return ["kubectl", *args]
    return ["kubectl", "--context", _selected.context, *args]


def resolve_kubeconfig(explicit: str | None = None) -> str:
    """The kubeconfig as kubectl would read it now: `explicit`, else
    $KUBECONFIG, else ~/.kube/config. Each path is made absolute, so the
    record means the same thing from any directory."""
    raw = explicit or os.environ.get("KUBECONFIG") or str(Path.home() / ".kube" / "config")
    parts = [p for p in raw.split(os.pathsep) if p.strip()]
    if not parts:
        raise KubeError(f"empty kubeconfig: {raw!r}")
    return os.pathsep.join(str(Path(p).expanduser().absolute()) for p in parts)


def _run(argv: list[str], kubeconfig: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, check=False,
            env={**os.environ, "KUBECONFIG": kubeconfig}, timeout=_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as e:
        raise KubeError("kubectl not on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise KubeError(
            f"`{' '.join(argv)}` did not answer within {_TIMEOUT_SECONDS} s; "
            "is the cluster reachable?"
        ) from e


def current_context(kubeconfig: str) -> str:
    proc = _run(["kubectl", "config", "current-context"], kubeconfig)
    ctx = proc.stdout.strip()
    if proc.returncode != 0 or not ctx:
        raise KubeError(
            f"no current context in {kubeconfig}: "
            f"{(proc.stderr or '').strip() or 'none set'}. Name one with --kube-context."
        )
    return ctx


def cluster_uid(kubeconfig: str, context: str) -> str:
    """The UID of the cluster `context` reaches, read through `kubeconfig`."""
    proc = _run(
        ["kubectl", "--context", context, "get", "namespace", _IDENTITY_NAMESPACE,
         "-o", "jsonpath={.metadata.uid}"],
        kubeconfig,
    )
    uid = proc.stdout.strip()
    if proc.returncode != 0 or not uid:
        raise KubeError(
            f"could not read the cluster behind context {context!r} "
            f"(kubeconfig {kubeconfig}): {(proc.stderr or '').strip() or 'no answer'}"
        )
    return uid


def record(context: str | None = None, kubeconfig: str | None = None) -> K8sCluster:
    """The cluster `context` (default: the kubeconfig's current one) reaches,
    as an install records it."""
    resolved = resolve_kubeconfig(kubeconfig)
    ctx = context or current_context(resolved)
    return K8sCluster(kubeconfig=resolved, context=ctx, cluster_uid=cluster_uid(resolved, ctx))


def _describe(cluster: K8sCluster) -> str:
    return f"cluster {cluster.cluster_uid} (context {cluster.context!r}, kubeconfig {cluster.kubeconfig})"


def ambient_hint(namespace: str) -> str:
    """Where the shell's kubectl points now, for a refusal or a warning. Best
    effort: an unreadable part is left out, never raised."""
    try:
        kubeconfig = resolve_kubeconfig()
        ctx = current_context(kubeconfig)
    except KubeError:
        return "kubectl has no current context here"
    hint = f"kubectl points at context {ctx!r} (kubeconfig {kubeconfig})"
    try:
        server = _run(
            ["kubectl", "--context", ctx, "config", "view", "--minify",
             "-o", "jsonpath={.clusters[0].cluster.server}"],
            kubeconfig,
        ).stdout.strip()
        if server:
            hint += f", server {server}"
        found = _run(["kubectl", "--context", ctx, "get", "namespace", namespace], kubeconfig)
        hint += (
            f"; namespace {namespace!r} exists there" if found.returncode == 0
            else f"; namespace {namespace!r} does not exist there"
        )
    except KubeError:
        pass
    return hint


def select(cluster: K8sCluster | None, *, namespace: str, mutating: bool) -> str | None:
    """Make `cluster` the target of every later `kubectl` argv in this process.

    Refuses (KubeError) when the recorded context no longer reaches the
    recorded cluster, and when nothing is recorded and the verb changes the
    cluster. A read-only verb on an install that records nothing runs on the
    shell's context; the returned text is the warning to print. Selecting the
    same cluster again is a no-op.
    """
    global _selected
    if cluster is None:
        if mutating:
            raise KubeError(
                "this install records no cluster: it was made before wip-deploy "
                "recorded one, and a verb that changes the cluster could reach "
                f"another one. {ambient_hint(namespace)}. Pin it once with "
                "`wip-deploy redeploy --name <install> --kube-context <context> "
                "[--kubeconfig <path>]` naming the cluster it lives on."
            )
        _selected = _Ambient()
        return (
            "this install records no cluster; reading from the shell's kubectl "
            f"context ({ambient_hint(namespace)}). Pin it with `redeploy --kube-context`."
        )
    if _selected == cluster:
        return None
    try:
        uid = cluster_uid(cluster.kubeconfig, cluster.context)
    except KubeError as e:
        raise KubeError(
            f"this install lives on {_describe(cluster)}, which cannot be reached: "
            f"{e}. If the kubeconfig moved or the context was renamed, re-pin with "
            "`redeploy --kube-context <context> [--kubeconfig <path>]`."
        ) from e
    if uid != cluster.cluster_uid:
        raise KubeError(
            f"this install lives on {_describe(cluster)}, but that context now "
            f"reaches cluster {uid}. Nothing was changed. If the cluster was "
            "rebuilt, re-pin with `redeploy --kube-context <context>`."
        )
    _set_kubeconfig(cluster.kubeconfig)
    _selected = cluster
    return None


def select_shell(namespace: str) -> str:
    """Select the shell's context for a verb given only a namespace, with no
    install on this host to record a cluster. Returns the warning to print."""
    global _selected
    _selected = _Ambient()
    return (
        f"no install on this host records the cluster for namespace {namespace!r}; "
        f"using the shell's kubectl context ({ambient_hint(namespace)})."
    )


def _set_kubeconfig(value: str) -> None:
    global _saved_env
    if _saved_env is None:
        _saved_env = ("KUBECONFIG" in os.environ, os.environ.get("KUBECONFIG"))
    os.environ["KUBECONFIG"] = value


def reset_for_tests() -> None:
    global _selected, _saved_env
    _selected = None
    if _saved_env is not None:
        had, value = _saved_env
        if had and value is not None:
            os.environ["KUBECONFIG"] = value
        else:
            os.environ.pop("KUBECONFIG", None)
        _saved_env = None
