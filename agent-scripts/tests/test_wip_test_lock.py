"""CASE-742 — the per-clone test-infrastructure lock in wip-test.sh.

A clone's test containers use fixed database names; two concurrent suite
runs in one clone wipe each other's fixtures mid-flight and manufacture
phantom regressions (measured: four runs of one untouched suite scoring 82,
8, 2 and 104 failures). The lock serializes runs that touch the containers
and ONLY those — no-dep suites (this one included, which is what lets these
tests run under the lock without deadlocking on themselves). Since CASE-912
both the containers and the lock are per clone, so different clones never
wait on each other.

Exercised via the script's WIP_TEST_LOCK_PROBE hook: acquire per the normal
gating, report LOCK_ACQUIRED / LOCK_SKIPPED, release, exit — the same
subprocess-against-a-hook pattern as test_wake_rollover.py, so the tests
prove the real acquisition path without running any suite.
"""

import os
import subprocess
import sys
import threading
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "wip-test.sh"


def _probe(tmp_path: Path, target: str, **extra_env) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("WIP_TEST_LOCK_HELD", None)
    env["WIP_TEST_LOCK_PROBE"] = "1"
    env["WIP_TEST_LOCK_DIR"] = str(tmp_path / "infra.lock")
    env.update({k: str(v) for k, v in extra_env.items()})
    return subprocess.run(
        ["bash", str(SCRIPT), target], capture_output=True, text=True, env=env,
    )


def _plant_lock(tmp_path: Path, pid: int) -> Path:
    lock = tmp_path / "infra.lock"
    lock.mkdir()
    (lock / "owner").write_text(
        f"pid={pid}\nclone=/some/other/clone\ncomponent=document-store\n"
        f"since=2026-07-21 21:00:00\n"
    )
    return lock


class TestLockGating:
    def test_container_dep_target_acquires_and_releases(self, tmp_path):
        res = _probe(tmp_path, "registry")
        assert res.returncode == 0, res.stderr
        assert "LOCK_ACQUIRED" in res.stdout
        # Released on exit: the trap must clear it or every next run
        # would need the stale-break.
        assert not (tmp_path / "infra.lock").exists()

    def test_no_dep_target_skips_the_lock(self, tmp_path):
        # deployer touches no shared container; serializing it would cost
        # a 1000-test suite's runtime for nothing.
        res = _probe(tmp_path, "deployer")
        assert res.returncode == 0, res.stderr
        assert "LOCK_SKIPPED" in res.stdout
        assert not (tmp_path / "infra.lock").exists()

    def test_all_acquires(self, tmp_path):
        res = _probe(tmp_path, "all")
        assert res.returncode == 0, res.stderr
        assert "LOCK_ACQUIRED" in res.stdout

    def test_held_marker_skips_reacquisition(self, tmp_path):
        # A parent that already holds the lock exports WIP_TEST_LOCK_HELD;
        # a child invocation must not deadlock on its parent.
        res = _probe(tmp_path, "registry", WIP_TEST_LOCK_HELD="1")
        assert res.returncode == 0, res.stderr
        assert "LOCK_SKIPPED" in res.stdout


class TestContention:
    def test_live_holder_fails_fast_naming_the_owner(self, tmp_path):
        # A live PID that is certainly not ours: this test process's own
        # parent (pytest) — alive for the duration by construction.
        _plant_lock(tmp_path, os.getppid())

        res = _probe(tmp_path, "registry")

        assert res.returncode == 1
        assert "another test run holds" in res.stderr
        assert "clone=/some/other/clone" in res.stderr
        assert "component=document-store" in res.stderr
        assert "WIP_TEST_WAIT=1" in res.stderr
        # The holder's lock survives the refused attempt.
        assert (tmp_path / "infra.lock").exists()

    def test_dead_holder_is_broken_and_acquired(self, tmp_path):
        # A PID that is certainly dead: spawn-and-reap one.
        proc = subprocess.Popen(["true"])
        proc.wait()
        _plant_lock(tmp_path, proc.pid)

        res = _probe(tmp_path, "registry")

        assert res.returncode == 0, res.stderr
        assert "Stale test-infra lock" in res.stdout
        assert "LOCK_ACQUIRED" in res.stdout
        assert not (tmp_path / "infra.lock").exists()

    def test_bounded_wait_gives_up_with_the_owner_named(self, tmp_path):
        _plant_lock(tmp_path, os.getppid())

        res = _probe(
            tmp_path, "registry", WIP_TEST_WAIT="1", WIP_TEST_WAIT_TIMEOUT="5",
        )

        assert res.returncode == 1
        assert "waiting" in res.stdout
        assert "still locked after 5s" in res.stderr

    def test_wait_acquires_when_holder_dies(self, tmp_path):
        # Holder alive at first poll, dead before the timeout: the waiter
        # must break the stale lock and proceed rather than run the clock
        # out. The child must be reaped CONCURRENTLY with the probe — an
        # unreaped child is a zombie, and kill -0 succeeds on zombies, so a
        # wait() deferred to after the probe makes the holder look alive
        # for the whole timeout (found the hard way: first version of this
        # test did exactly that and "failed" against a correct script).
        holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(7)"])
        reaper = threading.Thread(target=holder.wait)
        reaper.start()
        _plant_lock(tmp_path, holder.pid)
        try:
            res = _probe(
                tmp_path, "registry",
                WIP_TEST_WAIT="1", WIP_TEST_WAIT_TIMEOUT="60",
            )
        finally:
            reaper.join()

        assert res.returncode == 0, res.stderr
        assert "LOCK_ACQUIRED" in res.stdout


class TestPerCloneLock:
    """The default lock path derives from the clone, so agents in separate
    clones on one machine never queue behind each other."""

    @staticmethod
    def _fake_clone(root: Path) -> Path:
        # The script needs only its own path and a venv activate file to
        # reach the probe; the probe exits before any suite runs.
        (root / "scripts").mkdir(parents=True)
        (root / ".venv" / "bin").mkdir(parents=True)
        (root / ".venv" / "bin" / "activate").write_text("")
        script = root / "scripts" / "wip-test.sh"
        script.write_text(SCRIPT.read_text())
        return script

    @staticmethod
    def _default_lock_dir(script: Path) -> str:
        env = dict(os.environ)
        env.pop("WIP_TEST_LOCK_HELD", None)
        env.pop("WIP_TEST_LOCK_DIR", None)
        env["WIP_TEST_LOCK_PROBE"] = "1"
        # A no-dep target: reports the path without acquiring the real lock.
        res = subprocess.run(
            ["bash", str(script), "deployer"], capture_output=True, text=True, env=env,
        )
        assert res.returncode == 0, res.stderr
        assert "LOCK_SKIPPED" in res.stdout
        (line,) = [ln for ln in res.stdout.splitlines() if ln.startswith("lock_dir=")]
        return line.removeprefix("lock_dir=")

    def test_two_clones_get_different_locks(self, tmp_path):
        a = self._default_lock_dir(self._fake_clone(tmp_path / "clone-a"))
        b = self._default_lock_dir(self._fake_clone(tmp_path / "clone-b"))
        assert a != b
        for path in (a, b):
            assert path.startswith("/tmp/wip-test-infra-")
            assert path != "/tmp/wip-test-infra.lock"

    def test_one_clone_keeps_its_lock(self, tmp_path):
        script = self._fake_clone(tmp_path / "clone")
        assert self._default_lock_dir(script) == self._default_lock_dir(script)
