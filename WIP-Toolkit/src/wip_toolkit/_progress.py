"""Shared progress callback plumbing for export/import orchestrators.

A misbehaving observer must never break a long-running export or import.
:func:`emit` is the single entrypoint for invoking a caller-supplied
:class:`~wip_toolkit.models.ProgressEvent` callback; any exception raised
inside the callback is logged to stderr and swallowed.
"""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console

from .models import ProgressEvent

ProgressCallback = Callable[[ProgressEvent], None]

_console = Console(stderr=True)


def emit(callback: ProgressCallback | None, event: ProgressEvent) -> None:
    """Invoke a progress callback, swallowing any exception it raises.

    Designed for use by export/import orchestrators that need to surface
    progress without trusting the observer. A broken callback (e.g. an SSE
    queue that has been closed) must not abort the underlying operation.
    """
    if callback is None:
        return
    try:
        callback(event)
    except Exception as e:  # pragma: no cover - defensive
        _console.print(
            f"  [yellow]progress callback raised {type(e).__name__}: {e}[/yellow]"
        )
