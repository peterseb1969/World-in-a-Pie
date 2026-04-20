"""Apply a rendered tree against the target platform.

Compose target (`apply_compose`):

  1. Write the FileTree under the install directory.
  2. Run `podman-compose up -d` (or docker compose fallback).
  3. Optionally wait for every service with a healthcheck to report
     `healthy` via `podman-compose ps`.
  4. Run post-install hooks via `compose exec`.

K8s target (`apply_k8s`):

  1. Clean stale render output under the install directory (without
     touching the secrets backend dir if it shares the path).
  2. Write the FileTree under the install directory.
  3. `kubectl apply -f <namespace.yaml>` (no prune — Namespace is cluster-
     scoped and pruning could affect peer WIP namespaces).
  4. `kubectl apply -R -f . --prune --selector=app.kubernetes.io/part-of=wip`
     with an explicit allowlist of pruneable kinds (Deployments, Services,
     ConfigMaps, Secrets, PVCs, StatefulSets, Ingresses). Prune removes
     resources from the previous install that aren't in the current render.
  5. Optionally wait for every workload's rollout to finish via
     `kubectl rollout status`.
  6. Run post-install hooks via `kubectl exec` against a ready pod.

Errors surface as ApplyError with a human-readable message; the CLI
translates to a non-zero exit code.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from wip_deploy.renderers.base import FileTree
from wip_deploy.spec import Deployment
from wip_deploy.spec.activation import is_component_active
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component

# ────────────────────────────────────────────────────────────────────


class ApplyError(Exception):
    """Anything that went wrong during apply."""


@dataclass
class ApplyResult:
    install_dir: Path
    services_up: int
    healthy: bool


# ────────────────────────────────────────────────────────────────────
# Public entry
# ────────────────────────────────────────────────────────────────────


def apply_compose(
    *,
    deployment: Deployment,
    components: list[Component],
    apps: list[App],
    tree: FileTree,
    install_dir: Path,
) -> ApplyResult:
    """Materialize the tree and run `podman-compose up -d`.

    Honors `deployment.spec.apply` — wait/timeout/on_timeout.
    """
    install_dir = Path(install_dir)
    tree.write(install_dir)

    compose_cmd = _detect_compose_cmd()
    # Dev target uses local build contexts — always rebuild + recreate
    # so Dockerfile edits and libs/wip-auth changes take effect on every
    # install. Without --build, podman-compose reuses the cached image
    # tag; without --force-recreate, it keeps the existing container
    # even if the image was rebuilt. Both are footguns in dev mode.
    force_build = deployment.spec.target == "dev"
    _run_up(install_dir, compose_cmd, force_build=force_build)

    # Count the services in our rendered file to report a summary.
    up_count = _count_services(tree)

    healthy = True
    if deployment.spec.apply.wait:
        healthy = _wait_healthy(
            install_dir=install_dir,
            compose_cmd=compose_cmd,
            components=components,
            apps=apps,
            deployment=deployment,
        )

    if deployment.spec.apply.wait and not healthy:
        behavior = deployment.spec.apply.on_timeout
        if behavior == "fail":
            raise ApplyError("apply timed out: not all services became healthy")
        # warn/continue: caller decides what to print.

    _run_post_install(install_dir, compose_cmd, components, apps, deployment)

    return ApplyResult(install_dir=install_dir, services_up=up_count, healthy=healthy)


# ────────────────────────────────────────────────────────────────────
# Compose command detection
# ────────────────────────────────────────────────────────────────────


def _detect_compose_cmd() -> list[str]:
    """Prefer podman-compose; fall back to `docker compose`."""
    if shutil.which("podman-compose"):
        return ["podman-compose"]
    if shutil.which("docker"):
        return ["docker", "compose"]
    raise ApplyError(
        "neither podman-compose nor docker is available on PATH"
    )


# ────────────────────────────────────────────────────────────────────
# compose up
# ────────────────────────────────────────────────────────────────────


def _run_up(
    install_dir: Path,
    compose_cmd: list[str],
    *,
    force_build: bool = False,
) -> None:
    cmd = [
        *compose_cmd,
        "--env-file", ".env",
        "-f", "docker-compose.yaml",
        "up", "-d",
    ]
    if force_build:
        cmd.extend(["--build", "--force-recreate"])
    try:
        subprocess.run(
            cmd,
            cwd=install_dir,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise ApplyError(
            f"{compose_cmd[0]} up failed (exit {e.returncode})"
        ) from e


# ────────────────────────────────────────────────────────────────────
# Health wait
# ────────────────────────────────────────────────────────────────────


def _wait_healthy(
    *,
    install_dir: Path,
    compose_cmd: list[str],
    components: list[Component],
    apps: list[App],
    deployment: Deployment,
) -> bool:
    """Poll `compose ps` until every service with a healthcheck reports
    healthy, or the timeout elapses."""
    expected = _services_with_healthchecks(components, apps, deployment)
    if not expected:
        return True

    deadline = time.time() + deployment.spec.apply.timeout_seconds

    while time.time() < deadline:
        statuses = _compose_ps(install_dir, compose_cmd)
        missing = [name for name in expected if name not in statuses]
        unhealthy = [
            name
            for name, state in statuses.items()
            if name in expected and state != "healthy"
        ]
        if not missing and not unhealthy:
            return True
        time.sleep(3)
    return False


def _services_with_healthchecks(
    components: list[Component], apps: list[App], deployment: Deployment
) -> set[str]:
    """Set of container names (`wip-<name>`) we expect to turn healthy."""
    names: set[str] = set()
    enabled_app_names = {a.name for a in deployment.spec.apps if a.enabled}

    for c in components:
        if not is_component_active(c, deployment):
            continue
        if c.spec.healthcheck is not None:
            names.add(f"wip-{c.metadata.name}")

    for a in apps:
        if a.metadata.name not in enabled_app_names:
            continue
        if a.spec.healthcheck is not None:
            names.add(f"wip-{a.metadata.name}")

    return names


def _compose_ps(install_dir: Path, compose_cmd: list[str]) -> dict[str, str]:
    """Return a map of container_name → health status.

    podman-compose and docker compose both support `ps --format json` in
    modern versions, though the JSON shapes differ slightly. We normalize.

    podman-compose ≤1.3.0 does not include a Health field in its JSON
    (only State="running"/"exited"). For those cases we supplement with a
    single `podman inspect` call per running container to fetch
    `State.Health.Status`. Without this, `_wait_healthy` would loop until
    timeout even when every service was actually healthy.
    """
    cmd = [
        *compose_cmd,
        "-f", "docker-compose.yaml",
        "ps", "--format", "json",
    ]
    try:
        result = subprocess.run(
            cmd,
            cwd=install_dir,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        # podman-compose sometimes errors noisily on first call; treat
        # as "no statuses yet" and let the outer loop retry.
        return {}

    statuses = _parse_ps_output(result.stdout)
    _supplement_health_from_inspect(statuses, compose_cmd)
    return statuses


def _supplement_health_from_inspect(
    statuses: dict[str, str], compose_cmd: list[str]
) -> None:
    """Fill in missing health info from `<runtime> inspect`.

    If a container's health is already a recognizable state (healthy /
    unhealthy / starting) we leave it alone. Otherwise we query
    `State.Health.Status` via a single batched `inspect` call. Any
    container that has no healthcheck declared inspect returns an empty
    string — we leave those as-is so callers that pass through
    `_services_with_healthchecks` skip them anyway.
    """
    recognized = {"healthy", "unhealthy", "starting"}
    needs_fill = [name for name, health in statuses.items() if health not in recognized]
    if not needs_fill:
        return

    runtime = _inspect_runtime(compose_cmd)
    if runtime is None:
        return

    # Nil-safe template: when a container has no healthcheck
    # (.State.Health is nil), emit an empty status instead of panicking.
    # Without `{{with}}`, the Go-template engine errors out on
    # `.State.Health.Status` over a nil base, which fails the whole
    # batched command — we saw this in practice on Pi where wip-dex
    # and wip-caddy have no healthchecks.
    template = "{{.Name}}={{with .State.Health}}{{.Status}}{{end}}"

    try:
        result = subprocess.run(
            [runtime, "inspect", "--format", template, *needs_fill],
            check=False,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return

    for line in result.stdout.splitlines():
        line = line.strip()
        if "=" not in line:
            continue
        name, health = line.split("=", 1)
        name = name.lstrip("/")
        health = health.strip()
        if health:
            statuses[name] = health


def _inspect_runtime(compose_cmd: list[str]) -> str | None:
    """Pick the runtime binary matching the compose command.

    podman-compose → podman inspect.
    docker compose → docker inspect.
    Returns None if neither is available on PATH.
    """
    if compose_cmd and compose_cmd[0] == "podman-compose" and shutil.which("podman"):
        return "podman"
    if compose_cmd and compose_cmd[0] == "docker" and shutil.which("docker"):
        return "docker"
    # Last-ditch fallback.
    return shutil.which("podman") or shutil.which("docker")


def _parse_ps_output(stdout: str) -> dict[str, str]:
    """Parse compose-ps output (JSON array or NDJSON) into a health map.

    Fields we care about:
      - Name / Service: the container name
      - Health: one of 'healthy' | 'unhealthy' | 'starting' | 'none' | ''
    """
    stdout = stdout.strip()
    if not stdout:
        return {}

    # Try whole-doc JSON array first (docker compose).
    try:
        items = json.loads(stdout)
        if isinstance(items, dict):
            items = [items]
    except json.JSONDecodeError:
        # NDJSON (podman-compose):
        items = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    out: dict[str, str] = {}
    for item in items:
        name = (
            item.get("Name")
            or item.get("name")
            or item.get("Names")
            or ""
        )
        # podman-compose returns a list in Names sometimes.
        if isinstance(name, list) and name:
            name = name[0]
        if isinstance(name, str) and name.startswith("/"):
            name = name[1:]
        if not name:
            continue

        health = (
            item.get("Health")
            or item.get("health")
            or ""
        )
        state = item.get("State") or item.get("state") or ""
        # Heuristic: if there's no Health field but State is "running",
        # treat as "running" (healthy once probe completes; if no probe
        # exists at all, we shouldn't be polling for it anyway).
        if not health and state:
            health = state

        out[str(name)] = str(health)
    return out


# ────────────────────────────────────────────────────────────────────
# Post-install hooks
# ────────────────────────────────────────────────────────────────────


def _run_post_install(
    install_dir: Path,
    compose_cmd: list[str],
    components: list[Component],
    apps: list[App],
    deployment: Deployment,
) -> None:
    """Run every active component's post-install hooks. Hooks run inside
    their owning container via `compose exec`."""
    enabled_app_names = {a.name for a in deployment.spec.apps if a.enabled}

    owners: list[tuple[str, list]] = []  # type: ignore[type-arg]
    for c in components:
        if is_component_active(c, deployment) and c.spec.post_install:
            owners.append((c.metadata.name, c.spec.post_install))
    for a in apps:
        if a.metadata.name in enabled_app_names and a.spec.post_install:
            owners.append((a.metadata.name, a.spec.post_install))

    for owner_name, hooks in owners:
        for hook in hooks:
            cmd = [
                *compose_cmd,
                "-f", "docker-compose.yaml",
                "exec", "-T", owner_name,
                "sh", "-c", hook.run,
            ]
            try:
                subprocess.run(cmd, cwd=install_dir, check=True)
            except subprocess.CalledProcessError as e:
                raise ApplyError(
                    f"post-install hook {hook.name!r} on {owner_name!r} "
                    f"failed (exit {e.returncode})"
                ) from e


# ────────────────────────────────────────────────────────────────────
# Misc
# ────────────────────────────────────────────────────────────────────


def _count_services(tree: FileTree) -> int:
    """Count services in the rendered docker-compose.yaml."""
    compose = tree.files.get(Path("docker-compose.yaml"))
    if compose is None:
        return 0
    import yaml as _yaml

    try:
        data = _yaml.safe_load(compose.content)
    except _yaml.YAMLError:
        return 0
    if not isinstance(data, dict):
        return 0
    services = data.get("services")
    if not isinstance(services, dict):
        return 0
    return len(services)


# ════════════════════════════════════════════════════════════════════
# K8s apply
# ════════════════════════════════════════════════════════════════════


def apply_k8s(
    *,
    deployment: Deployment,
    components: list[Component],
    apps: list[App],
    tree: FileTree,
    install_dir: Path,
) -> ApplyResult:
    """Materialize the tree and run `kubectl apply`.

    Honors `deployment.spec.apply` — wait/timeout/on_timeout. Wait uses
    `kubectl rollout status` per Deployment/StatefulSet. Post-install
    hooks run via `kubectl exec` against a pod selected by the
    component's `app.kubernetes.io/name` label.
    """
    if not shutil.which("kubectl"):
        raise ApplyError("kubectl not on PATH")

    k8s = deployment.spec.platform.k8s
    if k8s is None:
        raise ApplyError("k8s target requires platform.k8s")
    ns = k8s.namespace

    install_dir = Path(install_dir)
    _clean_rendered_tree(install_dir)
    tree.write(install_dir)

    _kubectl_apply_tree(install_dir, ns)

    up_count = _count_k8s_workloads(tree)

    healthy = True
    if deployment.spec.apply.wait:
        healthy = _wait_k8s_rollout(
            install_dir=install_dir,
            ns=ns,
            components=components,
            apps=apps,
            deployment=deployment,
        )

    if deployment.spec.apply.wait and not healthy:
        behavior = deployment.spec.apply.on_timeout
        if behavior == "fail":
            raise ApplyError("apply timed out: not all workloads became ready")

    _run_post_install_k8s(ns, components, apps, deployment)

    return ApplyResult(install_dir=install_dir, services_up=up_count, healthy=healthy)


# ────────────────────────────────────────────────────────────────────
# kubectl apply
# ────────────────────────────────────────────────────────────────────


_PRUNE_ALLOWLIST = (
    "core/v1/Secret",
    "core/v1/ConfigMap",
    "core/v1/Service",
    "core/v1/PersistentVolumeClaim",
    "apps/v1/Deployment",
    "apps/v1/StatefulSet",
    "networking.k8s.io/v1/Ingress",
)


def _clean_rendered_tree(install_dir: Path) -> None:
    """Delete prior render outputs so orphans don't get re-applied.

    Scope: top-level `*.yaml` files and the `services/` + `infrastructure/`
    subdirs — exactly what the k8s renderer writes. Deliberately avoids
    touching anything else (notably a `secrets/` backend directory that
    may share the install_dir).
    """
    if not install_dir.exists():
        return
    for yaml_file in install_dir.glob("*.yaml"):
        yaml_file.unlink()
    for name in ("services", "infrastructure"):
        sub = install_dir / name
        if sub.is_dir():
            shutil.rmtree(sub)


def _kubectl_apply_tree(install_dir: Path, ns: str) -> None:
    """Apply the rendered tree with prune.

    Namespace applied first without prune (cluster-scoped — pruning could
    delete a peer namespace sharing the same label). Then the full tree
    applied with `--prune --selector=app.kubernetes.io/part-of=wip` scoped
    to an explicit allowlist of kinds. The second apply re-targets the
    namespace too (no-op update), but prune ignores it because Namespace
    is not in the allowlist.
    """
    namespace_yaml = install_dir / "namespace.yaml"
    if namespace_yaml.exists():
        _kubectl_run(["apply", "-f", str(namespace_yaml)])

    args = [
        "apply",
        "-n", ns,
        "-R", "-f", str(install_dir),
        "--prune",
        "--selector=app.kubernetes.io/part-of=wip",
    ]
    for kind in _PRUNE_ALLOWLIST:
        args.extend(["--prune-allowlist", kind])
    _kubectl_run(args)


def _kubectl_run(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Shell out to kubectl and raise ApplyError on non-zero exit."""
    try:
        return subprocess.run(
            ["kubectl", *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        raise ApplyError(
            f"kubectl {' '.join(args)} failed (exit {e.returncode}): {stderr}"
        ) from e


# ────────────────────────────────────────────────────────────────────
# Rollout wait
# ────────────────────────────────────────────────────────────────────


def _wait_k8s_rollout(
    *,
    install_dir: Path,
    ns: str,
    components: list[Component],
    apps: list[App],
    deployment: Deployment,
) -> bool:
    """Poll every active workload via `kubectl rollout status`.

    Returns True if all rollouts complete within the total timeout,
    False on first timeout. Respects `spec.apply.timeout_seconds` as a
    *total* budget across all workloads, not per-workload.
    """
    workloads = _expected_workloads(components, apps, deployment)
    if not workloads:
        return True

    deadline = time.time() + deployment.spec.apply.timeout_seconds
    for kind, name in workloads:
        remaining = int(deadline - time.time())
        if remaining <= 0:
            return False
        try:
            subprocess.run(
                [
                    "kubectl", "rollout", "status",
                    "-n", ns,
                    f"{kind.lower()}/{name}",
                    f"--timeout={remaining}s",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError:
            return False
    return True


def _expected_workloads(
    components: list[Component], apps: list[App], deployment: Deployment
) -> list[tuple[str, str]]:
    """List of `(kind, name)` pairs for every active workload.

    Kind is StatefulSet when the component has storage, else Deployment
    — matches the rendering logic in `renderers/k8s.py`.
    """
    out: list[tuple[str, str]] = []
    enabled_app_names = {a.name for a in deployment.spec.apps if a.enabled}

    for c in components:
        if not is_component_active(c, deployment):
            continue
        kind = "StatefulSet" if c.spec.storage else "Deployment"
        out.append((kind, f"wip-{c.metadata.name}"))

    for a in apps:
        if a.metadata.name not in enabled_app_names:
            continue
        kind = "StatefulSet" if a.spec.storage else "Deployment"
        out.append((kind, f"wip-{a.metadata.name}"))

    return out


# ────────────────────────────────────────────────────────────────────
# Post-install hooks (k8s)
# ────────────────────────────────────────────────────────────────────


def _run_post_install_k8s(
    ns: str,
    components: list[Component],
    apps: list[App],
    deployment: Deployment,
) -> None:
    """Run every active component's post-install hooks inside a running
    pod via `kubectl exec`. Pod is selected by the
    `app.kubernetes.io/name` label."""
    enabled_app_names = {a.name for a in deployment.spec.apps if a.enabled}

    owners: list[tuple[str, list]] = []  # type: ignore[type-arg]
    for c in components:
        if is_component_active(c, deployment) and c.spec.post_install:
            owners.append((c.metadata.name, c.spec.post_install))
    for a in apps:
        if a.metadata.name in enabled_app_names and a.spec.post_install:
            owners.append((a.metadata.name, a.spec.post_install))

    for owner_name, hooks in owners:
        pod = _pod_for_component(ns, owner_name)
        for hook in hooks:
            try:
                subprocess.run(
                    [
                        "kubectl", "exec", "-n", ns, pod,
                        "--", "sh", "-c", hook.run,
                    ],
                    check=True,
                )
            except subprocess.CalledProcessError as e:
                raise ApplyError(
                    f"post-install hook {hook.name!r} on {owner_name!r} "
                    f"failed (exit {e.returncode})"
                ) from e


def _pod_for_component(ns: str, component_name: str) -> str:
    """Return the name of a Running pod for the given component.

    Selects via the `app.kubernetes.io/name=<component>` label set by
    the k8s renderer. Raises ApplyError if no running pod is found."""
    try:
        result = subprocess.run(
            [
                "kubectl", "get", "pod",
                "-n", ns,
                "-l", f"app.kubernetes.io/name={component_name}",
                "--field-selector=status.phase=Running",
                "-o", "jsonpath={.items[0].metadata.name}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        raise ApplyError(
            f"could not find pod for component {component_name!r} in ns "
            f"{ns!r}: {stderr}"
        ) from e

    pod = result.stdout.strip()
    if not pod:
        raise ApplyError(
            f"no running pod found for component {component_name!r} in ns {ns!r}"
        )
    return pod


# ────────────────────────────────────────────────────────────────────
# K8s misc
# ────────────────────────────────────────────────────────────────────


def _count_k8s_workloads(tree: FileTree) -> int:
    """Count Deployments + StatefulSets in the rendered tree.

    Used only for the summary line in the CLI — a rough proxy for "how
    many services did we bring up". Ignores Namespaces/Secrets/etc.
    """
    import yaml as _yaml

    count = 0
    for rel, entry in tree.files.items():
        if rel.name in ("namespace.yaml", "secrets.yaml", "configmaps.yaml", "ingress.yaml"):
            continue
        try:
            for doc in _yaml.safe_load_all(entry.content):
                if isinstance(doc, dict) and doc.get("kind") in ("Deployment", "StatefulSet"):
                    count += 1
        except _yaml.YAMLError:
            continue
    return count
