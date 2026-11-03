"""Records wip-deploy owns: the refusal every API write to one returns.

The Registry imports the install's key file and reservations file into its
own records, marked `managed_by: "wip-deploy"` (services/key_import.py).
They change only when the file changes; the API refuses every write to them,
so the Console can never half-edit a deployer app's key, grant or
reservation and see the next import undo it.
"""

from typing import Final, Literal

from fastapi import Request
from fastapi.responses import JSONResponse

MANAGED_BY_DEPLOYER = "managed_by_deployer"
WIP_DEPLOY: Final[Literal["wip-deploy"]] = "wip-deploy"


class ManagedRecordRefused(Exception):
    """An API write to a record wip-deploy owns. Answers 409 with
    `{"detail": <message>, "error_code": "managed_by_deployer"}`; `detail`
    stays a string, as every other refusal's does."""

    def __init__(self, what: str, how: str) -> None:
        self.detail = f"{what} is managed by wip-deploy; {how}"
        super().__init__(self.detail)


async def managed_record_refused_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ManagedRecordRefused)
    return JSONResponse(
        status_code=409,
        content={"detail": exc.detail, "error_code": MANAGED_BY_DEPLOYER},
    )


def key_refusal(name: str) -> ManagedRecordRefused:
    return ManagedRecordRefused(
        f"API key '{name}'",
        "change it with wip-deploy (app-namespace add/remove, rotate-key, or the "
        "app's manifest), not through the API",
    )


def grant_refusal(namespace: str, subject: str) -> ManagedRecordRefused:
    return ManagedRecordRefused(
        f"The grant to '{subject}' on namespace '{namespace}'",
        "change it with wip-deploy (app-namespace add/remove, or the app's manifest)",
    )


def reservation_refusal(prefix: str, app: str) -> ManagedRecordRefused:
    return ManagedRecordRefused(
        f"The reservation of namespace '{prefix}' (app '{app}')",
        "change it with wip-deploy (app-namespace add/remove, or the app's manifest)",
    )
