"""Import the install's key file and reservations file into Registry records.

One key model: the deployer keeps rendering its key file (plaintext keys,
their namespaces and grants) and its reservations file, and the Registry
imports both into its own records, marked `managed_by: "wip-deploy"`:

- a key into `api_keys`, hashed with bcrypt (the plaintext is never stored);
- a key's declared grants into `namespace_grants` (its namespaces give read,
  its grants their level, the same rule wip-auth applies to a config key);
- a reservation into `namespace_reservations`.

A pass runs at startup before the Registry serves, every
RECONCILE_INTERVAL_SECONDS, and when a reservation lookup finds either file
changed. It is idempotent: it creates what the files declare and is missing,
corrects what differs, and removes managed records the files no longer
declare. It never removes a record it did not import: Console and restored
records are only ever taken over, never deleted.

Rules:

- **Never remove on a bad read.** A key file that is missing, unreadable,
  does not parse, or lacks the canary key (every rendered file has it, so a
  file without it is a broken render, not an install with no keys) imports
  nothing and removes nothing. The same holds for a missing or unparseable
  reservations file. The previous import stands until the file is fixed.
- **A collision: the managed record wins, loudly.** A Console or restored
  record with a name, grant or reserved prefix the file declares is taken
  over (the database keeps one record per name, grant and prefix), logged
  CRITICAL with what it replaced. A Console key with the same plaintext as a
  file key is shadowed by the shadow check (key_shadowing), which compares
  stored hashes with this module's file plaintexts.
- **Imported keys stay dormant while the Registry loads the same file as
  config keys.** A managed key whose name is a loaded config key is left out
  of key sync and of the Registry's own provider: every service still
  resolves that plaintext as the config key. Once the Registry no longer
  loads the file as config keys, the records are the keys.
- **Change detection without a persisted fast hash.** Each verified pair
  (the file plaintext's SHA-256 prehash, the stored bcrypt hash) is kept in
  memory only, so an unchanged key costs no bcrypt. After a restart each
  managed key costs one bcrypt check. Nothing fast to attack is stored in
  MongoDB or carried by a backup.
"""

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import TypeAdapter, ValidationError

from wip_auth import APIKeyProvider, APIKeyRecord
from wip_auth.providers.api_key import api_key_prehash, hash_api_key, prehash_matches

from ..models.api_key import StoredAPIKey
from ..models.grant import NamespaceGrant
from ..models.reservation import RuntimeReservation
from .managed import WIP_DEPLOY
from .reservations import RESERVATIONS_FILE_ENV, ReservationsUnavailable, load_reservations

logger = logging.getLogger("registry.key_import")

# The key file: WIP_REGISTRY_KEY_FILE, which only the Registry reads. An
# install rendered before it existed declares WIP_AUTH_API_KEYS_FILE, which
# wip-auth also loads as config keys, so its imports stay dormant.
KEY_FILE_ENV = "WIP_REGISTRY_KEY_FILE"
LEGACY_KEY_FILE_ENV = "WIP_AUTH_API_KEYS_FILE"
# The key every rendered key file carries (wip-deploy's CANARY_KEY_NAME,
# deployer/src/wip_deploy/spec/deployment.py). Its absence marks a broken render.
CANARY_KEY_NAME = "wip-canary"

_EXPIRES: TypeAdapter[datetime | None] = TypeAdapter(datetime | None)

_lock = asyncio.Lock()
# name -> (prehash of the file's plaintext, the stored hash it was verified against)
_verified: dict[str, tuple[bytes, str]] = {}
# name -> prehash of the file's plaintext, for the shadow check
_file_prehashes: dict[str, bytes] = {}
# Content digests of (key file, reservations file) at the last pass
_last_seen: tuple[str | None, str | None] | None = None
# Why the reservations file could not be used at the last pass, if it could not
_reservations_error: ReservationsUnavailable | None = None


@dataclass
class ImportReport:
    keys_created: list[str] = field(default_factory=list)
    keys_updated: list[str] = field(default_factory=list)
    keys_removed: list[str] = field(default_factory=list)
    taken_over: list[str] = field(default_factory=list)
    grants_changed: list[str] = field(default_factory=list)
    reservations_changed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def file_prehashes() -> dict[str, bytes]:
    """The plaintext prehash of every key in the last good key file."""
    return dict(_file_prehashes)


def is_dormant(doc: StoredAPIKey, config_key_names: Iterable[str]) -> bool:
    """A managed key the Registry also loads as a config key of the same name.

    It stays out of key sync and of the Registry's provider: the config key
    is what every service resolves its plaintext as, so a second record for
    one plaintext must not reach them.
    """
    return doc.managed_by is not None and doc.name in set(config_key_names)


def _digest(path: str | None) -> str | None:
    if not path:
        return None
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def _key_file_path() -> str | None:
    return os.getenv(KEY_FILE_ENV) or os.getenv(LEGACY_KEY_FILE_ENV) or None


def _current_digests() -> tuple[str | None, str | None]:
    return _digest(_key_file_path()), _digest(os.getenv(RESERVATIONS_FILE_ENV))


def _read_key_file() -> tuple[list[dict[str, Any]] | None, str | None]:
    """The key file's entries, or (None, why) when it must not be imported."""
    path = _key_file_path()
    if not path:
        return None, None  # a hand-run Registry: nothing to import, nothing to report
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return None, f"{path} does not exist"
    except (OSError, ValueError) as e:
        return None, f"{path} cannot be read: {e}"
    entries = data.get("keys") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        return None, f"{path} has no list of keys"
    names = [e.get("name") for e in entries]
    if CANARY_KEY_NAME not in names:
        return None, f"{path} lacks the canary key '{CANARY_KEY_NAME}'; not a complete render"
    if len(set(names)) != len(names):
        return None, f"{path} names a key twice"
    for entry in entries:
        record = {k: v for k, v in entry.items() if k != "key"}
        record.setdefault("key_hash", "x")
        try:
            APIKeyRecord(**record)
        except ValidationError as err:
            return None, f"{path}: key '{entry.get('name')}' is invalid: {err}"
        if not entry.get("key") and not entry.get("key_hash"):
            return None, f"{path}: key '{entry.get('name')}' has no key"
    return entries, None


Permission = Literal["read", "write", "admin"]


def _desired_grants(entry: dict[str, Any]) -> dict[str, Permission] | None:
    """A key's grants as the file declares them, or None when it declares
    none (its grants then stay under the API). Namespaces give read, grants
    their level: the rule wip-auth applies to a config key's declared grants."""
    grants = entry.get("grants")
    if grants is None:
        return None
    return {**{ns: "read" for ns in entry.get("namespaces") or []}, **grants}


async def _refresh_provider(
    provider: APIKeyProvider | None, old_hash: str | None, doc: StoredAPIKey | None
) -> None:
    """Keep the Registry's provider in step with a managed record, unless the
    record is dormant (the provider already holds the config key)."""
    if provider is None:
        return
    from .key_shadowing import provider_lock, runtime_record

    async with provider_lock:
        if old_hash:
            provider.remove_key(old_hash)
        if doc is not None and doc.enabled and not is_dormant(doc, provider.config_key_names()):
            provider.remove_key(doc.key_hash)
            provider.add_key(runtime_record(doc))


async def _import_keys(
    entries: list[dict[str, Any]], provider: APIKeyProvider | None, report: ImportReport
) -> None:
    from .key_salt import current_salt

    salt = current_salt()
    in_file: set[str] = set()
    prehashes: dict[str, bytes] = {}
    for entry in entries:
        name = entry["name"]
        in_file.add(name)
        plaintext = entry.get("key")
        prehash = api_key_prehash(plaintext, salt) if plaintext else None
        fields = {
            "owner": entry.get("owner", "system"),
            "groups": list(entry.get("groups") or []),
            "description": entry.get("description"),
            "expires_at": _EXPIRES.validate_python(entry.get("expires_at")),
            "enabled": entry.get("enabled", True),
            "namespaces": entry.get("namespaces"),
            "grants_managed": entry.get("grants") is not None,
        }

        doc = await StoredAPIKey.find_one(StoredAPIKey.name == name)
        old_hash = doc.key_hash if doc else None
        if doc is None:
            key_hash = (
                await asyncio.to_thread(hash_api_key, plaintext, salt)
                if plaintext else entry["key_hash"]
            )
            doc = StoredAPIKey(
                name=name, key_hash=key_hash, created_by=WIP_DEPLOY,
                managed_by=WIP_DEPLOY, origin="file", **fields,
            )
            await doc.insert()
            report.keys_created.append(name)
        else:
            changed = False
            if doc.managed_by is None:
                logger.critical(
                    "API key '%s' (origin %s, created by %s) has the name of a key in the "
                    "install's key file; the key file's key replaces it and wip-deploy "
                    "manages it from now on. The replaced key's value stops working.",
                    name, doc.origin, doc.created_by,
                )
                report.taken_over.append(f"api_key:{name}")
                doc.managed_by = WIP_DEPLOY
                doc.origin = "file"
                changed = True
            if plaintext and prehash is not None:
                same = _verified.get(name) == (prehash, doc.key_hash) or await asyncio.to_thread(
                    prehash_matches, prehash, doc.key_hash
                )
                if not same:
                    doc.key_hash = await asyncio.to_thread(hash_api_key, plaintext, salt)
                    changed = True
            elif doc.key_hash != entry["key_hash"]:
                doc.key_hash = entry["key_hash"]
                changed = True
            for attr, value in fields.items():
                if getattr(doc, attr) != value:
                    setattr(doc, attr, value)
                    changed = True
            if changed:
                await doc.save()
                if name not in report.keys_created:
                    report.keys_updated.append(name)
        if prehash is not None:
            _verified[name] = (prehash, doc.key_hash)
            prehashes[name] = prehash
        if old_hash != doc.key_hash or name in report.keys_created or name in report.keys_updated:
            await _refresh_provider(provider, old_hash, doc)

        desired = _desired_grants(entry)
        await _import_grants(name, desired, report)

    # Managed keys the file no longer declares go, as a revoke takes them:
    # with their grants, and their API reservations where never used.
    for doc in await StoredAPIKey.find(StoredAPIKey.managed_by == WIP_DEPLOY).to_list():
        if doc.name in in_file:
            continue
        await doc.delete()
        await _refresh_provider(provider, doc.key_hash, None)
        await NamespaceGrant.find(
            {"subject_type": "api_key", "subject": {"$in": [doc.name, f"apikey:{doc.name}"]}}
        ).delete()
        from .reservation_store import release, reservations_of

        for r in await reservations_of(doc.name):
            if r.managed_by is None:
                await release(r.prefix)
        _verified.pop(doc.name, None)
        report.keys_removed.append(doc.name)

    _file_prehashes.clear()
    _file_prehashes.update(prehashes)


async def _import_grants(
    name: str, declared: dict[str, Permission] | None, report: ImportReport
) -> None:
    """Make the managed key's grants exactly what the file declares. When it
    declares none (None), only grants an earlier import made are removed;
    the key's other grants stay under the API."""
    desired = declared or {}
    existing = await NamespaceGrant.find(
        {"subject_type": "api_key", "subject": {"$in": [name, f"apikey:{name}"]}}
    ).to_list()
    by_ns: dict[str, list[NamespaceGrant]] = {}
    for g in existing:
        by_ns.setdefault(g.namespace, []).append(g)

    for ns, permission in desired.items():
        grants = by_ns.pop(ns, [])
        canonical = next((g for g in grants if g.subject == name), None)
        for g in grants:
            if g is canonical:
                continue
            if g.managed_by is None:
                logger.critical(
                    "Grant to '%s' on '%s' (%s, origin %s) is replaced by the install's "
                    "key file", g.subject, ns, g.permission, g.origin,
                )
                report.taken_over.append(f"grant:{ns}:{g.subject}")
            await g.delete()
        if canonical is None:
            await NamespaceGrant(
                namespace=ns, subject=name, subject_type="api_key",
                permission=permission, granted_by=WIP_DEPLOY,
                managed_by=WIP_DEPLOY, origin="file",
            ).create()
            report.grants_changed.append(f"{ns}:{name}")
        elif (canonical.managed_by, canonical.permission) != (WIP_DEPLOY, permission):
            if canonical.managed_by is None:
                logger.critical(
                    "Grant to '%s' on '%s' (%s, origin %s) is replaced by the install's "
                    "key file (%s)", name, ns, canonical.permission, canonical.origin, permission,
                )
                report.taken_over.append(f"grant:{ns}:{name}")
            canonical.permission = permission
            canonical.managed_by = WIP_DEPLOY
            canonical.origin = "file"
            canonical.granted_by = WIP_DEPLOY
            canonical.expires_at = None
            # The install's file declares this grant: it is effective, never
            # an orphan a restore left waiting.
            canonical.orphaned = False
            canonical.orphaned_at = None
            await canonical.save()
            report.grants_changed.append(f"{ns}:{name}")

    # A grant on a namespace the file does not declare: one this import made
    # goes; one the API made goes too when the file owns the key's grants
    # (wip-auth never consulted it for a config key with declared grants).
    owns_grants = declared is not None
    for ns, grants in by_ns.items():
        for g in grants:
            if g.managed_by is None and not owns_grants:
                continue
            if g.managed_by is None:
                logger.warning(
                    "Grant to '%s' on '%s' (%s), made through the API, is removed: the "
                    "install's key file owns this key's grants", g.subject, ns, g.permission,
                )
            await g.delete()
            report.grants_changed.append(f"-{ns}:{g.subject}")


async def _import_reservations(report: ImportReport) -> None:
    """Make the managed reservations exactly the file's. Raises
    ReservationsUnavailable for a file that is present and unusable."""
    path = os.getenv(RESERVATIONS_FILE_ENV)
    if not path:
        return
    if not os.path.exists(path):
        report.skipped.append(f"{RESERVATIONS_FILE_ENV}={path} does not exist")
        return
    reservations = load_reservations()  # raises ReservationsUnavailable

    for prefix, r in reservations.items():
        settings = r.settings.model_dump(exclude_none=True)
        doc = await RuntimeReservation.find_one(RuntimeReservation.prefix == prefix)
        if doc is None:
            await RuntimeReservation(
                prefix=prefix, app=r.app, settings=settings, created_by=WIP_DEPLOY,
                managed_by=WIP_DEPLOY, origin="file",
            ).insert()
            report.reservations_changed.append(prefix)
            continue
        if doc.managed_by is None:
            logger.critical(
                "Namespace '%s' is reserved by runtime API key '%s' and by app '%s' in the "
                "install's reservations; the install's reservation replaces it. Key '%s' "
                "keeps its grants.", prefix, doc.app, r.app, doc.app,
            )
            report.taken_over.append(f"reservation:{prefix}")
        if (doc.managed_by, doc.app, doc.settings, doc.origin) != (WIP_DEPLOY, r.app, settings, "file"):
            doc.managed_by = WIP_DEPLOY
            doc.app = r.app
            doc.settings = settings
            doc.origin = "file"
            await doc.save()
            report.reservations_changed.append(prefix)

    for doc in await RuntimeReservation.find(RuntimeReservation.managed_by == WIP_DEPLOY).to_list():
        if doc.prefix not in reservations:
            await doc.delete()
            report.reservations_changed.append(f"-{doc.prefix}")


async def run_pass(provider: APIKeyProvider | None = None) -> ImportReport:
    """One import of both files. Safe to call concurrently: passes run one at a time."""
    global _last_seen, _reservations_error
    async with _lock:
        report = ImportReport()
        digests = _current_digests()

        entries, why = _read_key_file()
        if entries is None:
            if why:
                logger.error("Key file not imported (nothing changed): %s", why)
                report.skipped.append(why)
        else:
            await _import_keys(entries, provider, report)

        try:
            await _import_reservations(report)
            _reservations_error = None
        except ReservationsUnavailable as e:
            logger.critical("Reservations file not imported (nothing changed): %s", e)
            report.skipped.append(str(e))
            _reservations_error = e

        _last_seen = digests
        changes = [
            report.keys_created, report.keys_updated, report.keys_removed,
            report.taken_over, report.grants_changed, report.reservations_changed,
        ]
        if any(changes):
            logger.info(
                "Key import: keys created=%s updated=%s removed=%s; grants=%s; "
                "reservations=%s; taken over=%s",
                report.keys_created, report.keys_updated, report.keys_removed,
                report.grants_changed, report.reservations_changed, report.taken_over,
            )
        return report


async def ensure_current(provider: APIKeyProvider | None = None) -> None:
    """Import now if either file changed since the last pass, so a lookup
    never answers from an import older than the files. Raises
    ReservationsUnavailable when the reservations file is unusable."""
    if _current_digests() != _last_seen:
        await run_pass(provider)
    if _reservations_error is not None:
        raise _reservations_error


async def run_loop(provider: APIKeyProvider | None) -> None:
    """Import, then recompute the shadows, every RECONCILE_INTERVAL_SECONDS
    until cancelled. A pass also re-asserts every managed record, so a
    MongoDB restore under a running Registry heals within one interval."""
    from . import key_salt, key_shadowing

    while True:
        await asyncio.sleep(key_shadowing.RECONCILE_INTERVAL_SECONDS)
        try:
            # Another Registry replica may have adopted a restored salt.
            await key_salt.refresh(provider)
            await run_pass(provider)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Key import failed; the previous import stands")
        if provider is not None:
            try:
                await key_shadowing.reconcile(provider)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("API key shadow check failed; keeping the previous result")


def reset_for_tests() -> None:
    global _last_seen, _reservations_error
    _verified.clear()
    _file_prehashes.clear()
    _last_seen = None
    _reservations_error = None
