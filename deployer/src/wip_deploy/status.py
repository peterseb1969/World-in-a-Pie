"""Read the live deployment state and print a compact table.

Equivalent of `podman ps` / `kubectl get pods` scoped to this deployment
and named the deployment's own components. Read-only: never mutates state.

Auto-detection rule:
  - If <install_dir>/docker-compose.yaml exists → compose (or dev) target
  - Else if --namespace is passed → k8s target
  - Else: error with hint

The compose path reuses the helpers already in apply.py for ps-output
parsing so one change to the JSON-shape normalization covers both apply
and status.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from wip_deploy.apply import ApplyError, _compose_ps, _detect_compose_cmd

# ────────────────────────────────────────────────────────────────────


class StatusError(Exception):
    """Anything that went wrong reading status."""


@dataclass(frozen=True)
class ServiceStatus:
    """One row of the status table."""

    name: str  # e.g. "wip-registry" (compose) or "wip-registry-abc" (k8s pod)
    state: str  # "running" | "exited" | "pending" | etc.
    health: str  # "healthy" | "unhealthy" | "starting" | "" (no probe)


# ────────────────────────────────────────────────────────────────────
# Compose
# ────────────────────────────────────────────────────────────────────


def read_compose_status(install_dir: Path) -> list[ServiceStatus]:
    """Run `compose ps` in the install dir and return a normalized table."""
    compose_yaml = install_dir / "docker-compose.yaml"
    if not compose_yaml.exists():
        raise StatusError(
            f"no docker-compose.yaml under {install_dir} — not a compose install"
        )

    try:
        compose_cmd = _detect_compose_cmd()
    except ApplyError as e:
        raise StatusError(str(e)) from e

    health_map = _compose_ps(install_dir, compose_cmd)
    rows: list[ServiceStatus] = []
    for name, health in sorted(health_map.items()):
        # `_compose_ps` folds state into health when there's no probe.
        # Re-split here so the table reads sensibly.
        if health in ("healthy", "unhealthy", "starting"):
            state = "running"
            hc = health
        else:
            state = health or "unknown"
            hc = ""
        rows.append(ServiceStatus(name=name, state=state, health=hc))
    return rows


# ────────────────────────────────────────────────────────────────────
# K8s
# ────────────────────────────────────────────────────────────────────


def read_k8s_status(namespace: str) -> list[ServiceStatus]:
    """Run `kubectl get pods -n <ns> -o json` and return a normalized table."""
    if not shutil.which("kubectl"):
        raise StatusError("kubectl not on PATH")

    try:
        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", namespace, "-o", "json"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip() if e.stderr else ""
        raise StatusError(
            f"kubectl get pods failed (exit {e.returncode}): {stderr}"
        ) from e

    try:
        doc = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise StatusError(f"kubectl output not JSON: {e}") from e

    items = doc.get("items", [])
    rows: list[ServiceStatus] = []
    for item in items:
        meta = item.get("metadata", {})
        name = meta.get("name", "")
        state = item.get("status", {}).get("phase", "").lower() or "unknown"
        # Ready condition tells us if pod is actually serving.
        hc = ""
        for cond in item.get("status", {}).get("conditions", []):
            if cond.get("type") == "Ready":
                hc = "healthy" if cond.get("status") == "True" else "not ready"
                break
        rows.append(ServiceStatus(name=name, state=state, health=hc))
    rows.sort(key=lambda r: r.name)
    return rows


# ────────────────────────────────────────────────────────────────────
# Table formatting
# ────────────────────────────────────────────────────────────────────


def format_table(rows: list[ServiceStatus]) -> str:
    """Render as a plain-text table. Empty list → friendly "(none)" line."""
    if not rows:
        return "(no services found)"

    headers = ("NAME", "STATE", "HEALTH")
    cols = [
        max(len(headers[0]), max(len(r.name) for r in rows)),
        max(len(headers[1]), max(len(r.state) for r in rows)),
        max(len(headers[2]), max(len(r.health or "—") for r in rows)),
    ]

    def _row(parts: tuple[str, str, str]) -> str:
        return "  ".join(p.ljust(cols[i]) for i, p in enumerate(parts))

    lines = [_row(headers)]
    for r in rows:
        lines.append(_row((r.name, r.state, r.health or "—")))
    return "\n".join(lines)
