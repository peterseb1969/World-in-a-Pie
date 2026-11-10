"""CASE-1198 — wip-test.sh stops a run that upstream has superseded.

Parallel clones push to one branch. A run that finishes against code another
clone already changed has to run again, so the wrapper fetches while pytest
runs and, when commits that arrived touch what the run depends on, stops
pytest and every process it started, and exits 75. `--overlap-check` asks
the same question before a push.

Exercised against throwaway git repos: a bare "upstream", the clone under
test, and a second clone that pushes. The wrapper runs from a copy inside
the clone under test, so its REPO_ROOT is that clone.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "scripts" / "wip-test.sh"
HELPER = REPO / "scripts" / "upstream_overlap.py"

sys.path.insert(0, str(REPO / "scripts"))
import upstream_overlap  # noqa: E402


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    ).stdout


def _commit(clone: Path, path: str, text: str, msg: str) -> None:
    target = clone / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    _git(clone, "add", path)
    _git(clone, "commit", "-q", "-m", msg)


def _repos(tmp_path: Path) -> tuple[Path, Path]:
    """(clone under test, other clone), both tracking a bare upstream."""
    upstream = tmp_path / "upstream.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "develop", str(upstream))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", "-q", str(upstream), str(seed))
    _git(seed, "checkout", "-q", "-b", "develop")
    _commit(seed, "components/fake/src/fake.py", "x = 1\n", "seed")
    _commit(seed, "components/fake/requirements.txt", "-e ../../libs/shared\n", "reqs")
    _commit(seed, "libs/shared/src/shared.py", "y = 1\n", "lib")
    _commit(seed, "components/other/src/other.py", "z = 1\n", "other")
    _git(seed, "push", "-q", "origin", "develop")
    mine, theirs = tmp_path / "mine", tmp_path / "theirs"
    for clone in (mine, theirs):
        _git(tmp_path, "clone", "-q", "-b", "develop", str(upstream), str(clone))
    return mine, theirs


def _push(theirs: Path, path: str) -> None:
    _commit(theirs, path, f"# {time.time()}\n", f"touch {path}")
    _git(theirs, "push", "-q", "origin", "develop")


def _check(clone: Path, *components: str, since: Path | None = None) -> subprocess.CompletedProcess:
    args = [sys.executable, str(HELPER), "check", str(clone), *components]
    if since is not None:
        args += ["--since", str(since)]
    return subprocess.run(args, capture_output=True, text=True)


class TestWatchPaths:
    def test_a_component_watches_itself_its_editable_libs_and_the_wrapper(self, tmp_path):
        mine, _ = _repos(tmp_path)
        paths = upstream_overlap.watch_paths(mine, "fake")
        assert "components/fake" in paths
        assert "libs/shared" in paths
        assert "scripts/wip-test.sh" in paths and "pyproject.toml" in paths
        assert "components/other" not in paths

    def test_all_watches_everything(self, tmp_path):
        mine, _ = _repos(tmp_path)
        assert upstream_overlap.watch_paths(mine, "all") == [""]

    def test_the_toolkit_watches_the_same_paths_under_either_spelling(self, tmp_path):
        # Its directory is WIP-Toolkit; its rules are keyed wip-toolkit. Under
        # the directory's spelling it used to watch only its own directory,
        # missing wip-archive and the four services its integration suite runs.
        mine, _ = _repos(tmp_path)
        (mine / "WIP-Toolkit").mkdir()
        lower = upstream_overlap.watch_paths(mine, "wip-toolkit")
        assert upstream_overlap.watch_paths(mine, "WIP-Toolkit") == lower
        assert {"WIP-Toolkit", "libs/wip-archive", "components/document-store"} <= set(lower)


class TestOverlapCheck:
    def test_nothing_came_in_is_clean(self, tmp_path):
        mine, _ = _repos(tmp_path)
        assert _check(mine, "fake").returncode == 0

    def test_a_commit_touching_the_component_overlaps(self, tmp_path):
        mine, theirs = _repos(tmp_path)
        _push(theirs, "components/fake/src/fake.py")
        res = _check(mine, "fake")
        assert res.returncode == 75, res.stdout + res.stderr
        assert res.stdout.startswith("superseded: upstream 1 commit(s) touch components/fake")

    def test_a_commit_touching_an_editable_lib_overlaps(self, tmp_path):
        mine, theirs = _repos(tmp_path)
        _push(theirs, "libs/shared/src/shared.py")
        assert _check(mine, "fake").returncode == 75

    def test_an_unrelated_commit_does_not(self, tmp_path):
        mine, theirs = _repos(tmp_path)
        _push(theirs, "components/other/src/other.py")
        assert _check(mine, "fake").returncode == 0

    def test_a_commit_touching_what_this_clone_changed_overlaps(self, tmp_path):
        mine, theirs = _repos(tmp_path)
        (mine / "components/other/src/other.py").write_text("z = 2\n")  # local edit
        _push(theirs, "components/other/src/other.py")
        assert _check(mine, "fake").returncode == 75

    def test_since_counts_only_what_arrived_after_the_snapshot(self, tmp_path):
        mine, theirs = _repos(tmp_path)
        _push(theirs, "components/fake/src/fake.py")  # before the run started
        state = tmp_path / "state.json"
        subprocess.run([sys.executable, str(HELPER), "snapshot", str(mine), str(state)], check=True)
        assert _check(mine, "fake", since=state).returncode == 0
        _push(theirs, "libs/shared/src/shared.py")  # during the run
        assert _check(mine, "fake", since=state).returncode == 75

    def test_a_failed_fetch_is_unknown_never_clean(self, tmp_path):
        mine, _ = _repos(tmp_path)
        _git(mine, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
        res = _check(mine, "fake")
        assert res.returncode == 76, res.stdout
        assert "unknown" in res.stdout


def _wrapper_clone(tmp_path: Path) -> tuple[Path, Path]:
    """A clone that can run the real wrapper on a fake component whose one
    test records its pid and sleeps."""
    mine, theirs = _repos(tmp_path)
    (mine / "scripts").mkdir()
    shutil.copy(WRAPPER, mine / "scripts" / "wip-test.sh")
    shutil.copy(HELPER, mine / "scripts" / "upstream_overlap.py")
    (mine / ".venv" / "bin").mkdir(parents=True)
    (mine / ".venv" / "bin" / "activate").write_text("# stub: pytest comes from PATH\n")
    tests = mine / "components" / "fake" / "tests"
    tests.mkdir(parents=True)
    (mine / "components" / "fake" / "requirements.txt").unlink()  # nothing to provision
    (tests / "test_slow.py").write_text(
        "import os, time\n"
        "def test_slow():\n"
        f"    open({str(tmp_path / 'pytest.pid')!r}, 'w').write(str(os.getpid()))\n"
        "    time.sleep(30)\n"
    )
    return mine, theirs


def _run_wrapper(mine: Path, **env) -> subprocess.Popen:
    return subprocess.Popen(
        ["bash", str(mine / "scripts" / "wip-test.sh"), "fake", "-q", "-p", "no:cacheprovider"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env={**os.environ, "PATH": f"{Path(sys.executable).parent}:{os.environ['PATH']}",
             "WIP_TEST_SUPERSEDE_INTERVAL": "1", **env},
    )


def _alive(pid: int) -> bool:
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout
    return bool(out.strip()) and not out.strip().startswith("Z")


class TestWrapperStopsASupersededRun:
    def test_an_overlapping_push_stops_pytest_and_exits_75(self, tmp_path):
        mine, theirs = _wrapper_clone(tmp_path)
        proc = _run_wrapper(mine)
        pid_file = tmp_path / "pytest.pid"
        deadline = time.time() + 20
        while not pid_file.exists() and time.time() < deadline:
            time.sleep(0.2)
        assert pid_file.exists(), proc.communicate(timeout=5)[0]
        _push(theirs, "components/fake/src/fake.py")
        out, _ = proc.communicate(timeout=25)
        assert proc.returncode == 75, out
        assert "superseded: upstream 1 commit(s) touch components/fake" in out
        assert not _alive(int(pid_file.read_text()))

    def test_supersede_0_runs_unwatched(self, tmp_path):
        mine, theirs = _wrapper_clone(tmp_path)
        (mine / "components/fake/tests/test_slow.py").write_text("def test_fast():\n    pass\n")
        _push(theirs, "components/fake/src/fake.py")
        proc = _run_wrapper(mine, WIP_TEST_SUPERSEDE="0")
        out, _ = proc.communicate(timeout=30)
        assert proc.returncode == 0, out
        assert "superseded" not in out

    def test_an_unrelated_push_lets_the_run_finish(self, tmp_path):
        mine, theirs = _wrapper_clone(tmp_path)
        (mine / "components/fake/tests/test_slow.py").write_text(
            "import time\ndef test_brief():\n    time.sleep(4)\n"
        )
        proc = _run_wrapper(mine)
        time.sleep(1.5)
        _push(theirs, "components/other/src/other.py")
        out, _ = proc.communicate(timeout=30)
        assert proc.returncode == 0, out


class TestWrapperStopsOnTerm:
    def test_a_terminated_wrapper_stops_pytest_with_the_watch_off(self, tmp_path):
        """A foreground pytest held off the wrapper's TERM trap until it
        finished; the run must stop now, watched or not."""
        mine, _ = _wrapper_clone(tmp_path)
        proc = _run_wrapper(mine, WIP_TEST_SUPERSEDE="0")
        pid_file = tmp_path / "pytest.pid"
        deadline = time.time() + 20
        while not pid_file.exists() and time.time() < deadline:
            time.sleep(0.2)
        assert pid_file.exists(), proc.communicate(timeout=5)[0]
        started = time.time()
        proc.terminate()
        proc.communicate(timeout=15)
        assert time.time() - started < 10  # not the test's 30 s
        assert proc.returncode == 143
        assert not _alive(int(pid_file.read_text()))
