"""SecretBackend protocol + in-memory value container.

A SecretBackend persists named secrets. `get_or_generate` returns the
existing value if one is already stored; otherwise it calls the generator,
caches the new value in memory, and marks it for persistence on the next
`persist()` call. This lifecycle is critical:

  - First install: generate + persist. Database volumes initialize with
    the fresh value.
  - Re-install: read existing. No regeneration — the database on disk
    still expects the old password.
  - Rotate: explicit `remove()` then `get_or_generate()` generates anew.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class SecretBackend(Protocol):
    """Protocol every secret backend implements.

    Implementations MUST:
      - Cache values in memory so repeated `get_or_generate` for the
        same name returns the same value (no duplicate generation).
      - Preserve existing values on re-invocation; generate only when
        the value is truly absent.
      - Persist new values only when `persist()` is called explicitly;
        this gives callers atomic control.
    """

    def get_or_generate(
        self, name: str, generator: Callable[[], str]
    ) -> str: ...

    def persist(self) -> None: ...

    def list_names(self) -> list[str]: ...

    def remove(self, name: str) -> None: ...


# ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ResolvedSecrets:
    """Name → value map of every secret a deployment needs.

    Returned by the secrets orchestrator. Immutable so renderers can't
    accidentally mutate the set.
    """

    values: dict[str, str]

    def get(self, name: str) -> str:
        """Look up a secret by name. Raises KeyError if absent — which
        indicates the orchestrator didn't collect this name, not that
        the backend doesn't have it."""
        try:
            return self.values[name]
        except KeyError as e:
            raise KeyError(
                f"secret {name!r} not in ResolvedSecrets — was it collected?"
            ) from e

    def try_get(self, name: str) -> str | None:
        """Look up a secret, returning None if absent. Used for optional
        env vars whose source secret may not have been provisioned."""
        return self.values.get(name)

    def names(self) -> list[str]:
        return sorted(self.values.keys())
