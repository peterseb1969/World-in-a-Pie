"""API module for the Def-Store service."""

from fastapi import APIRouter

from .terminologies import router as terminologies_router
from .terms import router as terms_router
from .import_export import router as import_export_router
from .validation import router as validation_router
from .audit import router as audit_router
from .ontology import router as ontology_router

# Aggregate all API routers
api_router = APIRouter(prefix="/api/def-store")
api_router.include_router(terminologies_router)
api_router.include_router(terms_router)
api_router.include_router(import_export_router)
api_router.include_router(validation_router)
api_router.include_router(audit_router)
api_router.include_router(ontology_router)

__all__ = ["api_router"]
