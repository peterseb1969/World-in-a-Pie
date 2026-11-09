"""MongoDB credentials for WIP services.

MongoDB requires authentication; each service connects as its own user,
limited to the databases it needs (the deployer renders the users and
injects ``MONGO_USER`` / ``MONGO_PASSWORD``). The credentials are passed to
the client as arguments, never inside ``MONGO_URI``: every service prints
the URI at startup, so a password in it would land in the log.

Users live in the ``admin`` database (``authSource``). Without
``MONGO_USER`` the client connects anonymously — a MongoDB without
authentication, such as the test containers.

Every client is built with ``mongo_client_options`` and every probe pings
with ``mongo_ping``: a MongoDB that is unreachable, or reachable but not
answering, must fail a health or readiness probe inside the orchestrator's
probe budget (5 s by default), not after the driver's own 30 s server
selection or never.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

# A server is selected (and a connection opened) within this, or the
# operation fails. Startup tolerates a MongoDB that is not up yet by
# retrying (init_beanie_with_retry), so a short bound only makes it retry
# more often.
SERVER_SELECTION_TIMEOUT_MS = 2000
CONNECT_TIMEOUT_MS = 2000

# A probe's ping answers within this — under the 5 s probe budget, with
# room for the probe's other checks. Server selection bounds only finding a
# server; a selected server that accepts the connection and then does not
# answer is bounded here.
PING_TIMEOUT_S = 2.5


def mongo_credentials() -> dict[str, Any]:
    """Keyword arguments that authenticate a Motor/PyMongo client, or {}."""
    user = os.environ.get("MONGO_USER") or ""
    if not user:
        return {}
    return {
        "username": user,
        "password": os.environ.get("MONGO_PASSWORD", ""),
        "authSource": "admin",
    }


def mongo_client_options() -> dict[str, Any]:
    """Keyword arguments for every service's Motor/PyMongo client: the
    credentials plus bounded server-selection and connect timeouts."""
    return {
        **mongo_credentials(),
        "serverSelectionTimeoutMS": SERVER_SELECTION_TIMEOUT_MS,
        "connectTimeoutMS": CONNECT_TIMEOUT_MS,
    }


async def mongo_ping(client: Any, timeout: float | None = None) -> None:
    """Ping MongoDB for a health or readiness probe; raises when it does not
    answer within ``timeout`` (default PING_TIMEOUT_S; TimeoutError) or
    answers with an error."""
    await asyncio.wait_for(
        client.admin.command("ping"),
        timeout=PING_TIMEOUT_S if timeout is None else timeout,
    )


def mongo_identity() -> str:
    """The MongoDB user this process connects as, for log lines."""
    return os.environ.get("MONGO_USER") or "anonymous"
