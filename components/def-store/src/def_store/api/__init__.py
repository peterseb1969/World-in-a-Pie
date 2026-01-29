"""API module for the Def-Store service."""

from fastapi import APIRouter

from .terminologies import router as terminologies_router
from .terms import router as terms_router
from .import_export import router as import_export_router

# Aggregate all API routers
api_router = APIRouter(prefix="/api/def-store")
api_router.include_router(terminologies_router)
api_router.include_router(terms_router)
api_router.include_router(import_export_router)

__all__ = ["api_router"]
