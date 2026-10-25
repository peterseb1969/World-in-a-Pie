"""MongoDB credentials for WIP services.

MongoDB requires authentication; each service connects as its own user,
limited to the databases it needs (the deployer renders the users and
injects ``MONGO_USER`` / ``MONGO_PASSWORD``). The credentials are passed to
the client as arguments, never inside ``MONGO_URI``: every service prints
the URI at startup, so a password in it would land in the log.

Users live in the ``admin`` database (``authSource``). Without
``MONGO_USER`` the client connects anonymously — a MongoDB without
authentication, such as the test containers.
"""

from __future__ import annotations

import os
from typing import Any


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


def mongo_identity() -> str:
    """The MongoDB user this process connects as, for log lines."""
    return os.environ.get("MONGO_USER") or "anonymous"
