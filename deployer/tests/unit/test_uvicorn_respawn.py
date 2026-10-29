"""The dev launcher's respawn, run for real: uvicorn, a worker, a kill.

`uvicorn --reload` restarts its worker only when a watched file changes.
A dev stack whose document-store worker was OOM-killed sat "Up (unhealthy)"
for 45 minutes: the supervisor held the socket, the kernel accepted
connections nobody served, and the container never exited for its restart
policy. These tests start the launcher the dev renderer mounts, against a
tiny app, and kill its worker.

They need uvicorn and watchfiles importable (both are in the repo venv);
skipped otherwise. Each takes a few seconds: the reload supervisor's loop
wakes at least every five seconds, when the file watcher times out.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("uvicorn")
pytest.importorskip("watchfiles")

LAUNCHER = (
    Path(__file__).resolve().parents[2] / "src" / "wip_deploy" / "dev_runtime" / "uvicorn_respawn.py"
)

APP = '''
import os

async def app(scope, receive, send):
    if scope["type"] != "http":
        return
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": str(os.getpid()).encode()})
'''


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _get(port: int) -> str | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as r:
            return str(r.read().decode())
    except OSError:
        return None


def _wait_for(port: int, deadline_s: float, unless: str | None = None) -> str | None:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        body = _get(port)
        if body is not None and body != unless:
            return body
        time.sleep(0.2)
    return None


def _start(tmp_path: Path, app_src: str, min_uptime_s: float) -> tuple[subprocess.Popen[str], int, Path]:
    (tmp_path / "probe_app.py").write_text(app_src)
    # Run the launcher's main() with a short MIN_UPTIME_S, so the test does
    # not wait out the production threshold before killing the worker.
    runner = tmp_path / "run_launcher.py"
    runner.write_text(
        "import runpy, sys\n"
        f"ns = runpy.run_path({str(LAUNCHER)!r}, run_name='uvicorn_respawn')\n"
        # run_path returns a copy of the module globals; the live dict is the
        # one a function defined in the launcher closes over.
        f"ns['RespawningReload'].__next__.__globals__['MIN_UPTIME_S'] = {min_uptime_s}\n"
        "if __name__ == '__main__':\n"
        "    ns['main']()\n"
    )
    port = _free_port()
    log = tmp_path / "launcher.log"
    proc = subprocess.Popen(
        [sys.executable, str(runner), "probe_app:app", "--host", "127.0.0.1",
         "--port", str(port), "--reload", "--reload-dir", str(tmp_path)],
        cwd=tmp_path, stdout=log.open("w"), stderr=subprocess.STDOUT, text=True,
        env={**os.environ, "WATCHFILES_FORCE_POLLING": "1"},
    )
    return proc, port, log


def _stop(proc: subprocess.Popen[str]) -> None:
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_killed_worker_is_respawned(tmp_path: Path) -> None:
    proc, port, log = _start(tmp_path, APP, min_uptime_s=1.0)
    try:
        first = _wait_for(port, 15)
        assert first is not None, log.read_text()
        time.sleep(1.5)  # past MIN_UPTIME_S: this worker "ran for a while"
        os.kill(int(first), signal.SIGKILL)
        second = _wait_for(port, 15, unless=first)
        assert second is not None and second != first, log.read_text()
        assert proc.poll() is None, "the launcher itself must keep running"
        assert "Worker exited (code -9)" in log.read_text()
    finally:
        _stop(proc)


def test_worker_that_fails_at_start_waits_for_a_change(tmp_path: Path) -> None:
    # A syntax error mid-edit kills the worker at import. Respawning it
    # would spin; the launcher logs once and waits for the next file change,
    # as plain --reload does — and the fixed file then serves.
    proc, port, log = _start(tmp_path, "raise RuntimeError('mid-edit')\n", min_uptime_s=10.0)
    try:
        end = time.monotonic() + 15
        while "waiting for a file change" not in log.read_text() and time.monotonic() < end:
            time.sleep(0.2)
        assert "waiting for a file change" in log.read_text(), log.read_text()
        time.sleep(6)  # at least one more supervisor pass: no respawn loop
        assert log.read_text().count("Worker exited") == 1, log.read_text()
        (tmp_path / "probe_app.py").write_text(APP)
        assert _wait_for(port, 20) is not None, log.read_text()
    finally:
        _stop(proc)
