"""uvicorn with hot reload that also survives a dead worker (dev target only).

`uvicorn --reload` runs the app in a worker process under a supervisor that
restarts the worker when a watched file changes, and at no other time. When
the worker dies on its own (OOM-killed, a crash), the supervisor keeps the
listening socket and stays asleep: the kernel goes on accepting connections
nobody serves, every request hangs, and because the container's main process
is still alive its restart policy never fires. A dev stack sat "Up
(unhealthy)" for 45 minutes that way.

This launcher is uvicorn's own CLI with one change to its reload
supervisor: each pass of the supervisor loop (at least every few seconds —
the file watcher yields on a timeout) also checks that the worker is alive.

- A worker that ran for a while and died is respawned, with a log line
  naming its exit code.
- A worker that dies within MIN_UPTIME_S of starting failed to start (a
  syntax error mid-edit, a missing setting). Respawning it would only spin,
  so the launcher waits for the next file change instead — exactly what
  plain `--reload` does today.

Usage is uvicorn's: `python uvicorn_respawn.py <app> [uvicorn options] --reload`.
"""

from __future__ import annotations

import importlib
import logging
import time

from uvicorn.supervisors import ChangeReload

logger = logging.getLogger("uvicorn.error")

MIN_UPTIME_S = 10.0


class RespawningReload(ChangeReload):  # type: ignore[misc,valid-type]
    _started_at = 0.0
    _waiting_for_change = False

    def startup(self) -> None:
        super().startup()
        self._mark_started()

    def restart(self) -> None:
        super().restart()
        self._mark_started()

    def _mark_started(self) -> None:
        self._started_at = time.monotonic()
        self._waiting_for_change = False

    def __next__(self) -> list[object] | None:
        changes: list[object] | None = super().__next__()
        if (
            changes
            or self._waiting_for_change
            or self.should_exit.is_set()
            or self.process.is_alive()
        ):
            return changes
        code = self.process.exitcode
        uptime = time.monotonic() - self._started_at
        if uptime < MIN_UPTIME_S:
            logger.error(
                "Worker exited (code %s) %.1fs after starting; waiting for a file change.",
                code, uptime,
            )
            self._waiting_for_change = True
            return changes
        logger.error("Worker exited (code %s) after %.0fs; restarting it.", code, uptime)
        self.restart()
        return changes


def main() -> None:
    # uvicorn.run builds the reload supervisor from the name `ChangeReload`
    # in the uvicorn.main module; replacing that binding keeps every CLI
    # option exactly as uvicorn parses it. (`uvicorn.main` the attribute is
    # the click command, hence the module import.)
    uvicorn_main = importlib.import_module("uvicorn.main")
    if getattr(uvicorn_main, "ChangeReload", None) is not ChangeReload:
        raise SystemExit(
            "uvicorn_respawn: uvicorn.main no longer binds ChangeReload; "
            "this launcher needs updating for the installed uvicorn."
        )
    uvicorn_main.ChangeReload = RespawningReload  # type: ignore[attr-defined]
    uvicorn_main.main(prog_name="uvicorn")


if __name__ == "__main__":
    main()
