"""Namespace reservations: listing both sources, releasing runtime ones.

Runtime reservations are made with a runtime API key (POST /api-keys with
`reserve`, or PUT /api-keys/{name}/namespaces/{prefix}). These endpoints let
a platform admin see every reservation, from the install's file and the
Registry, and release a runtime one explicitly, for instance one kept when
its key was revoked because the namespace already existed.
"""

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..models.reservation import RuntimeReservation
from ..services.auth import require_admin_key
from ..services.reservation_store import list_reservations
from ..services.reservations import ReservationsUnavailable, load_reservations

logger = logging.getLogger("registry.reservations")

router = APIRouter()


class ReservationResponse(BaseModel):
    prefix: str
    app: str = Field(description="The owning app (file) or runtime API key (runtime)")
    source: Literal["file", "runtime"] = Field(
        description="'file': the install's reservations, managed by wip-deploy. "
        "'runtime': made through the Registry's API with a runtime API key."
    )
    settings: dict[str, Any] = Field(description="The settings a create applies")
    namespace_exists: bool
    conflict: str | None = Field(
        default=None,
        description="Set when both sources reserve the prefix: the namespace "
        "upsert refuses it (409) until one of the two is removed.",
    )


def _unavailable(e: ReservationsUnavailable) -> HTTPException:
    return HTTPException(status_code=503, detail=f"Namespace reservations are unavailable: {e}")


@router.get("", response_model=list[ReservationResponse], summary="List namespace reservations")
async def get_reservations(
    source: Literal["file", "runtime"] | None = Query(
        None, description="Only reservations from this source"
    ),
    _admin: str = Depends(require_admin_key),
) -> list[ReservationResponse]:
    """Every namespace reservation, from the install's file and the Registry,
    sorted by prefix. A prefix both sources reserve appears twice, with
    `conflict` set on both."""
    try:
        views = await list_reservations()
    except ReservationsUnavailable as e:
        raise _unavailable(e) from e
    return [
        ReservationResponse(**v.__dict__)
        for v in views
        if source is None or v.source == source
    ]


@router.delete("/{prefix}", summary="Release a runtime namespace reservation")
async def release_reservation(
    prefix: str,
    _admin: str = Depends(require_admin_key),
) -> dict:
    """Release the runtime reservation of `prefix`, whether or not the
    namespace exists. The namespace itself is never touched. A reservation
    from the install's file is released through wip-deploy, not here."""
    doc = await RuntimeReservation.find_one(RuntimeReservation.prefix == prefix)
    if doc is None:
        try:
            in_file = prefix in load_reservations()
        except ReservationsUnavailable as e:
            raise _unavailable(e) from e
        if in_file:
            raise HTTPException(
                status_code=400,
                detail=f"namespace '{prefix}' is reserved in the install's reservations; "
                "wip-deploy manages it",
            )
        raise HTTPException(status_code=404, detail=f"no runtime reservation of '{prefix}'")
    await doc.delete()
    logger.info("Released runtime reservation of %s (key %s)", prefix, doc.app)
    return {"status": "released", "prefix": prefix, "app": doc.app}
