"""Step 4b of scripts/setup-backend-agent.sh — when the ratchet hook gets enabled.

Enabling `core.hooksPath` is a switch with two silent failure modes: a hook
that refuses every commit because its interpreter has no radon, and a hooks
directory whose own hooks stop running the moment the key is set. The step is
read out of the script and run against scratch repositories, so the gating is
checked rather than asserted from the prose around it.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SETUP = REPO / "scripts" / "setup-backend-agent.sh"


def step_4b() -> str:
    """The 4b block, from its header comment to the next numbered step."""
    script = SETUP.read_text(encoding="utf-8")
    match = re.search(r"^# --- 4b\..*?(?=^# --- )", script, re.S | re.M)
    assert match, "step 4b (the git hooks step) not found in setup-backend-agent.sh"
    return match.group(0)


def run(tmp_path: Path, *, radon: bool) -> subprocess.CompletedProcess[str]:
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    if radon:
        # A wrapper, not a symlink: reached through a symlink the interpreter
        # finds no pyvenv.cfg and loses the venv's site-packages, radon with it.
        venv_python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    else:
        venv_python.write_text("#!/bin/sh\nexit 1\n")
    venv_python.chmod(0o755)
    hooks = tmp_path / "scripts" / "git-hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "pre-commit").write_text((REPO / "scripts" / "git-hooks" / "pre-commit").read_text())
    (hooks / "pre-commit").chmod(0o755)
    script = (
        "set -euo pipefail\n"
        f'WIP_ROOT="{tmp_path}"\n'
        f'VENV_PYTHON="{venv_python}"\n'
        + step_4b()
    )
    return subprocess.run(
        ["bash", "-c", script], cwd=tmp_path, capture_output=True, text=True,
    )


def init_repo(tmp_path: Path) -> Path:
    for args in (
        ["init", "-q"],
        ["config", "user.email", "t@example.invalid"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def hooks_path(tmp_path: Path) -> str | None:
    res = subprocess.run(
        ["git", "config", "--get", "core.hooksPath"], cwd=tmp_path, capture_output=True, text=True,
    )
    return res.stdout.strip() or None


def test_enabled_when_the_venv_has_radon(tmp_path: Path):
    init_repo(tmp_path)
    result = run(tmp_path, radon=True)
    assert result.returncode == 0, result.stderr
    assert hooks_path(tmp_path) == "scripts/git-hooks"


def test_not_enabled_when_radon_is_missing(tmp_path: Path):
    init_repo(tmp_path)
    result = run(tmp_path, radon=False)
    assert result.returncode == 0, result.stderr
    assert hooks_path(tmp_path) is None, "the hook was enabled without radon: every commit would fail"
    assert "NOT enabled" in result.stdout
    assert "radon" in result.stdout


def test_not_enabled_over_a_hooks_directory_with_its_own_hooks(tmp_path: Path):
    init_repo(tmp_path)
    own = tmp_path / ".git" / "hooks" / "commit-msg"
    own.write_text("#!/bin/sh\nexit 0\n")
    own.chmod(0o755)
    result = run(tmp_path, radon=True)
    assert result.returncode == 0, result.stderr
    assert hooks_path(tmp_path) is None, (
        "setting core.hooksPath silently retires the hooks in .git/hooks"
    )
    assert "commit-msg" in result.stdout


def test_the_sample_hooks_git_ships_do_not_block_it(tmp_path: Path):
    init_repo(tmp_path)
    # git creates *.sample files in every fresh repository.
    samples = list((tmp_path / ".git" / "hooks").glob("*.sample"))
    assert samples, "a fresh clone is expected to hold sample hooks"
    result = run(tmp_path, radon=True)
    assert result.returncode == 0, result.stderr
    assert hooks_path(tmp_path) == "scripts/git-hooks"


def test_a_hooks_path_someone_else_set_is_left_alone(tmp_path: Path):
    init_repo(tmp_path)
    subprocess.run(
        ["git", "config", "core.hooksPath", "my-hooks"], cwd=tmp_path, check=True, capture_output=True,
    )
    result = run(tmp_path, radon=True)
    assert result.returncode == 0, result.stderr
    assert hooks_path(tmp_path) == "my-hooks"
    assert "left as is" in result.stdout


def test_re_running_is_idempotent(tmp_path: Path):
    init_repo(tmp_path)
    assert run(tmp_path, radon=True).returncode == 0
    assert run(tmp_path, radon=True).returncode == 0
    assert hooks_path(tmp_path) == "scripts/git-hooks"
