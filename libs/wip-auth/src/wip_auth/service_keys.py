"""The keys a service presents when it calls another WIP service.

Each outbound key is read from a chain of environment variables: the variable
that names it (``REGISTRY_API_KEY`` for calls to the Registry), then
``API_KEY``. An empty value counts as unset, because an empty key is no
credential. When the whole chain is unset the key is missing, and that is
reported as missing. It is never stood in for by a constant.

A well-known default (``dev_master_key_for_testing``) used to stand in. On any
install with generated keys the called service answers it 401, and wip-auth
turned that 401 into "the Registry could not be reached", so the operator was
told a service was down when a variable was not set. Likewise an empty key
sent as ``X-API-Key: ``.

Two places check the chains:

- **Startup.** A service's lifespan calls :func:`require_service_keys` with
  every chain it calls with, so a service with a missing key refuses to start
  and names each variable, in dev and production alike.
- **The call itself.** :func:`service_api_key` raises
  :class:`MissingServiceKeyError` when the chain is unset; ``setup_auth``
  registers a handler that answers it as a 500 naming the variable. That
  covers a key a startup list did not name.
"""

from __future__ import annotations

import os
from collections.abc import Iterable

# The chains wip-auth itself calls with, for services to include in their
# startup check. Each names the variable for one callee, then API_KEY.
REGISTRY_KEY: tuple[str, ...] = ("REGISTRY_API_KEY", "API_KEY")
DEF_STORE_KEY: tuple[str, ...] = ("DEF_STORE_API_KEY", "API_KEY")
TEMPLATE_STORE_KEY: tuple[str, ...] = ("TEMPLATE_STORE_API_KEY", "API_KEY")
DOCUMENT_STORE_KEY: tuple[str, ...] = ("DOCUMENT_STORE_API_KEY", "API_KEY")
# The key a service presents to the Registry's permission doors: its own
# service key. wip-auth copies MASTER_API_KEY or API_KEY into
# WIP_AUTH_LEGACY_API_KEY at import when that is unset.
PERMISSION_KEY: tuple[str, ...] = ("WIP_AUTH_LEGACY_API_KEY", "API_KEY")
# The key setup_key_sync polls the Registry with when none is passed: the
# Registry key, else the service's own key under any of its names.
KEY_SYNC_KEY: tuple[str, ...] = (
    "REGISTRY_API_KEY", "WIP_AUTH_LEGACY_API_KEY", "API_KEY", "MASTER_API_KEY",
)


class MissingServiceKeyError(RuntimeError):
    """No variable in a chain holds a key: the service cannot call its callee.

    A configuration error, not an outage, so it is deliberately not a
    ``DependencyUnavailableError``: nothing was asked, and retrying does not
    help. ``names`` is the chain, the first name being the one to set.
    """

    def __init__(self, names: tuple[str, ...]) -> None:
        self.names = names
        super().__init__(_missing_message(names))


def _missing_message(names: tuple[str, ...]) -> str:
    if len(names) == 1:
        return f"{names[0]} is not set"
    return f"{names[0]} is not set (nor {', '.join(names[1:])})"


def service_api_key(*names: str) -> str:
    """The first non-empty value among the variables ``names``.

    Raises :class:`MissingServiceKeyError` when every one is unset or empty.
    """
    if not names:
        raise ValueError("service_api_key needs at least one variable name")
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    raise MissingServiceKeyError(tuple(names))


def missing_service_keys(chains: Iterable[tuple[str, ...]]) -> list[tuple[str, ...]]:
    """The chains in ``chains`` that hold no key, each listed once, in order."""
    missing: list[tuple[str, ...]] = []
    for chain in chains:
        if chain in missing:
            continue
        if not any(os.getenv(name) for name in chain):
            missing.append(chain)
    return missing


def require_service_keys(chains: Iterable[tuple[str, ...]], *, service: str) -> None:
    """Refuse to start when any chain holds no key, naming every missing one.

    Called from a service's lifespan. Raises ``RuntimeError``, which makes
    the ASGI server report the failed startup and exit.
    """
    missing = missing_service_keys(chains)
    if not missing:
        return
    lines = "\n".join(f"  - {_missing_message(chain)}" for chain in missing)
    raise RuntimeError(
        f"{service} cannot start: it has no key for the services it calls.\n"
        f"{lines}\n"
        "Set each named variable to the key the called service accepts "
        "(wip-deploy renders them all)."
    )
