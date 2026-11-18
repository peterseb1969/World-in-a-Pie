"""A command healthcheck leaves no zombie behind (CASE-1428).

A command check runs under `timeout` so a hung check is stopped. busybox's
`timeout` runs the command in the parent and its watchdog in a forked
child; when the command exits, the watchdog is re-parented to the
container's PID 1. The router and nats run caddy and nats-server as PID 1,
which never reap, so every check run (every 10 s) left a zombie, until the
podman VM's task limit refused every fork and the VM locked up. Every
service with a command check now runs under podman's init, which reaps.

The renderer tests run everywhere. The image tests start the router and nats
images with the rendered healthcheck and need a podman that answers; they
are skipped without one.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml

from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_compose, render_dev_simple
from wip_deploy.secrets import ensure_secrets
from wip_deploy.secrets_backend import FileSecretBackend

from .test_case_1225 import _deployment

REPO_ROOT = Path(__file__).parent.parent.parent.parent.resolve()
RUNS = 20


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _services(target: str, tmp_path: Path, disc: Discovery) -> dict[str, Any]:
    d = _deployment("c1428", target)
    s = ensure_secrets(d, disc.components, disc.apps, FileSecretBackend(tmp_path / "secrets"))
    tree = (
        render_compose(d, disc.components, disc.apps, s)
        if target == "compose"
        else render_dev_simple(d, disc.components, disc.apps, s, repo_root=REPO_ROOT)
    )
    return yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)["services"]


def _command_check(block: dict[str, Any]) -> bool:
    test = (block.get("healthcheck") or {}).get("test") or []
    return len(test) == 2 and test[1].startswith("timeout ")


@pytest.mark.parametrize("target", ["compose", "dev"])
def test_every_command_check_runs_under_an_init(target: str, tmp_path: Path, discovery: Discovery) -> None:
    services = _services(target, tmp_path, discovery)
    wrapped = {name for name, block in services.items() if _command_check(block)}
    assert {"router", "nats"} <= wrapped
    assert {name for name in wrapped if services[name].get("init") is not True} == set()


@pytest.mark.parametrize("target", ["compose", "dev"])
def test_an_http_probe_gets_no_init(target: str, tmp_path: Path, discovery: Discovery) -> None:
    services = _services(target, tmp_path, discovery)
    probed = [n for n, b in services.items() if b.get("healthcheck") and not _command_check(b)]
    assert probed
    assert all("init" not in services[n] for n in probed)


# ── the images themselves ──


def _podman_answers() -> bool:
    if shutil.which("podman") is None:
        return False
    try:
        return subprocess.run(["podman", "info"], capture_output=True, timeout=20).returncode == 0
    except subprocess.TimeoutExpired:
        return False


needs_podman = pytest.mark.skipif(not _podman_answers(), reason="needs a podman that answers")

_COUNT_ZOMBIES = (
    'n=0; for s in /proc/[0-9]*/stat; do '
    '[ "$(cut -d" " -f3 "$s" 2>/dev/null)" = Z ] && n=$((n+1)); done; echo $n'
)


def _zombies_after_checks(image: str, check: str, *, init: bool) -> int:
    """Start `image` with a PID 1 that never reaps (`sleep`), run the rendered
    healthcheck RUNS times as podman runs it, and count the zombies."""
    name = f"c1428-{uuid.uuid4().hex[:8]}"
    run = ["podman", "run", "-d", "--rm", "--name", name, "--entrypoint", "sleep"]
    if init:
        run.append("--init")
    started = subprocess.run([*run, image, "600"], capture_output=True, text=True, timeout=300)
    if started.returncode != 0:
        pytest.skip(f"cannot start {image}: {started.stderr.strip()}")
    try:
        for _ in range(RUNS):
            subprocess.run(["podman", "exec", name, "sh", "-c", check], capture_output=True, timeout=30)
        counted = subprocess.run(
            ["podman", "exec", name, "sh", "-c", _COUNT_ZOMBIES],
            capture_output=True, text=True, timeout=30, check=True,
        )
        return int(counted.stdout.strip())
    finally:
        subprocess.run(["podman", "rm", "-f", name], capture_output=True, timeout=60)


@needs_podman
@pytest.mark.parametrize("service", ["router", "nats"])
def test_the_rendered_check_leaves_no_zombie_in_the_real_image(
    service: str, tmp_path: Path, discovery: Discovery,
) -> None:
    block = _services("compose", tmp_path, discovery)[service]
    check = block["healthcheck"]["test"][1]
    without_init = _zombies_after_checks(block["image"], check, init=False)
    # The bed reaches the leak: without an init, the image's `timeout` leaves
    # zombies. If it ever stops doing so, this test no longer proves anything.
    assert without_init > 0, f"{block['image']} left no zombie without an init"
    assert _zombies_after_checks(block["image"], check, init=block.get("init") is True) == 0
