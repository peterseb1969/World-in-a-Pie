"""API routers for the Registry service."""

from fastapi import APIRouter

from .namespaces import router as namespaces_router
from .id_pools import router as id_pools_router
from .entries import router as entries_router
from .synonyms import router as synonyms_router
from .search import router as search_router

# Create main API router
api_router = APIRouter(prefix="/api/registry")

# Include sub-routers
api_router.include_router(namespaces_router, prefix="/namespaces", tags=["Namespaces"])
api_router.include_router(id_pools_router, prefix="/id-pools", tags=["ID Pools"])
api_router.include_router(entries_router, prefix="/entries", tags=["Entries"])
api_router.include_router(synonyms_router, prefix="/synonyms", tags=["Synonyms"])
api_router.include_router(search_router, prefix="/search", tags=["Search"])

__all__ = ["api_router"]
