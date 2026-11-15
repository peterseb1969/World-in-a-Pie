"""A MongoDB probe answers inside the orchestrator's probe budget.

mongo_ping is bounded by PING_TIMEOUT_S and server selection by
SERVER_SELECTION_TIMEOUT_MS so that a health or readiness probe fails
inside the budget the deployer renders for every service (its health-check
spec's timeout_seconds), rather than being killed by it. Nothing else ties
the two numbers together: the probe tests override the constant to stay
fast, so this pins the real values against the deployer's default.
"""

import re
from pathlib import Path

from wip_auth import mongo

REPO = Path(__file__).resolve().parents[3]
COMPONENT_SPEC = REPO / "deployer" / "src" / "wip_deploy" / "spec" / "component.py"


def _probe_budget_seconds() -> int:
    text = COMPONENT_SPEC.read_text()
    spec = text[text.index("class HealthcheckSpec"):]
    match = re.search(r"timeout_seconds: int = Field\(default=(\d+)", spec)
    assert match, "HealthcheckSpec.timeout_seconds not found — update this test"
    return int(match.group(1))


def test_the_ping_and_server_selection_fit_the_probe_budget():
    budget = _probe_budget_seconds()
    assert budget > mongo.PING_TIMEOUT_S
    assert budget > mongo.SERVER_SELECTION_TIMEOUT_MS / 1000
    assert budget > mongo.CONNECT_TIMEOUT_MS / 1000


def test_a_ping_waits_the_real_bound_by_default(monkeypatch):
    # mongo_ping uses PING_TIMEOUT_S when no timeout is passed.
    import asyncio

    seen = {}

    async def fake_wait_for(coro, timeout):
        seen["timeout"] = timeout
        coro.close()

    class Admin:
        async def command(self, name):  # pragma: no cover - never awaited
            return {"ok": 1}

    class Client:
        admin = Admin()

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    asyncio.run(mongo.mongo_ping(Client()))
    assert seen["timeout"] == mongo.PING_TIMEOUT_S
