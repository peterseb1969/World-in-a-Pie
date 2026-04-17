"""Pre-install checks: catch predictable problems before `apply_*` runs.

Port bindings and stale containers are the two most common sources of
"why did compose up error-halt on recipe line 3?" confusion. This module
surfaces them with clear messages before any work happens.

Kept intentionally narrow — this is a safety net, not a linter. A missing
runtime dependency (podman/docker) surfaces from `_detect_compose_cmd`
at apply time; duplicating that check here would only add noise.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

# ────────────────────────────────────────────────────────────────────


class PreflightError(Exception):
    """A pre-install check failed — abort before any work happens."""


@dataclass(frozen=True)
class PreflightWarning:
    """Non-fatal issue the user should see but can acknowledge through."""

    message: str


# ────────────────────────────────────────────────────────────────────
# Port binding
# ────────────────────────────────────────────────────────────────────


def check_port_free(port: int, *, host: str = "0.0.0.0") -> None:
    """Raise PreflightError if a TCP listener is already bound to `port`.

    Uses SO_REUSEADDR when probing so we don't leave TIME_WAIT sockets
    behind on repeated runs. If bind() succeeds, the port is free; we
    close the probe socket immediately.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError as e:
        raise PreflightError(
            f"port {port} is already in use on {host}. Free it (stop the "
            f"other service, or run `wip-deploy nuke` to clean up a prior "
            f"WIP install) and try again."
        ) from e
    finally:
        sock.close()


def check_ports_free(ports: list[int], *, host: str = "0.0.0.0") -> None:
    """Probe every port. Raises on the first conflict — if there are
    multiple conflicts the user fixes one and retries."""
    for p in ports:
        check_port_free(p, host=host)


# ────────────────────────────────────────────────────────────────────
# Stale containers
# ────────────────────────────────────────────────────────────────────


def check_no_stale_containers(install_dir: Path) -> list[PreflightWarning]:
    """Warn if `wip-*` containers exist on the host but `install_dir` has
    no compose file yet.

    Interpretation: the user is starting fresh, but a previous install
    left containers that will fight with this one for names like
    `wip-caddy`. `podman-compose up` would fail opaquely.

    Not fatal — some users deliberately import leftover state. Emit a
    warning so they see it; let them decide.
    """
    warnings: list[PreflightWarning] = []
    compose_yaml = install_dir / "docker-compose.yaml"
    if compose_yaml.exists():
        return warnings  # reinstall path — containers should exist

    runtime = _container_runtime()
    if runtime is None:
        return warnings  # can't check without a runtime

    try:
        result = subprocess.run(
            [runtime, "ps", "-a", "--filter", "name=wip-", "--format", "{{.Names}}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return warnings  # runtime error — fall through to apply which will surface it

    names = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
    if names:
        n = len(names)
        sample = ", ".join(sorted(names)[:5])
        more = f" (and {n - 5} more)" if n > 5 else ""
        warnings.append(PreflightWarning(
            f"found {n} stray wip-* container(s) from a prior install: "
            f"{sample}{more}. Run `wip-deploy nuke --purge-all` to clean "
            f"up, or `wip-deploy install --install-dir <existing-dir>` to "
            f"reuse the prior state."
        ))
    return warnings


def _container_runtime() -> str | None:
    """Prefer podman; fall back to docker. None if neither is installed."""
    if shutil.which("podman"):
        return "podman"
    if shutil.which("docker"):
        return "docker"
    return None
