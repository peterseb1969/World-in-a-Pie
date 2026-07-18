"""The shared X-API-Key OpenAPI declaration (declare_api_key_security).

Runtime Depends() enforcement emits nothing into the schema — this helper
is what makes the auth requirement machine-readable. All five services
call it; these tests pin the contract their schemas rely on.
"""

from fastapi import FastAPI

from wip_auth import declare_api_key_security


def _app() -> FastAPI:
    app = FastAPI(title="t", version="0.0.1", description="d")

    @app.get("/thing")
    async def thing():  # pragma: no cover - route body irrelevant
        return {}

    return app


def test_declares_scheme_and_global_security():
    app = declare_api_key_security(_app())
    schema = app.openapi()
    assert schema["components"]["securitySchemes"] == {
        "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"}
    }
    assert schema["security"] == [{"ApiKeyAuth": []}]


def test_schema_content_preserved():
    # The override must add security, not replace the generated document.
    schema = declare_api_key_security(_app()).openapi()
    assert schema["info"]["title"] == "t"
    assert "/thing" in schema["paths"]


def test_cached_like_fastapi_default():
    app = declare_api_key_security(_app())
    assert app.openapi() is app.openapi()
