"""Middleware to reject requests with undeclared query parameters.

FastAPI silently ignores unknown query parameters. This middleware
inspects each request's query params against the matched route's
endpoint signature and returns 422 for any unknown params.
"""

import inspect
import logging
from typing import Set

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Match

logger = logging.getLogger(__name__)

# Query param names that are always allowed (FastAPI/OpenAPI internals)
_GLOBAL_ALLOW = frozenset()

# Cache: endpoint function id → declared query param names
_DECLARED_CACHE: dict[int, frozenset[str]] = {}


def _get_declared_query_params(endpoint) -> frozenset[str]:
    """Extract declared query parameter names from an endpoint's signature.

    Results are cached per endpoint function (signatures never change at runtime).
    """
    key = id(endpoint)
    if key in _DECLARED_CACHE:
        return _DECLARED_CACHE[key]

    from fastapi.params import (
        Depends as DependsClass,
        Query as QueryClass,
        Path as PathClass,
        Body as BodyClass,
        Header as HeaderClass,
        Cookie as CookieClass,
        File as FileClass,
        Form as FormClass,
    )

    # All non-query FastAPI parameter types
    _NON_QUERY = (DependsClass, PathClass, BodyClass, HeaderClass, CookieClass, FileClass, FormClass)

    sig = inspect.signature(endpoint)
    declared: set[str] = set()

    for name, param in sig.parameters.items():
        default = param.default

        # Skip non-query params (Depends, Path, Body, Header, Cookie, File, Form)
        if isinstance(default, _NON_QUERY):
            continue

        # Explicit Query() params
        if isinstance(default, QueryClass):
            alias = getattr(default, "alias", None)
            declared.add(alias or name)
            continue

        # Plain typed params with defaults are query params in FastAPI
        if param.default is not inspect.Parameter.empty:
            declared.add(name)

    result = frozenset(declared)
    _DECLARED_CACHE[key] = result
    return result


class RejectUnknownQueryParamsMiddleware(BaseHTTPMiddleware):
    """Rejects requests containing undeclared query parameters.

    For each incoming request:
    1. Match against the app's routes to find the endpoint
    2. Inspect the endpoint's signature for declared query params
    3. Compare against actual query params in the request
    4. Return 422 with details if unknown params are found

    Skips non-matched routes (404s), docs endpoints, and health checks.
    """

    # Paths to skip (docs, health, OpenAPI schema)
    SKIP_PATHS = frozenset({"/docs", "/redoc", "/openapi.json", "/health", "/ready", "/"})

    async def dispatch(self, request: Request, call_next):
        # Skip if no query params
        if not request.query_params:
            return await call_next(request)

        # Skip docs/health paths
        if request.url.path in self.SKIP_PATHS:
            return await call_next(request)

        # Find the matching route
        app: FastAPI = request.app
        matched_route = None
        for route in app.routes:
            match, _ = route.matches(request.scope)
            if match == Match.FULL:
                matched_route = route
                break

        if not matched_route:
            # No route matched — let FastAPI handle the 404
            return await call_next(request)

        # Get the endpoint function
        endpoint = getattr(matched_route, "endpoint", None)
        if not endpoint:
            return await call_next(request)

        # Build set of declared query param names (cached per endpoint)
        declared = _get_declared_query_params(endpoint) | _GLOBAL_ALLOW

        # Compare with actual query params
        actual = set(request.query_params.keys())
        unknown = actual - declared

        if unknown:
            return JSONResponse(
                status_code=422,
                content={
                    "detail": [
                        {
                            "type": "unknown_query_param",
                            "loc": ["query", name],
                            "msg": f"Unknown query parameter: '{name}'",
                            "input": request.query_params[name],
                        }
                        for name in sorted(unknown)
                    ]
                },
            )

        return await call_next(request)
