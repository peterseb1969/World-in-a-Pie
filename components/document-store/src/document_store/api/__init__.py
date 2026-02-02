"""API module for the Document Store service."""

from fastapi import APIRouter

from .documents import router as documents_router
from .validation import router as validation_router
from .table_view import router as table_view_router
from .files import router as files_router

# Aggregate all API routers
api_router = APIRouter(prefix="/api/document-store")
api_router.include_router(documents_router)
api_router.include_router(validation_router)
api_router.include_router(table_view_router)
api_router.include_router(files_router)

__all__ = ["api_router"]
