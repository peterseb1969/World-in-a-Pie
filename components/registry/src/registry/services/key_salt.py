"""The salt the Registry hashes runtime API keys with, kept for the stack.

A stored key hash is bcrypt(sha256("<salt>:<key>")). Every runtime key (the
Registry's records: Console keys, imported key-file keys, restored keys) is
hashed under one salt, and every service must verify under the same one. The
Registry keeps it in its own database and hands it to the services with key
sync, so no deployer setting has to agree across services and a restore can
carry it.

- **First start.** When no salt is stored, the Registry stores one before it
  imports or serves anything: the configured `WIP_AUTH_API_KEY_HASH_SALT`
  (default `wip_auth_salt`) when `api_keys` already holds records, so no
  existing hash breaks; a fresh random value when it holds none, which is a
  new install. The write is insert-once, so Registry replicas starting
  together agree on one value.
- **Never changed afterwards,** except by a system restore onto a fresh stack
  (system_backup), which adopts the archive's salt: the archive's hashes were
  made under it.
- **Not a secret against a leaked backup,** which carries it. Its value is
  that stacks do not share one public constant.

Config keys are not affected: each process hashes and verifies its own
config keys under its configured salt.
"""

import asyncio
import logging
import secrets
from datetime import UTC, datetime
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from wip_auth import APIKeyProvider, get_auth_config

from ..models.api_key import StoredAPIKey

logger = logging.getLogger("registry.key_salt")

COLLECTION = "key_hash_salt"
_DOC_ID = "runtime"

# The stored value, once read; None until ensure() or refresh() ran.
_current: str | None = None

# Held while a key is hashed under the current salt and stored, and while a
# restore re-checks that the stack is fresh and adopts another salt: a key
# created in between would be stored under a salt that is then replaced, and
# never authenticate.
write_lock = asyncio.Lock()


def _collection() -> Any:
    return StoredAPIKey.get_motor_collection().database[COLLECTION]


def current_salt() -> str:
    """The salt runtime keys are hashed and verified under.

    Before the stored value has been read (only in code paths that run
    without the Registry's startup, such as tests) it is the configured salt,
    which is what every stack used before the Registry kept its own.
    """
    return _current if _current is not None else get_auth_config().api_key_hash_salt


async def ensure() -> str:
    """Read the stored salt, storing it first when there is none."""
    global _current
    coll = _collection()
    doc = await coll.find_one({"_id": _DOC_ID})
    if doc is None:
        if await StoredAPIKey.find_one({}) is not None:
            value, origin = get_auth_config().api_key_hash_salt, "configured"
        else:
            value, origin = secrets.token_urlsafe(32), "generated"
        try:
            doc = await coll.find_one_and_update(
                {"_id": _DOC_ID},
                {"$setOnInsert": {"value": value, "origin": origin, "set_at": datetime.now(UTC)}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            # Another replica inserted between our read and our upsert.
            doc = await coll.find_one({"_id": _DOC_ID})
        if doc is not None and doc.get("origin") == origin and doc.get("value") == value:
            logger.warning(
                "Runtime API key salt stored (%s): %s",
                origin,
                "the configured salt, since API keys already exist"
                if origin == "configured" else "a new random value for this install",
            )
    assert doc is not None
    _current = doc["value"]
    return _current


async def refresh(provider: APIKeyProvider | None = None) -> str:
    """Re-read the stored salt (another replica may have adopted one) and
    bring `provider` in step."""
    global _current
    doc = await _collection().find_one({"_id": _DOC_ID})
    if doc is not None:
        _current = doc["value"]
    salt = current_salt()
    if provider is not None:
        provider.set_runtime_salt(salt)
    return salt


async def adopt(salt: str, provider: APIKeyProvider | None = None) -> None:
    """Make `salt` the stack's runtime salt (a system restore onto a fresh
    stack). The caller re-hashes the managed keys afterwards."""
    global _current
    await _collection().update_one(
        {"_id": _DOC_ID},
        {"$set": {"value": salt, "origin": "restored", "set_at": datetime.now(UTC)}},
        upsert=True,
    )
    _current = salt
    if provider is not None:
        provider.set_runtime_salt(salt)
    logger.warning("Runtime API key salt replaced by a system restore's")


def reset_for_tests() -> None:
    global _current
    _current = None
