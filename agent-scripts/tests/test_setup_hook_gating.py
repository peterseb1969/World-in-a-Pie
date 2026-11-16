"""Step 4b of scripts/setup-backend-agent.sh — when the ratchet hook gets enabled.

Enabling `core.hooksPath` is a switch with two silent failure modes: a hook
that refuses every commit because its interpreter has no radon, and a hooks
directory whose own hooks stop running the moment the key is set. The step is
read out of the script and run against scratch repositories, so the gating is
checked rather than asserted from the prose around it.
"""

from __future__ import annotations

import re
import shutil
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


def test_a_symlinked_hook_blocks_it_as_much_as_a_regular_one(tmp_path: Path):
    """A hook that links to a script elsewhere is a hook git runs.

    Listing the directory by file type alone sees only regular files, so the
    link is invisible and setting the path would retire it in silence.
    """
    init_repo(tmp_path)
    target = tmp_path / "tools" / "hooks" / "commit-msg"
    target.parent.mkdir(parents=True)
    target.write_text("#!/bin/sh\nexit 0\n")
    target.chmod(0o755)
    link = tmp_path / ".git" / "hooks" / "pre-commit"
    link.symlink_to(target)
    result = run(tmp_path, radon=True)
    assert result.returncode == 0, result.stderr
    assert hooks_path(tmp_path) is None, (
        "setting core.hooksPath silently retires the symlinked hook in .git/hooks"
    )
    assert "pre-commit" in result.stdout


def test_a_symlinked_hooks_directory_is_searched_through(tmp_path: Path):
    """.git/hooks itself can be a link; the hooks behind it are still live."""
    init_repo(tmp_path)
    real = tmp_path / "shared-hooks"
    real.mkdir()
    own = real / "commit-msg"
    own.write_text("#!/bin/sh\nexit 0\n")
    own.chmod(0o755)
    hooks = tmp_path / ".git" / "hooks"
    shutil.rmtree(hooks)
    hooks.symlink_to(real)
    result = run(tmp_path, radon=True)
    assert result.returncode == 0, result.stderr
    assert hooks_path(tmp_path) is None, (
        "the hooks behind the linked hooks directory would stop running"
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


def test_a_file_git_would_not_run_is_no_hook(tmp_path: Path):
    """Git runs only an executable hook: a non-executable file (a README, a
    script whose bit was cleared) in .git/hooks retires nothing, so it does not
    block."""
    init_repo(tmp_path)
    stray = tmp_path / ".git" / "hooks" / "commit-msg"
    stray.write_text("#!/bin/sh\nexit 0\n")
    stray.chmod(0o644)
    (tmp_path / ".git" / "hooks" / "README").write_text("notes\n")
    result = run(tmp_path, radon=True)
    assert result.returncode == 0, result.stderr
    assert hooks_path(tmp_path) == "scripts/git-hooks"


def test_an_executable_backup_of_a_hook_is_no_hook(tmp_path: Path):
    """Archiving a hook the usual way keeps its mode bit, and git never runs
    the result: `mv pre-commit pre-commit.disabled` and `cp -p commit-msg
    commit-msg.bak` both stay 0755. Only a hook name is a hook, so neither
    retires anything and neither may hold the ratchet back."""
    init_repo(tmp_path)
    for name in ("pre-commit.disabled", "commit-msg.bak", "pre-push.old"):
        stray = tmp_path / ".git" / "hooks" / name
        stray.write_text("#!/bin/sh\nexit 0\n")
        stray.chmod(0o755)
    result = run(tmp_path, radon=True)
    assert result.returncode == 0, result.stderr
    assert hooks_path(tmp_path) == "scripts/git-hooks", (
        "a file git does not run cannot be retired by setting the path"
    )
    for name in ("pre-commit.disabled", "commit-msg.bak", "pre-push.old"):
        assert name not in result.stdout


def test_a_hook_under_another_git_hook_name_blocks_it(tmp_path: Path):
    """post-commit is a hook git runs (githooks(5)) though git ships no sample
    for it. Filtering the entries down to the names git ships as samples would
    stop seeing it, and setting the path would retire it in silence."""
    init_repo(tmp_path)
    own = tmp_path / ".git" / "hooks" / "post-commit"
    own.write_text("#!/bin/sh\nexit 0\n")
    own.chmod(0o755)
    result = run(tmp_path, radon=True)
    assert result.returncode == 0, result.stderr
    assert hooks_path(tmp_path) is None, (
        "setting core.hooksPath silently retires the post-commit hook in .git/hooks"
    )
    assert "post-commit" in result.stdout


def test_a_link_to_a_non_executable_script_is_no_hook(tmp_path: Path):
    init_repo(tmp_path)
    target = tmp_path / "tools" / "commit-msg"
    target.parent.mkdir(parents=True)
    target.write_text("#!/bin/sh\nexit 0\n")
    target.chmod(0o644)
    (tmp_path / ".git" / "hooks" / "commit-msg").symlink_to(target)
    result = run(tmp_path, radon=True)
    assert result.returncode == 0, result.stderr
    assert hooks_path(tmp_path) == "scripts/git-hooks"


def test_a_re_run_warns_of_hooks_added_after_the_path_was_set(tmp_path: Path):
    """Once core.hooksPath points at scripts/git-hooks, a hook dropped into
    .git/hooks later is not run. A re-run of the setup says so."""
    init_repo(tmp_path)
    assert run(tmp_path, radon=True).returncode == 0
    assert hooks_path(tmp_path) == "scripts/git-hooks"
    later = tmp_path / ".git" / "hooks" / "prepare-commit-msg"
    later.write_text("#!/bin/sh\nexit 0\n")
    later.chmod(0o755)
    result = run(tmp_path, radon=True)
    assert result.returncode == 0, result.stderr
    assert hooks_path(tmp_path) == "scripts/git-hooks"
    assert "WARNING" in result.stdout and "not run: prepare-commit-msg" in result.stdout


def test_a_re_run_without_radon_says_the_hook_it_left_is_active(tmp_path: Path):
    """An earlier run set the key; the hook stays installed and refuses every
    commit once the venv loses radon (a recreated .venv, a skipped or failed
    install). Saying it is NOT enabled sends the operator to the wrong file."""
    init_repo(tmp_path)
    assert run(tmp_path, radon=True).returncode == 0
    assert hooks_path(tmp_path) == "scripts/git-hooks"
    result = run(tmp_path, radon=False)
    assert result.returncode == 0, result.stderr
    assert hooks_path(tmp_path) == "scripts/git-hooks", (
        "step 4b unsetting a key it did not set would drop the gate unasked"
    )
    assert "IS active" in result.stdout, result.stdout
    assert "NOT enabled" not in result.stdout, result.stdout
    assert "git config --unset core.hooksPath" in result.stdout


def test_a_re_run_without_radon_over_someone_elses_path_says_not_enabled(tmp_path: Path):
    """git uses the other hooks directory, so the ratchet really is off here —
    the blocked state above is only ours."""
    init_repo(tmp_path)
    subprocess.run(
        ["git", "config", "core.hooksPath", "my-hooks"], cwd=tmp_path, check=True, capture_output=True,
    )
    result = run(tmp_path, radon=False)
    assert result.returncode == 0, result.stderr
    assert hooks_path(tmp_path) == "my-hooks"
    assert "NOT enabled" in result.stdout and "IS active" not in result.stdout
