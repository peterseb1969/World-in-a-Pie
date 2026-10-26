"""Reload the config-file API keys when their file changes.

The deployer renders every declared key into one file
(WIP_AUTH_API_KEYS_FILE). Services used to read it once at start, so adding,
removing or rotating any declared key meant restarting every service that
reads the file. The watcher re-reads it instead: a daemon thread compares the
file's content with what was last applied, and on a change parses the whole
key configuration and swaps it into the provider in one step
(`APIKeyProvider.reload_config_keys`).

A file that does not parse keeps the previous key set. A change is applied
only once it parses completely, so a reader that catches a half-written file
retries on the next check; the same unparseable content seen on two
consecutive checks is logged CRITICAL. A missing file also keeps the
previous keys: an unmounted file is not an instruction to drop every key.
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


def _content_hash(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


class APIKeysFileWatcher:
    """Re-applies `load()` to `provider` whenever the file at `path` changes."""

    def __init__(
        self,
        path: str | Path,
        load: Callable[[], list[APIKeyRecord]],
        provider: APIKeyProvider,
        interval_seconds: float = 10.0,
    ) -> None:
        self.path = Path(path)
        self.load = load
        self.provider = provider
        self.interval_seconds = interval_seconds
        # The provider was built from the file as it is now.
        self._applied = _content_hash(self.path)
        self._bad: str | None = None
        self._missing_logged = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def check_once(self) -> bool:
        """Apply the file if it changed. True when a new key set was applied."""
        current = _content_hash(self.path)
        if current is None:
            if not self._missing_logged:
                logger.warning(
                    "API key file %s is unreadable; keeping the current keys", self.path
                )
                self._missing_logged = True
            return False
        self._missing_logged = False
        if current == self._applied:
            self._bad = None
            return False
        try:
            keys = self.load()
        except Exception as e:  # any parse or validation failure keeps the old set
            if current == self._bad:
                logger.critical(
                    "API key file %s does not parse (%s); keeping the previous "
                    "%d config key(s)",
                    self.path, e, len(self.provider.config_key_names()),
                )
            self._bad = current
            return False
        self.provider.reload_config_keys(keys)
        self._applied = current
        self._bad = None
        logger.info("Reloaded %d config API key(s) from %s", len(keys), self.path)
        return True

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="wip-auth-key-file-watch", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.check_once()
            except Exception:
                logger.exception("API key file check failed")
