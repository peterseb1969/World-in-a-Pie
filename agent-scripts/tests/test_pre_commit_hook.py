"""scripts/git-hooks/pre-commit — the ratchet a clone runs before committing.

The hook is shell with no .sh suffix, so nothing else in the repository's own
gates ran it, and its "the hook passes" claim was only ever checked by hand.
These tests run it in a scratch repository: the real hook file, the real
ratchet script, an interpreter that imports radon, and commits that either add
a C+ function to an audited path or do not.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / "scripts" / "git-hooks" / "pre-commit"
RATCHET = REPO / "scripts" / "check-complexity-ratchet.py"

# A path the ratchet audits (scripts/check-complexity-ratchet.py AUDITED_PATHS).
AUDITED = "components/registry/src/registry/x.py"


def branches(n: int) -> str:
    """A function of cyclomatic complexity n + 1 (n independent ifs)."""
    body = "".join(f"    if a == {i}:\n        a += 1\n" for i in range(n))
    return f"def f(a):\n{body}    return a\n"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True,
    )


def make_repo(tmp_path: Path, *, radon: bool = True) -> Path:
    """A committed scratch repository wired to run the clone's hook."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.invalid")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "core.hooksPath", str(HOOK.parent))
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    # A wrapper, not a symlink: an interpreter reached through a symlink finds
    # no pyvenv.cfg next to it and loses the venv's site-packages, where radon
    # lives. `pip install` is not this hook's business, only `import radon`.
    venv_python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    if not radon:
        # The state a skipped or failed setup leaves behind: an interpreter
        # that answers "no" to `import radon`.
        venv_python.write_text("#!/bin/sh\nexit 1\n")
    venv_python.chmod(0o755)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / RATCHET.name).write_text(RATCHET.read_text(encoding="utf-8"))
    write_and_commit(tmp_path, {AUDITED: "def f(a):\n    return a\n"})
    return tmp_path


def write_and_commit(repo: Path, files: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Stage files and run `git commit` — hooks included — returning the run."""
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(repo, "add", "-A")
    return subprocess.run(
        ["git", "commit", "-m", "change"], cwd=repo, capture_output=True, text=True,
    )


def test_hook_is_executable_shell():
    assert HOOK.is_file()
    assert os.access(HOOK, os.X_OK), "git only runs an executable hook"


def test_a_commit_that_adds_no_complexity_passes(tmp_path: Path):
    repo = make_repo(tmp_path)
    result = write_and_commit(repo, {AUDITED: "def f(a):\n    if a:\n        return 1\n    return 0\n"})
    assert result.returncode == 0, result.stderr
    assert "complexity ratchet" in result.stdout + result.stderr


def test_a_commit_that_adds_a_c_plus_function_is_refused(tmp_path: Path):
    repo = make_repo(tmp_path)
    result = write_and_commit(repo, {AUDITED: branches(12)})
    assert result.returncode != 0, "the hook let a new C+ function through"
    combined = result.stdout + result.stderr
    assert "complexity ratchet" in combined
    assert "FAIL" in combined


def test_a_refused_commit_leaves_the_repository_as_it_was(tmp_path: Path):
    repo = make_repo(tmp_path)
    head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    write_and_commit(repo, {AUDITED: branches(12)})
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == head_before
    # The snapshot commit object the hook builds is not on any ref; the index
    # still holds the staged change, and the file is still on disk.
    assert "components/registry/src/registry/x.py" in _git(repo, "diff", "--cached", "--name-only").stdout
    assert (repo / AUDITED).read_text().startswith("def f(a):")


def test_without_radon_the_hook_refuses_and_names_the_remedy(tmp_path: Path):
    repo = make_repo(tmp_path, radon=False)
    result = write_and_commit(repo, {AUDITED: "def f(a):\n    return a\n"})
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "radon" in combined and "--no-verify" in combined


def test_the_hook_compares_the_staged_snapshot():
    """CI checks a pushed commit against its parent; the hook hands it the same
    pair of refs, with a commit object built from the index standing in for the
    commit being made — so a change that lives only in the index is judged."""
    source = HOOK.read_text(encoding="utf-8")
    assert '--base "$parent"' in source and '--head "$staged"' in source
    assert "git write-tree" in source and "git commit-tree" in source
