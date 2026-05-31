"""One-shot admin entrypoint: build the CompositeKeyClaim domain (CASE-427).

Destructive (strips losing duplicate synonyms, audited via logs), so it is an
explicit one-shot — NOT run at service startup. Run it once after deploying the
CASE-427 change, against the same MongoDB the registry uses:

    # inside the registry container (has MONGO_URI to its own mongo):
    python -m registry.admin_backfill_claims

It connects, initializes Beanie (creating the claims unique index on the empty
collection), runs backfill_claims() then reconcile_orphan_claims(), and prints
the summaries. Idempotent — safe to re-run.
"""

import asyncio
import logging
import os

from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient

from .models.composite_key_claim import CompositeKeyClaim
from .models.entry import RegistryEntry
from .services.claims import backfill_claims, reconcile_orphan_claims


async def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    db_name = os.getenv("DATABASE_NAME", "wip_registry")
    print(f"Connecting to MongoDB at {mongo_uri} (db={db_name})...")
    client: AsyncIOMotorClient = AsyncIOMotorClient(mongo_uri)
    await init_beanie(
        database=client[db_name],
        document_models=[RegistryEntry, CompositeKeyClaim],
    )
    print("Running CASE-427 claim backfill...")
    backfill_summary = await backfill_claims()
    print(f"Backfill: {backfill_summary}")
    reconcile_summary = await reconcile_orphan_claims()
    print(f"Reconcile: {reconcile_summary}")
    print("Done.")


if __name__ == "__main__":
    asyncio.run(_main())
