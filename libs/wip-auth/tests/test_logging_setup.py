"""configure_logging: a uvicorn service's INFO lines reach the log, at the
level WIP_LOG_LEVEL names, without httpx's line per request."""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest

from wip_auth import configure_logging
from wip_auth.logging_setup import LOG_FORMAT


@pytest.fixture
def bare_root() -> Iterator[logging.Logger]:
    """The root logger as `uvicorn module:app` leaves it: no handler, WARNING.

    pytest attaches its capture handlers to the root around each test's
    body, after fixtures run, so the test strips them with `_strip`; the
    teardown puts back what was there."""
    root = logging.getLogger()
    saved_level = root.level
    saved_quiet = {n: logging.getLogger(n).level for n in ("httpx", "httpcore")}
    for name in saved_quiet:
        logging.getLogger(name).setLevel(logging.NOTSET)
    yield root
    root.setLevel(saved_level)
    for name, level in saved_quiet.items():
        logging.getLogger(name).setLevel(level)


def _strip(root: logging.Logger) -> list[logging.Handler]:
    """Remove every root handler (pytest's included) and return them."""
    removed = root.handlers[:]
    root.handlers = []
    root.setLevel(logging.WARNING)
    return removed


def _configure(root: logging.Logger, *args: int) -> list[logging.Handler]:
    """configure_logging on a bare root; returns the handlers it added, then
    puts back the root's own (pytest's), so no test leaves a handler behind."""
    removed = _strip(root)
    try:
        configure_logging(*args)
        return root.handlers[:]
    finally:
        root.handlers = removed


def test_info_reaches_the_root_handler_in_the_shared_format(bare_root, monkeypatch) -> None:
    monkeypatch.delenv("WIP_LOG_LEVEL", raising=False)
    (handler,) = _configure(bare_root)
    assert bare_root.level == logging.INFO
    assert handler.formatter is not None
    assert handler.formatter._fmt == LOG_FORMAT
    assert logging.getLogger("registry.anything").isEnabledFor(logging.INFO)


def test_httpx_per_request_lines_stay_out(bare_root, monkeypatch) -> None:
    monkeypatch.delenv("WIP_LOG_LEVEL", raising=False)
    _configure(bare_root)
    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)
    assert not logging.getLogger("httpcore").isEnabledFor(logging.INFO)
    assert logging.getLogger("httpx").isEnabledFor(logging.WARNING)


@pytest.mark.parametrize(
    ("env", "default", "expected"),
    [
        ("debug", logging.INFO, logging.DEBUG),
        ("ERROR", logging.INFO, logging.ERROR),
        ("", logging.DEBUG, logging.DEBUG),
        ("loud", logging.INFO, logging.INFO),  # not a level: the default, with a warning
    ],
)
def test_the_level_comes_from_the_environment(bare_root, monkeypatch, env, default, expected) -> None:
    monkeypatch.setenv("WIP_LOG_LEVEL", env)
    _configure(bare_root, default)
    assert bare_root.level == expected


def test_httpx_follows_a_level_above_warning(bare_root, monkeypatch) -> None:
    monkeypatch.setenv("WIP_LOG_LEVEL", "ERROR")
    _configure(bare_root)
    assert not logging.getLogger("httpx").isEnabledFor(logging.WARNING)


def test_a_configured_root_is_left_alone(bare_root, monkeypatch) -> None:
    """A test runner's capture or a host's own configuration is kept, and a
    second call adds no second handler."""
    monkeypatch.setenv("WIP_LOG_LEVEL", "DEBUG")
    removed = _strip(bare_root)
    own = logging.NullHandler()
    bare_root.addHandler(own)
    try:
        configure_logging()
        configure_logging()
        assert bare_root.handlers == [own]
        assert bare_root.level == logging.WARNING
    finally:
        bare_root.handlers = removed
