"""The liveness endpoint: is the process alive and serving HTTP?

Liveness and readiness are different questions. A readiness check may fail
while a dependency (MongoDB, PostgreSQL) is down, and k8s then only stops
routing to the pod. A liveness failure restarts the pod, which cannot bring a
dependency back: probing liveness on a dependency check turns an outage into
a restart loop. So the liveness endpoint does no I/O at all — it answers as
long as the event loop serves requests.

The path is public by default (see middleware._DEFAULT_PUBLIC_PATHS): probes
carry no credentials.
"""

from typing import Any

LIVENESS_PATH = "/livez"


def install_liveness(app: Any) -> None:
    """Serve GET /livez on `app` (FastAPI): 200, no I/O, no auth."""

    async def livez() -> dict[str, str]:
        return {"status": "alive"}

    app.add_api_route(LIVENESS_PATH, livez, methods=["GET"], include_in_schema=False)
