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

Both modes keep data and secrets unless asked: `remove_data` and
`remove_secrets` are opt-in. On k8s that means a teardown without
`remove_data` deletes WIP's workloads but keeps the namespace, its PVCs and
its Secrets; only `remove_data` deletes the namespace. Every mode honours
`dry_run`: it reports what it would remove and removes nothing.

Removing data also removes the saved deployment state of every install
whose data went. `install` treats that state as a live install and refuses
to reshape it; once the volumes are gone it describes nothing, and keeping
it would block the next install while `redeploy` could only bring back an
empty copy of the old spec. Without `remove_data` the state stays, so
`redeploy` can bring the surviving volumes back.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from wip_deploy.apply import PODMAN_COMPOSE_PARALLEL
from wip_deploy.kube import DELETE_TIMEOUT_SECONDS, QUERY_TIMEOUT_SECONDS, kubectl
from wip_deploy.naming import NETWORK_KEY

# The persisted install spec, plus its pre-rename name.
STATE_FILENAMES = ("deployment.deployer-state", "deployment.json")


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
    # purge-all clears secrets host-wide (one dir per install), so it needs
    # a list rather than the scoped path's single dir (CASE-387).
    secrets_dirs_removed: list[Path] = field(default_factory=list)
    state_files_removed: list[Path] = field(default_factory=list)
    compose_down_ran: bool = False
    # CASE-362 k8s teardown.
    k8s_namespace_removed: str | None = None
    k8s_workloads_removed_from: str | None = None
    k8s_pvs_removed: list[str] = field(default_factory=list)
    # What the runtime refused to remove ("container X"), so the summary
    # reports results, not attempts.
    failed: list[str] = field(default_factory=list)
    # A dry run fills the fields with what WOULD be removed.
    dry_run: bool = False

    def summary(self) -> str:
        done = "would be removed" if self.dry_run else "removed"
        parts = []
        if self.compose_down_ran:
            parts.append("compose down would run" if self.dry_run else "compose down completed")
        if self.k8s_namespace_removed:
            parts.append(f"k8s namespace {done}: {self.k8s_namespace_removed}")
        if self.k8s_workloads_removed_from:
            parts.append(
                f"k8s workloads {done} from {self.k8s_workloads_removed_from} "
                f"(namespace, PVCs and Secrets kept)"
            )
        counted = (
            (self.k8s_pvs_removed, "PV(s)"),
            (self.containers_removed, "container(s)"),
            (self.pods_removed, "pod(s)"),
            (self.volumes_removed, "volume(s)"),
            (self.images_removed, "image(s)"),
            (self.networks_removed, "network(s)"),
            (self.secrets_dirs_removed, "secrets dir(s)"),
            (self.state_files_removed, "saved install state(s)"),
        )
        for items, label in counted:
            if items:
                parts.append(f"{len(items)} {label} {done}")
        if self.secrets_dir_removed:
            parts.append(f"secrets dir {done}: {self.secrets_dir_removed}")
        if self.failed:
            parts.append(f"{len(self.failed)} NOT removed: {', '.join(self.failed)}")
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
    dry_run: bool = False,
) -> NukeReport:
    """Tear down the compose project rooted at `install_dir`.

    `remove_data=True` passes `-v` to `compose down` so named volumes go
    too. `remove_secrets=True` additionally removes the secret backend
    directory. `remove_images=True` additionally removes every image
    referenced by the compose's `services.*.image` entries.

    Always sweeps any `wip-*` networks left dangling after `compose
    down` (e.g. when the network was declared `external: true`).

    `dry_run=True` runs nothing and returns what would be removed.
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

    report = NukeReport(dry_run=dry_run)
    cmd_prefix = _detect_compose_cmd()

    if dry_run:
        report.compose_down_ran = True
        report.images_removed = images
        report.networks_removed = networks
        _plan_secrets_and_state(
            report, install_dir, remove_data=remove_data,
            secrets_location=secrets_location, remove_secrets=remove_secrets,
        )
        return report

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

    _plan_secrets_and_state(
        report, install_dir, remove_data=False,
        secrets_location=secrets_location, remove_secrets=remove_secrets,
    )
    _apply_secrets_removal(report)
    if remove_data:
        report.state_files_removed = _remove_state_files(install_dir)

    return report


# ────────────────────────────────────────────────────────────────────
# k8s teardown (CASE-362)
# ────────────────────────────────────────────────────────────────────


def nuke_k8s_install_dir(
    install_dir: Path,
    *,
    namespace: str,
    remove_data: bool = False,
    secrets_location: Path | None = None,
    remove_secrets: bool = False,
    dry_run: bool = False,
) -> NukeReport:
    """Tear down a k8s install.

    Without `remove_data`: delete WIP's workloads and their wiring
    (Deployments, StatefulSets, Services, ConfigMaps, Ingresses,
    NetworkPolicies labelled `app.kubernetes.io/part-of=wip`) and keep the
    namespace, its PVCs and its Secrets, so `redeploy` brings the install
    back on its data. Deleting the namespace would cascade to the PVCs, and
    with a `Delete` reclaim policy (the usual default) the data with them.

    `remove_data=True` deletes the namespace (cascading to every namespaced
    resource, PVCs included) and then any cluster-scoped PVs that were bound
    to PVCs in it, so `Retain`-policy PVs go too.

    `remove_secrets=True` removes the local file-backend secrets
    directory at `secrets_location`.

    `dry_run=True` runs nothing and returns what would be removed.

    The renderer doesn't enumerate `--remove-images` for k8s today:
    images live in the registry, not on the operator's host. Skip with
    a no-op rather than error — keeps the CLI surface uniform with
    compose.
    """
    if not install_dir.exists():
        raise NukeError(f"install dir does not exist: {install_dir}")

    if not shutil.which("kubectl"):
        raise NukeError(
            "kubectl is not available on PATH. Install kubectl and "
            "configure access to the target cluster, or tear down "
            "manually via the cluster's UI."
        )

    report = NukeReport(dry_run=dry_run)

    if not remove_data:
        if not dry_run:
            _k8s_delete_workloads(namespace)
        report.k8s_workloads_removed_from = namespace
        _plan_secrets_and_state(
            report, install_dir, remove_data=False,
            secrets_location=secrets_location, remove_secrets=remove_secrets,
        )
        if not dry_run:
            _apply_secrets_removal(report)
        return report

    # Capture PVs bound to PVCs in this namespace BEFORE deleting it —
    # once the PVCs are gone, the PV's `claimRef` still points at the
    # (now-deleted) PVC, but we'd have to scan all PVs. Doing it now
    # is cheaper and gives a clean list for `--remove-data`.
    bound_pvs = _list_pvs_in_namespace(namespace)
    if dry_run:
        report.k8s_namespace_removed = namespace
        report.k8s_pvs_removed = bound_pvs
        _plan_secrets_and_state(
            report, install_dir, remove_data=True,
            secrets_location=secrets_location, remove_secrets=remove_secrets,
        )
        return report

    # Cascading delete. --ignore-not-found makes the call idempotent;
    # --wait=true blocks until the namespace is fully reclaimed (so a
    # subsequent re-install doesn't race the finalizers).
    try:
        subprocess.run(
            [
                *kubectl(),
                "delete",
                "namespace",
                namespace,
                "--ignore-not-found=true",
                "--wait=true",
            ],
            check=True,
            timeout=DELETE_TIMEOUT_SECONDS,
        )
        report.k8s_namespace_removed = namespace
    except subprocess.TimeoutExpired as e:
        raise NukeError(
            f"kubectl delete namespace {namespace} did not finish within "
            f"{DELETE_TIMEOUT_SECONDS} s (finalizers still pending?)"
        ) from e
    except subprocess.CalledProcessError as e:
        raise NukeError(
            f"kubectl delete namespace {namespace} failed (exit {e.returncode})"
        ) from e

    if bound_pvs:
        # The namespace delete cascaded the PVC; PVs with Retain
        # reclaim policy are now in 'Released' state and still hold
        # data. Delete them explicitly.
        report.k8s_pvs_removed = _delete_pvs(bound_pvs)

    _plan_secrets_and_state(
        report, install_dir, remove_data=True,
        secrets_location=secrets_location, remove_secrets=remove_secrets,
    )
    _apply_secrets_removal(report)
    report.state_files_removed = _remove_state_files(install_dir)
    return report


# The kinds a data-preserving k8s teardown deletes: what the renderer
# writes, minus PersistentVolumeClaims (the data) and Secrets (the keys and
# TLS material a redeploy reuses).
K8S_WORKLOAD_KINDS = (
    "deployments.apps",
    "statefulsets.apps",
    "services",
    "configmaps",
    "ingresses.networking.k8s.io",
    "networkpolicies.networking.k8s.io",
)


def _k8s_delete_workloads(namespace: str) -> None:
    try:
        subprocess.run(
            [
                *kubectl(),
                "delete",
                ",".join(K8S_WORKLOAD_KINDS),
                "-n",
                namespace,
                "-l",
                "app.kubernetes.io/part-of=wip",
                "--ignore-not-found=true",
                "--wait=true",
            ],
            check=True,
            timeout=DELETE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as e:
        raise NukeError(
            f"kubectl delete of WIP workloads in {namespace} did not finish "
            f"within {DELETE_TIMEOUT_SECONDS} s"
        ) from e
    except subprocess.CalledProcessError as e:
        raise NukeError(
            f"kubectl delete of WIP workloads in {namespace} failed (exit {e.returncode})"
        ) from e


def _plan_secrets_and_state(
    report: NukeReport,
    install_dir: Path,
    *,
    remove_data: bool,
    secrets_location: Path | None,
    remove_secrets: bool,
) -> None:
    """Record the secrets dir and saved state a teardown removes. Removes
    the state only on a real run; the secrets dir is removed by
    `_apply_secrets_removal`."""
    if remove_secrets and secrets_location is not None and Path(secrets_location).exists():
        report.secrets_dir_removed = Path(secrets_location)
    if remove_data:
        report.state_files_removed = _remove_state_files(install_dir, dry_run=True)


def _apply_secrets_removal(report: NukeReport) -> None:
    if report.secrets_dir_removed is not None:
        _remove_tree(report.secrets_dir_removed)


def _list_pvs_in_namespace(namespace: str) -> list[str]:
    """Return the names of cluster-scoped PVs bound to PVCs in
    `namespace`. Empty list on any error (the worst case is we miss a
    Retain-policy PV; the namespace-delete itself still succeeds)."""
    if not shutil.which("kubectl"):
        return []
    try:
        result = subprocess.run(
            [
                *kubectl(),
                "get",
                "pv",
                "-o",
                "jsonpath={range .items[*]}"
                "{.spec.claimRef.namespace}{\"\\t\"}{.metadata.name}"
                "{\"\\n\"}{end}",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=QUERY_TIMEOUT_SECONDS,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []
    out: list[str] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) == 2 and parts[0] == namespace:
            out.append(parts[1])
    return out


def _delete_pvs(pv_names: list[str]) -> list[str]:
    """Best-effort `kubectl delete pv` for each name. Returns the names
    that the cluster accepted the delete for. We don't fail the whole
    teardown if a single PV refuses to delete (rare but possible —
    e.g., a finalizer still pending)."""
    if not shutil.which("kubectl") or not pv_names:
        return []
    removed: list[str] = []
    for pv in pv_names:
        try:
            subprocess.run(
                [*kubectl(), "delete", "pv", pv, "--ignore-not-found=true"],
                check=True,
                timeout=DELETE_TIMEOUT_SECONDS,
            )
            removed.append(pv)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            # Don't abort the whole nuke for one stuck PV; the operator
            # can clean it up manually after.
            continue
    return removed


def has_k8s_install(install_root: Path) -> bool:
    """Return True if any `~/.wip-deploy/<name>/` saved state (either
    name in STATE_FILENAMES) under `install_root` declares target=k8s. Used by --purge-all to
    refuse on hosts with k8s installs (CASE-362)."""
    if not install_root.exists():
        return False
    import json
    for child in install_root.iterdir():
        state = next(
            (child / n for n in STATE_FILENAMES if (child / n).is_file()), None
        )
        if state is None:
            continue
        try:
            payload = json.loads(state.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        try:
            if payload["deployment"]["spec"]["target"] == "k8s":
                return True
        except (KeyError, TypeError):
            continue
    return False


# ────────────────────────────────────────────────────────────────────
# Purge all wip-* resources (cross-install cleanup)
# ────────────────────────────────────────────────────────────────────


def nuke_purge_all(
    *,
    remove_data: bool = False,
    remove_images: bool = False,
    remove_secrets: bool = False,
    install_root: Path | None = None,
    dry_run: bool = False,
) -> NukeReport:
    """Remove every `wip-*` container, pod, network, and (if
    `remove_data`) volume on this host. Optionally also images.

    Used to recover from cross-install conflicts or to fully reset a dev
    machine. Data-destructive — `remove_data`, `remove_images`, and
    `remove_secrets` are all opt-in.

    `remove_secrets=True` deletes the file-backend `secrets/` directory of
    every install under `install_root` (default `~/.wip-deploy/`). purge-all
    is host-wide by design, so its secrets removal is too — symmetric with
    the host-wide volume sweep. Without this, a surviving `secrets/api-key`
    makes the next install reuse the same master key (CASE-387: the scoped
    teardown honored `--remove-secrets`, but purge-all silently dropped it).
    """
    report = NukeReport(dry_run=dry_run)

    containers = _podman_ls_names(["ps", "-a", "--filter", "name=wip-", "--format", "{{.Names}}"])
    report.containers_removed = _remove_each(
        ["rm", "-f"], containers, "container", report, dry_run=dry_run,
    )

    root = install_root if install_root is not None else (Path.home() / ".wip-deploy")
    install_dirs = sorted(d for d in root.iterdir() if d.is_dir()) if root.exists() else []
    wip_pods = _wip_pods(install_dirs)
    report.pods_removed = _remove_each(
        ["pod", "rm", "-f"], wip_pods, "pod", report, dry_run=dry_run,
    )

    if remove_data:
        volumes = _podman_ls_names(["volume", "ls", "--format", "{{.Name}}"])
        wip_volumes = [v for v in volumes if _looks_like_wip_volume(v)]
        report.volumes_removed = _remove_each(
            ["volume", "rm"], wip_volumes, "volume", report, dry_run=dry_run,
        )

    # Networks: any `wip-*` network. Removing in-use networks fails
    # silently — handled inside _remove_networks.
    network_names = _podman_ls_names(["network", "ls", "--format", "{{.Name}}"])
    wip_networks = [
        n for n in network_names
        if n.startswith("wip-") or n.endswith(f"_{NETWORK_KEY}")
    ]
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

    if remove_secrets:
        report.secrets_dirs_removed = _remove_secrets_dirs(install_dirs, dry_run=dry_run)

    # Every wip-* volume on the host is gone, so every install's saved
    # state is stale. purge-all refuses to run while a k8s install exists,
    # so only compose/dev states can be here.
    if remove_data:
        for child in install_dirs:
            report.state_files_removed.extend(_remove_state_files(child, dry_run=dry_run))

    return report


def _wip_pods(install_dirs: list[Path]) -> list[str]:
    """The pods purge-all removes: the fixed WIP patterns, plus each
    install's own pod, which podman-compose names after the project, i.e.
    the install dir (`pod_tc02`)."""
    install_pods = {f"pod_{d.name}" for d in install_dirs}
    pods = _podman_ls_names(["pod", "ls", "--format", "{{.Name}}"])
    return [p for p in pods if _looks_like_wip_pod(p) or p in install_pods]


def _remove_secrets_dirs(install_dirs: list[Path], *, dry_run: bool) -> list[Path]:
    removed = []
    for child in install_dirs:
        secrets_dir = child / "secrets"
        if secrets_dir.is_dir():
            if not dry_run:
                _remove_tree(secrets_dir)
            removed.append(secrets_dir)
    return removed


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────


def _remove_state_files(install_dir: Path, *, dry_run: bool = False) -> list[Path]:
    """Remove `install_dir`'s saved deployment state; return what went."""
    removed = [install_dir / n for n in STATE_FILENAMES if (install_dir / n).is_file()]
    if not dry_run:
        for path in removed:
            path.unlink()
    return removed


def _podman(args: list[str]) -> subprocess.CompletedProcess[str]:
    if not shutil.which("podman"):
        raise NukeError("podman is not available on PATH")
    return subprocess.run(["podman", *args], check=False, capture_output=True, text=True)


def _detect_compose_cmd() -> list[str]:
    if shutil.which("podman-compose"):
        return ["podman-compose", "--parallel", str(PODMAN_COMPOSE_PARALLEL)]
    if shutil.which("docker"):
        return ["docker", "compose"]
    raise NukeError("neither podman-compose nor docker is available on PATH")


def _remove_each(
    rm_args: list[str], names: list[str], kind: str, report: NukeReport,
    *, dry_run: bool,
) -> list[str]:
    """`podman <rm_args> <name>` per name; return the names it removed and
    record the refusals on `report.failed`. One call per name, because a
    batched call that removes some and refuses others reports neither."""
    if dry_run or not names:
        return list(names)
    removed: list[str] = []
    for name in names:
        r = _podman([*rm_args, name])
        if r.returncode == 0:
            removed.append(name)
        else:
            report.failed.append(f"{kind} {name}")
    return removed


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
    # Official upstream images are listed by podman under their full name
    # (`docker.io/library/registry:2`); WIP's own are bare, localhost/ or
    # under the install's registry. Without this, the base name `registry`
    # would take the upstream registry image with it.
    if repo.startswith("docker.io/library/"):
        return False
    base = repo.rsplit("/", 1)[-1]
    if base.startswith("wip-"):
        return True
    if base in bare_components:
        return True
    # Defensive: full repo path that contains 'wip-' anywhere (catches
    # ghcr.io/peterseb1969/wip-registry style).
    return "wip-" in repo
