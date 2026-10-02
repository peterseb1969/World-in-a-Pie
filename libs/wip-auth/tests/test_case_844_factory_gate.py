"""The factory-dependency gate and the direct admin dependency.

Provenance: CASE-844 — the Registry aliased the `require_admin` FACTORY
uncalled; FastAPI took the returned closure as the dependency's value and
the admin check never ran on 15 endpoints, privilege escalation included.
`assert_no_factory_dependencies` kills that wiring shape mechanically;
`require_admin_identity` makes the natural spelling the correct one.
"""

from collections.abc import Awaitable, Callable

import pytest
from fastapi import Depends, FastAPI, Request

from wip_auth import UserIdentity, require_admin, require_admin_identity
from wip_auth.identity import reset_current_identity, set_current_identity
from wip_auth.testing import (
    assert_no_factory_dependencies,
    collect_factory_dependencies,
)

# =========================================================================
# assert_no_factory_dependencies
# =========================================================================


def _local_factory() -> Callable[[Request], Awaitable[None]]:
    async def dependency(request: Request) -> None:
        return None

    return dependency


def test_uncalled_wip_auth_factory_is_caught():
    app = FastAPI()

    @app.get("/admin")
    async def admin_route(_gate=Depends(require_admin)):  # the CASE-844 shape
        return {}

    with pytest.raises(AssertionError) as exc:
        assert_no_factory_dependencies(app)
    assert "/admin" in str(exc.value)
    assert "require_admin" in str(exc.value)


def test_uncalled_local_factory_is_caught_by_annotation():
    """A service-local factory (not in wip-auth's list) is still caught:
    its return annotation is a Callable — the shape of 'returns a
    dependency' rather than 'is a dependency'."""
    app = FastAPI()

    @app.get("/guarded")
    async def guarded(_gate=Depends(_local_factory)):
        return {}

    offenders, examined = collect_factory_dependencies(app)
    assert examined >= 1
    assert any("_local_factory" in o for o in offenders)


def test_correct_wiring_passes():
    app = FastAPI()

    @app.get("/called-factory")
    async def called(_identity: UserIdentity = Depends(require_admin())):
        return {}

    @app.get("/direct")
    async def direct(_identity: UserIdentity = Depends(require_admin_identity)):
        return {}

    assert_no_factory_dependencies(app)


def test_nested_router_dependencies_are_walked():
    """The CASE-786 lesson applies here too: include_router output is not
    flat on current FastAPI, and a gate that only reads the top level
    examines nothing and passes green. The offender below is reachable
    only through a nested router."""
    from fastapi import APIRouter

    inner = APIRouter()

    @inner.get("/deep")
    async def deep(_gate=Depends(require_admin)):
        return {}

    outer = APIRouter(prefix="/api")
    outer.include_router(inner, prefix="/nested")
    app = FastAPI()
    app.include_router(outer)

    with pytest.raises(AssertionError) as exc:
        assert_no_factory_dependencies(app)
    assert "/api/nested/deep" in str(exc.value)


def test_zero_examined_dependencies_trips_the_canary():
    """A walk that examines nothing must not pass — same doctrine as the
    strictness gate's collected-count canary."""
    app = FastAPI()

    @app.get("/plain")
    async def plain():
        return {}

    with pytest.raises(AssertionError) as exc:
        assert_no_factory_dependencies(app)
    assert "ZERO" in str(exc.value)
    # The opt-out exists for apps that genuinely declare no dependencies.
    assert_no_factory_dependencies(app, expect_dependencies=False)


# =========================================================================
# require_admin_identity
# =========================================================================


def _request() -> Request:
    return Request(scope={"type": "http", "method": "GET", "path": "/", "headers": []})


@pytest.mark.asyncio
async def test_require_admin_identity_rejects_non_admin():
    from fastapi import HTTPException

    token = set_current_identity(
        UserIdentity(user_id="u1", username="u1", groups=["wip-viewers"], auth_method="api_key")
    )
    try:
        with pytest.raises(HTTPException) as exc:
            await require_admin_identity(_request())
        assert exc.value.status_code == 403
    finally:
        reset_current_identity(token)


@pytest.mark.asyncio
async def test_require_admin_identity_rejects_unauthenticated():
    from fastapi import HTTPException

    token = set_current_identity(None)
    try:
        with pytest.raises(HTTPException) as exc:
            await require_admin_identity(_request())
        assert exc.value.status_code == 401
    finally:
        reset_current_identity(token)


@pytest.mark.asyncio
async def test_require_admin_identity_passes_admin():
    token = set_current_identity(
        UserIdentity(user_id="a1", username="a1", groups=["wip-admins"], auth_method="api_key")
    )
    try:
        identity = await require_admin_identity(_request())
        assert identity.username == "a1"
    finally:
        reset_current_identity(token)
