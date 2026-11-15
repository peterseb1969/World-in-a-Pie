"""The shipped pre-commit hook refuses local config and secrets at any path.

The hook read the staged paths from `git diff --cached --name-only`, which
C-quotes a path holding a non-ASCII byte, a '"', a backslash or a control
character under git's default core.quotePath. The quoted string matched none
of the anchored path patterns and, handed back to git as a pathspec, named no
file, so a `.env` under such a directory, or a key in a file there, was
committed with no word. Paths are now read NUL-delimited and handed back as
`:(literal)` pathspecs, which also covers names starting with ':' or holding
glob characters. Each case runs the hook as installed, in a scratch
repository, with HOME pointed away from this machine's real secrets.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / "scaffold" / "templates" / "hooks" / "pre-commit-local-config.sh"
# The oldest bash the hook must run under: macOS ships 3.2 as /bin/bash.
BASH = "/bin/bash" if Path("/bin/bash").exists() else shutil.which("bash")
KEY = "sk-ant-" + "a" * 32
LOCAL_SECRET = "s3cr3t-value-from-this-machine"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "app"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "core.quotePath", "true")  # git's default, whatever ~/.gitconfig says
    home = tmp_path / "home"
    secrets = home / ".wip-deploy" / "dev" / "secrets"
    secrets.mkdir(parents=True)
    (secrets / "api-key").write_text(LOCAL_SECRET + "\n")
    env = {**os.environ, "HOME": str(home), "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    return root, env


def _hook(root: Path, env: dict[str, str], path: str, text: str) -> subprocess.CompletedProcess:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    subprocess.run(["git", "-C", str(root), "add", "-f", "--", f":(literal){path}"],
                   check=True, capture_output=True, env=env)
    return subprocess.run([BASH, str(HOOK)], cwd=root, env=env, capture_output=True, text=True, timeout=60)


# Every directory shape that git quotes, plus the plain ASCII baseline: a
# test that passes on the baseline alone proves nothing about the fix.
DIRECTORIES = ["docs", "docs/設計", 'docs/a"b', "docs/back\\slash", "docs/tab\there"]


@pytest.mark.parametrize("directory", DIRECTORIES)
def test_a_local_env_file_is_refused_at_any_path(repo, directory):
    root, env = repo
    result = _hook(root, env, f"{directory}/.env", "A=1\n")
    assert result.returncode == 1, result.stderr
    assert "local environment config" in result.stderr


@pytest.mark.parametrize("directory", DIRECTORIES)
def test_a_key_shape_is_refused_at_any_path(repo, directory):
    root, env = repo
    result = _hook(root, env, f"{directory}/notes.txt", f"token {KEY}\n")
    assert result.returncode == 1, result.stderr
    assert "Anthropic API key" in result.stderr


@pytest.mark.parametrize("directory", DIRECTORIES)
def test_a_secret_value_from_this_machine_is_refused_at_any_path(repo, directory):
    root, env = repo
    result = _hook(root, env, f"{directory}/config.txt", f"key={LOCAL_SECRET}\n")
    assert result.returncode == 1, result.stderr
    assert "value of a secret from this machine" in result.stderr


@pytest.mark.parametrize("name", [":odd.txt", "[a].txt", "*.txt"])
def test_a_name_git_would_read_as_pathspec_magic_is_checked_as_itself(repo, name):
    root, env = repo
    result = _hook(root, env, name, f"token {KEY}\n")
    assert result.returncode == 1, result.stderr


@pytest.mark.parametrize("directory", DIRECTORIES)
def test_an_ordinary_file_passes(repo, directory):
    root, env = repo
    result = _hook(root, env, f"{directory}/readme.md", "nothing secret here\n")
    assert result.returncode == 0, result.stderr


def test_nothing_staged_passes(repo):
    root, env = repo
    result = subprocess.run([BASH, str(HOOK)], cwd=root, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
