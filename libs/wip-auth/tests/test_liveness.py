"""The liveness endpoint answers without credentials and without I/O.

k8s probes carry no credentials, and a liveness failure restarts the pod —
so /livez must be public by default and must not depend on anything but the
process serving requests.
"""

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from wip_auth import (
    APIKeyProvider,
    APIKeyRecord,
    create_auth_middleware,
    hash_api_key,
    install_liveness,
    require_api_key,
)


def _app() -> FastAPI:
    app = FastAPI()
    provider = APIKeyProvider([APIKeyRecord(name="test", key_hash=hash_api_key("secret"))])
    app.add_middleware(create_auth_middleware([provider]))
    install_liveness(app)

    @app.get("/protected", dependencies=[Depends(require_api_key)])
    async def protected():
        return {}

    return app


def test_livez_answers_without_credentials():
    client = TestClient(_app())
    assert client.get("/protected").status_code == 401  # auth is on
    resp = client.get("/livez")
    assert resp.status_code == 200
    assert resp.json() == {"status": "alive"}


def test_livez_answers_with_a_wrong_key():
    """Public paths skip provider iteration: a stale key on a probe is no 401."""
    client = TestClient(_app())
    assert client.get("/livez", headers={"X-API-Key": "wrong"}).status_code == 200


def test_livez_is_not_in_the_api_schema():
    assert "/livez" not in (_app().openapi().get("paths") or {})
