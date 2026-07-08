"""Machine-readable auth declaration for service OpenAPI schemas.

Every WIP service gates its routes with Depends(require_api_key) — but a
runtime dependency emits nothing into the OpenAPI document, so a schema
generated from the app alone understates auth: tooling that builds clients
or docs from the schema (rather than reading the prose description) would
never know to send the X-API-Key header. One service fixed this locally
with an inline override; the other four silently kept the gap — the exact
partial-cross-cutting shape this shared helper exists to end.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from fastapi import FastAPI


def declare_api_key_security(app: "FastAPI") -> "FastAPI":
    """Install a cached ``app.openapi`` override that declares the
    X-API-Key requirement: ``components.securitySchemes.ApiKeyAuth``
    (type apiKey, header ``X-API-Key``) plus a global ``security``
    reference. Call once, after routers are included. Idempotent per
    app instance (the schema is built lazily and cached, matching
    FastAPI's own behavior)."""

    def _openapi():
        if app.openapi_schema:
            return app.openapi_schema
        from fastapi.openapi.utils import get_openapi

        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        schema.setdefault("components", {})["securitySchemes"] = {
            "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"}
        }
        schema["security"] = [{"ApiKeyAuth": []}]
        app.openapi_schema = schema
        return schema

    app.openapi = _openapi
    return app
