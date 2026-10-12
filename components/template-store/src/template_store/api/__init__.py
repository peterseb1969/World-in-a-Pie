"""API module for the Template Store service."""

from fastapi import APIRouter

from .maintenance import router as maintenance_router
from .templates import router as templates_router

# Aggregate all API routers
api_router = APIRouter(prefix="/api/template-store")
api_router.include_router(templates_router)
api_router.include_router(maintenance_router)

__all__ = ["api_router"]
