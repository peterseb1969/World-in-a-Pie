"""API module for the Template Store service."""

from fastapi import APIRouter

from .templates import router as templates_router

# Aggregate all API routers
api_router = APIRouter(prefix="/api/template-store")
api_router.include_router(templates_router)

__all__ = ["api_router"]
