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


# --- collecting this machine's secret values in one pass ----------------------
#
# The gate reads every file under ~/.wip-deploy/*/secrets/ in one awk run (it
# forked a subshell per file, over a second per commit on a host with many
# installs). Each file's value is its content with every CR and LF removed,
# kept when 12 characters or longer.


def _secret(env: dict[str, str], install: str, name: str, content: bytes) -> None:
    secrets = Path(env["HOME"]) / ".wip-deploy" / install / "secrets"
    secrets.mkdir(parents=True, exist_ok=True)
    (secrets / name).write_bytes(content)


@pytest.mark.parametrize("content, value", [
    (b"value-from-another-install\n", "value-from-another-install"),
    (b"crlf-secret-value-here\r\n", "crlf-secret-value-here"),
    (b"no-trailing-newline-value", "no-trailing-newline-value"),
    (b"split-across-\nlines-value\n", "split-across-lines-value"),
])
def test_every_install_and_file_shape_is_collected(repo, content, value):
    root, env = repo
    _secret(env, "other", "token", content)
    _secret(env, "other", "empty", b"")
    result = _hook(root, env, "notes.txt", f"x={value}\n")
    assert result.returncode == 1, result.stderr
    assert "value of a secret from this machine" in result.stderr


def test_a_value_shorter_than_twelve_characters_is_not_collected(repo):
    root, env = repo
    _secret(env, "other", "short", b"elevenchars\n")
    result = _hook(root, env, "notes.txt", "x=elevenchars\n")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("value", ["first-file-value", "second-file-value", "third-file-value"])
def test_each_file_is_its_own_value_when_read_in_one_pass(repo, value):
    # One awk run reads every file; were neighbouring files joined into one
    # value, a file's own value alone would no longer match. The empty file
    # in the middle must not join its neighbours either.
    root, env = repo
    _secret(env, "a", "1", b"first-file-value\n")
    _secret(env, "a", "2", b"")
    _secret(env, "a", "3", b"second-file-value")
    _secret(env, "a", "4", b"third-file-value\n")
    result = _hook(root, env, "notes.txt", f"x={value}\n")
    assert result.returncode == 1, result.stderr


def test_a_secret_file_with_a_nul_byte_is_read_up_to_the_nul(repo):
    # The awk that reads every secret file in one pass cuts each line at its
    # first NUL byte. BSD awk does so by itself; gawk and mawk (Linux, CI)
    # kept the NUL and the rest in the value, which then matched nothing, so
    # the hook cuts it explicitly. BusyBox awk drops the NUL instead, so there
    # such a value matches nothing. wip-deploy writes its secrets as text; a
    # binary file's value is what precedes its first NUL, or nothing when that
    # is under 12 characters.
    root, env = repo
    _secret(env, "other", "binary", b"visible-part-before\0rest\n")
    result = _hook(root, env, "notes.txt", "x=visible-part-before\n")
    assert result.returncode == 1, result.stderr


def _secret_reader() -> str:
    """The hook's own awk program that reads the secret files."""
    import re

    return re.search(r"awk '(BEGIN \{ nul.*?)' \\\n", HOOK.read_text(), re.S).group(1)


def test_a_secret_file_that_cannot_be_opened_does_not_drop_the_others(tmp_path):
    """Every awk stops at an operand it cannot open, and the value of the
    file before it was lost too, so check 2 passed a commit carrying it. A
    file removed after the list was built is now skipped and named in a
    note, and the others are still read."""
    (tmp_path / "a").write_text("secret-value-aaaa\n")
    (tmp_path / "b").write_text("secret-value-bbbb\n")
    result = subprocess.run(
        ["awk", _secret_reader(), "a", "gone", "b"],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["secret-value-aaaa", "secret-value-bbbb"]
    assert "1 secret file(s) could not be read" in result.stderr


def test_with_every_secret_file_gone_the_reader_does_not_wait_on_stdin(tmp_path):
    result = subprocess.run(
        ["awk", _secret_reader(), "gone-1", "gone-2"],
        cwd=tmp_path, input="secret-from-stdin-xxxx\n", capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_an_added_line_starting_with_plus_plus_is_checked(repo):
    # The diff shows it as "+++…", the shape of the file header the checks skip.
    root, env = repo
    result = _hook(root, env, "notes.txt", f"++{LOCAL_SECRET}\n++token {KEY}\n")
    assert "value of a secret from this machine" in result.stderr
    assert "Anthropic API key" in result.stderr


def test_a_host_without_wip_deploy_passes_and_does_not_wait_on_stdin(repo):
    root, env = repo
    shutil.rmtree(Path(env["HOME"]) / ".wip-deploy")
    result = _hook(root, env, "readme.md", "nothing secret here\n")
    assert result.returncode == 0, result.stderr
    result = _hook(root, env, "notes.txt", f"token {KEY}\n")
    assert result.returncode == 1, result.stderr
