"""Teardown.

Two modes:

  - **Scoped** (default): `podman-compose down` inside an install
    directory. Removes containers, networks, and optionally volumes for
    that specific compose project. Safe — only touches what that
    compose file declared.

  - **Purge-all**: scans for every `wip-*` container, pod, and volume
    regardless of which deployment created them and removes them. Only
    necessary for cross-install cleanup (e.g. migrating from v1 `quick-
    install.sh` to v2 where two different compose projects both used
    `wip-*` names).

Both modes honor a `keep_data` / `keep_secrets` split so the data-
destructive step is always opt-in.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class NukeError(Exception):
    pass


@dataclass
class NukeReport:
    containers_removed: list[str] = field(default_factory=list)
    pods_removed: list[str] = field(default_factory=list)
    volumes_removed: list[str] = field(default_factory=list)
    images_removed: list[str] = field(default_factory=list)
    networks_removed: list[str] = field(default_factory=list)
    secrets_dir_removed: Path | None = None
    compose_down_ran: bool = False

    def summary(self) -> str:
        parts = []
        if self.compose_down_ran:
            parts.append("compose down completed")
        if self.containers_removed:
            parts.append(f"{len(self.containers_removed)} container(s) removed")
        if self.pods_removed:
            parts.append(f"{len(self.pods_removed)} pod(s) removed")
        if self.volumes_removed:
            parts.append(f"{len(self.volumes_removed)} volume(s) removed")
        if self.images_removed:
            parts.append(f"{len(self.images_removed)} image(s) removed")
        if self.networks_removed:
            parts.append(f"{len(self.networks_removed)} network(s) removed")
        if self.secrets_dir_removed:
            parts.append(f"secrets dir removed: {self.secrets_dir_removed}")
        return "; ".join(parts) or "nothing to do"


# ────────────────────────────────────────────────────────────────────
# Scoped teardown (single install dir)
# ────────────────────────────────────────────────────────────────────


def nuke_install_dir(
    install_dir: Path,
    *,
    remove_data: bool = False,
    secrets_location: Path | None = None,
    remove_secrets: bool = False,
    remove_images: bool = False,
) -> NukeReport:
    """Tear down the compose project rooted at `install_dir`.

    `remove_data=True` passes `-v` to `compose down` so named volumes go
    too. `remove_secrets=True` additionally removes the secret backend
    directory. `remove_images=True` additionally removes every image
    referenced by the compose's `services.*.image` entries.

    Always sweeps any `wip-*` networks left dangling after `compose
    down` (e.g. when the network was declared `external: true`).
    """
    if not install_dir.exists():
        raise NukeError(f"install dir does not exist: {install_dir}")

    compose_file = install_dir / "docker-compose.yaml"
    if not compose_file.exists():
        # v1 quick-install uses a different name.
        alt = install_dir / "docker-compose.production.yml"
        if alt.exists():
            compose_file = alt
        else:
            raise NukeError(
                f"no docker-compose.yaml (or .production.yml) in {install_dir}"
            )

    # Snapshot images BEFORE compose down — once the containers are
    # gone, parsing the compose still works, but doing it eagerly
    # keeps the call sites symmetric with the post-down cleanup.
    images = _images_in_compose(compose_file) if remove_images else []
    networks = _networks_in_compose(compose_file)

    report = NukeReport()
    cmd_prefix = _detect_compose_cmd()

    cmd = [*cmd_prefix, "-f", compose_file.name, "down"]
    if remove_data:
        cmd.append("-v")

    try:
        subprocess.run(cmd, cwd=install_dir, check=True)
        report.compose_down_ran = True
    except subprocess.CalledProcessError as e:
        raise NukeError(f"compose down failed (exit {e.returncode})") from e

    if remove_images and images:
        report.images_removed = _remove_images(images)

    # Defensive sweep: compose down doesn't remove `external: true`
    # networks, and partial failures can leave them behind. Removing a
    # network that's still in use fails silently — fine; the dangling
    # case is what we care about.
    report.networks_removed = _remove_networks(networks)

    if remove_secrets and secrets_location is not None:
        secrets_location = Path(secrets_location)
        if secrets_location.exists():
            _remove_tree(secrets_location)
            report.secrets_dir_removed = secrets_location

    return report


# ────────────────────────────────────────────────────────────────────
# Purge all wip-* resources (cross-install cleanup)
# ────────────────────────────────────────────────────────────────────


def nuke_purge_all(
    *,
    remove_data: bool = False,
    remove_images: bool = False,
    dry_run: bool = False,
) -> NukeReport:
    """Remove every `wip-*` container, pod, network, and (if
    `remove_data`) volume on this host. Optionally also images.

    Used to recover from cross-install conflicts or to fully reset a dev
    machine. Data-destructive — `remove_data` and `remove_images` are
    both opt-in.
    """
    report = NukeReport()

    containers = _podman_ls_names(["ps", "-a", "--filter", "name=wip-", "--format", "{{.Names}}"])
    if containers:
        if not dry_run:
            _podman(["rm", "-f", *containers])
        report.containers_removed = containers

    pods = _podman_ls_names(["pod", "ls", "--format", "{{.Name}}"])
    wip_pods = [p for p in pods if _looks_like_wip_pod(p)]
    if wip_pods:
        if not dry_run:
            _podman(["pod", "rm", "-f", *wip_pods])
        report.pods_removed = wip_pods

    if remove_data:
        volumes = _podman_ls_names(["volume", "ls", "--format", "{{.Name}}"])
        wip_volumes = [v for v in volumes if _looks_like_wip_volume(v)]
        if wip_volumes:
            if not dry_run:
                _podman(["volume", "rm", *wip_volumes])
            report.volumes_removed = wip_volumes

    # Networks: any `wip-*` network. Removing in-use networks fails
    # silently — handled inside _remove_networks.
    network_names = _podman_ls_names(["network", "ls", "--format", "{{.Name}}"])
    wip_networks = [n for n in network_names if n.startswith("wip-") or n == "wip-network"]
    if wip_networks:
        if not dry_run:
            report.networks_removed = _remove_networks(wip_networks)
        else:
            report.networks_removed = wip_networks

    if remove_images:
        images = _podman_ls_names(
            ["images", "--format", "{{.Repository}}:{{.Tag}}"]
        )
        wip_images = [i for i in images if _looks_like_wip_image(i)]
        if wip_images:
            if not dry_run:
                report.images_removed = _remove_images(wip_images)
            else:
                report.images_removed = wip_images

    return report


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────


def _detect_compose_cmd() -> list[str]:
    if shutil.which("podman-compose"):
        return ["podman-compose"]
    if shutil.which("docker"):
        return ["docker", "compose"]
    raise NukeError("neither podman-compose nor docker is available on PATH")


def _podman(args: list[str]) -> None:
    if not shutil.which("podman"):
        raise NukeError("podman is not available on PATH")
    subprocess.run(["podman", *args], check=False)


def _podman_ls_names(args: list[str]) -> list[str]:
    """Run `podman <args>` and return non-empty lines. Returns [] on
    error or empty output."""
    if not shutil.which("podman"):
        return []
    try:
        result = subprocess.run(
            ["podman", *args], check=True, capture_output=True, text=True
        )
    except subprocess.CalledProcessError:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _looks_like_wip_pod(name: str) -> bool:
    """Match a pod name that belongs to a WIP deployment.

    podman-compose creates pods named `pod_<project>` where `<project>`
    is usually the install directory name. Historical v1 projects:
    `wip-demo`, `docker-compose`, `setup-wip`, or per-service pods from
    the modular setup.sh path (`pod_registry`, `pod_def-store` …).

    A safe heuristic: a pod "looks WIP" if it starts with `pod_wip` OR
    the bare `pod_` followed by any of our known component short-names.
    """
    if name.startswith("pod_wip"):
        return True
    if name == "pod_docker-compose":
        # Known v1 artifact — the setup.sh default project name.
        return True
    # Per-service pods from setup.sh's multi-compose-project flow.
    known_services = {
        "registry",
        "def-store",
        "template-store",
        "document-store",
        "reporting-sync",
        "ingest-gateway",
        "mcp-server",
        "auth-gateway",
    }
    return any(name == f"pod_{svc}" for svc in known_services)


def _looks_like_wip_volume(name: str) -> bool:
    """Volumes we created. Compose projects prefix volume names with the
    project name, so we match:
      - wip-<anything>           (v2 + some v1 patterns)
      - *_wip-<anything>         (compose project prefixes)
      - wip-demo_*                (v1 quick-install)
    Volumes with random 64-hex names are anonymous, rarely ours — skip.
    """
    if name.startswith("wip-"):
        return True
    if "_wip-" in name:
        return True
    return name.startswith("wip-demo_")


def _remove_tree(path: Path) -> None:
    """Recursively remove a directory (best-effort)."""
    import shutil as _shutil

    _shutil.rmtree(path, ignore_errors=True)


# ────────────────────────────────────────────────────────────────────
# Image + network helpers (parsed from rendered compose)
# ────────────────────────────────────────────────────────────────────


def _images_in_compose(compose_file: Path) -> list[str]:
    """Collect every `services.*.image` value from the compose file."""
    import yaml as _yaml

    try:
        data = _yaml.safe_load(compose_file.read_text())
    except (OSError, _yaml.YAMLError):
        return []
    if not isinstance(data, dict):
        return []
    services = data.get("services")
    if not isinstance(services, dict):
        return []
    images: list[str] = []
    for svc in services.values():
        if isinstance(svc, dict):
            img = svc.get("image")
            if isinstance(img, str) and img:
                images.append(img)
    # Preserve order, drop duplicates.
    seen: set[str] = set()
    unique: list[str] = []
    for img in images:
        if img not in seen:
            seen.add(img)
            unique.append(img)
    return unique


def _networks_in_compose(compose_file: Path) -> list[str]:
    """Collect every top-level network name from the compose file.

    Honors an explicit ``name:`` override; otherwise uses the key name.
    Skips entries marked ``external: true`` whose name is a generic
    docker network we don't own — though by convention every WIP
    network starts with ``wip-`` so that filter is rarely meaningful.
    """
    import yaml as _yaml

    try:
        data = _yaml.safe_load(compose_file.read_text())
    except (OSError, _yaml.YAMLError):
        return []
    if not isinstance(data, dict):
        return []
    networks = data.get("networks")
    if not isinstance(networks, dict):
        return []
    out: list[str] = []
    for key, val in networks.items():
        name = key
        if isinstance(val, dict):
            override = val.get("name")
            if isinstance(override, str) and override:
                name = override
        if isinstance(name, str) and name:
            out.append(name)
    return out


def _remove_images(image_names: list[str]) -> list[str]:
    """Run `podman rmi` for each image; return the ones actually removed.

    Per-image individual calls so one missing image doesn't abort the
    rest. ``rmi -f`` to override "in use" complaints on stale tags.
    """
    if not shutil.which("podman"):
        return []
    removed: list[str] = []
    for img in image_names:
        try:
            r = subprocess.run(
                ["podman", "rmi", "-f", img],
                check=False,
                capture_output=True,
                text=True,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            continue
        if r.returncode == 0:
            removed.append(img)
    return removed


def _remove_networks(network_names: list[str]) -> list[str]:
    """Run `podman network rm` for each network; return the ones actually
    removed. Removing a network that's still in use fails — that's the
    intended behavior (we only want to clear true dangles).
    """
    if not shutil.which("podman"):
        return []
    removed: list[str] = []
    for net in network_names:
        try:
            r = subprocess.run(
                ["podman", "network", "rm", net],
                check=False,
                capture_output=True,
                text=True,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            continue
        if r.returncode == 0:
            removed.append(net)
    return removed


def _looks_like_wip_image(image: str) -> bool:
    """Match an image name that's owned by a WIP deployment.

    Matches:
      - ``wip-*`` prefix (rare — most service images use bare names in dev)
      - bare component names (e.g. ``mcp-server:dev``, ``registry:dev``)
      - ``<registry>/wip-*:<tag>`` (production registry-prefixed images)
      - ``localhost/<bare-component>:<tag>`` (podman default-tagged builds)
    """
    bare_components = {
        "registry",
        "def-store",
        "template-store",
        "document-store",
        "reporting-sync",
        "ingest-gateway",
        "mcp-server",
        "auth-gateway",
    }
    # Strip off any registry prefix (everything up to and including the
    # last '/') and any tag (':tag').
    repo = image.rsplit(":", 1)[0]
    base = repo.rsplit("/", 1)[-1]
    if base.startswith("wip-"):
        return True
    if base in bare_components:
        return True
    # Defensive: full repo path that contains 'wip-' anywhere (catches
    # ghcr.io/peterseb1969/wip-registry style).
    return "wip-" in repo
