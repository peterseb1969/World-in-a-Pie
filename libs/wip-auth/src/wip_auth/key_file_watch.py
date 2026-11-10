"""Apply changes to rendered config files without a restart.

The deployer renders some configuration as files that services read: the
federated group policy, in the auth config directory several services
mount; and the declared API keys, which reach only the Registry (as
WIP_REGISTRY_KEY_FILE, read by its own key import) and installs or apps
whose manifests still declare WIP_AUTH_API_KEYS_FILE. Services used to read
them once at start, so every change meant a restart. A watcher re-reads the
file instead: a daemon thread compares the file's content with
what was last applied, and on a change hands it to an `apply` callback that
parses the whole file and swaps the result in one step.

A file that does not parse keeps the previous state. A change is applied
only once it parses completely, so a reader that catches a half-written file
retries on the next check; the same unparseable content seen on two
consecutive checks is logged CRITICAL. A missing file also keeps the previous
state: an unmounted file is not an instruction to drop everything.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable
from pathlib import Path

from .models import APIKeyRecord
from .providers.api_key import APIKeyProvider

logger = logging.getLogger("wip_auth.key_file_watch")


def content_hash(path: str | Path) -> str | None:
    """The file's content hash, or None when it cannot be read. Take it
    BEFORE reading the file to build a state, and hand it to the watcher as
    `applied`: a change that lands between the two reads then differs from
    the baseline and is applied on the next check."""
    return _content_hash(Path(path))


def _content_hash(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


class ConfigFileWatcher:
    """Calls `apply()` whenever the file at `path` changes.

    `apply` re-reads and parses the file itself and raises when it does not
    parse; it must leave the current state untouched in that case. `what`
    names the state in log lines ("config API keys"). `applied` is the
    content hash (``content_hash``) taken before the current state was read
    from the file: the state is not assumed to match the file as it is when
    the watcher starts, since the file may have changed while it was built.
    """

    def __init__(
        self,
        path: str | Path,
        apply: Callable[[], None],
        *,
        what: str,
        applied: str | None,
        interval_seconds: float = 10.0,
    ) -> None:
        self.path = Path(path)
        self.apply = apply
        self.what = what
        self.interval_seconds = interval_seconds
        self._applied = applied
        self._bad: str | None = None
        self._missing_logged = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def check_once(self) -> bool:
        """Apply the file if it changed. True when a new state was applied."""
        current = _content_hash(self.path)
        if current is None:
            if not self._missing_logged:
                logger.warning(
                    "%s is unreadable; keeping the current %s", self.path, self.what
                )
                self._missing_logged = True
            return False
        self._missing_logged = False
        if current == self._applied:
            self._bad = None
            return False
        try:
            self.apply()
        except Exception as e:  # any parse or validation failure keeps the old state
            if current == self._bad:
                logger.critical(
                    "%s does not parse (%s); keeping the previous %s",
                    self.path, e, self.what,
                )
            self._bad = current
            return False
        self._applied = current
        self._bad = None
        logger.info("Reloaded %s from %s", self.what, self.path)
        return True

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name=f"wip-auth-watch-{self.path.name}", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.check_once()
            except Exception:
                logger.exception("Config file check failed for %s", self.path)


class APIKeysFileWatcher(ConfigFileWatcher):
    """Re-applies `load()` to `provider` whenever the API key file changes."""

    def __init__(
        self,
        path: str | Path,
        load: Callable[[], list[APIKeyRecord]],
        provider: APIKeyProvider,
        *,
        applied: str | None,
        interval_seconds: float = 10.0,
    ) -> None:
        self.load = load
        self.provider = provider
        super().__init__(
            path,
            lambda: provider.reload_config_keys(load()),
            what="config API keys",
            applied=applied,
            interval_seconds=interval_seconds,
        )
