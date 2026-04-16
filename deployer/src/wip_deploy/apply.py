"""Apply a rendered tree against the target platform.

For the compose target this is a small shell-out layer:

  1. Write the FileTree under the install directory.
  2. Run `podman-compose up -d` (or docker compose fallback).
  3. Optionally wait for every service with a healthcheck to report
     `healthy` via `podman-compose ps`.
  4. Run post-install hooks.

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
    _run_up(install_dir, compose_cmd)

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


def _run_up(install_dir: Path, compose_cmd: list[str]) -> None:
    cmd = [
        *compose_cmd,
        "--env-file", ".env",
        "-f", "docker-compose.yaml",
        "up", "-d",
    ]
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

    return _parse_ps_output(result.stdout)


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
