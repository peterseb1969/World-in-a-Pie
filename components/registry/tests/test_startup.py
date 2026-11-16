"""The Registry's startup, run through the real lifespan against the test
database.

Startup ensures the 'wip' namespace (recording that the bootstrap created
it) and its three default group grants, and runs the stored-record
maintenance: a namespace stored before namespace ids gets one, and an
orphaned grant with no namespace id is deleted. Nothing else in the suite
runs the lifespan, so without this file a change that broke startup or
dropped a step would pass every test.
"""

import pytest

from registry import main as registry_main
from registry.models.grant import NamespaceGrant
from registry.models.namespace import Namespace


@pytest.mark.asyncio
async def test_startup_ensures_wip_and_runs_the_maintenance(client, monkeypatch):
    # The fixture's database, with what startup has to put right.
    await NamespaceGrant.delete_all()
    await Namespace.find({"prefix": "wip"}).delete()
    await Namespace.get_motor_collection().insert_one({
        "prefix": "legacy-startup", "description": "stored before namespace ids",
        "isolation_mode": "open", "status": "active", "id_config": {},
    })
    await NamespaceGrant(
        namespace="legacy-startup", subject="team-orphan", subject_type="group",
        permission="read", orphaned=True, granted_by="test",
    ).create()
    # No NATS in the test: startup goes on without the bus.
    monkeypatch.setattr(registry_main.settings, "NATS_URL", "")

    async with registry_main.lifespan(registry_main.app):
        wip = await Namespace.find_one({"prefix": "wip"})
        assert wip is not None
        assert wip.namespace_id and wip.created_by == "system:bootstrap"
        grants = await NamespaceGrant.find({"namespace": "wip"}).to_list()
        assert sorted((g.subject, g.permission, g.granted_by) for g in grants) == [
            (registry_main.ADMIN_GROUP, "admin", "system:bootstrap"),
            ("wip-editors", "write", "system:bootstrap"),
            ("wip-viewers", "read", "system:bootstrap"),
        ]

        legacy = await Namespace.find_one({"prefix": "legacy-startup"})
        assert legacy.namespace_id
        assert await NamespaceGrant.find_one({"subject": "team-orphan"}) is None

    # A second start changes nothing: one 'wip', the same grants, and the id
    # the first start gave stays.
    async with registry_main.lifespan(registry_main.app):
        assert await Namespace.find({"prefix": "wip"}).count() == 1
        assert await NamespaceGrant.find({"namespace": "wip"}).count() == 3
        again = await Namespace.find_one({"prefix": "legacy-startup"})
        assert again.namespace_id == legacy.namespace_id
