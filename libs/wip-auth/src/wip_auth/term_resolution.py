"""Term-value resolution for WIP — the one way a service asks.

Which term does a submitted value name in a vocabulary? def-store alone
decides — only active terms match, a term's value beats any term's names,
the terminology's case rule applies, and every bearer of an ambiguous name
is reported — because term names and status live in def-store's data. This
module is how any service asks: one def-store call per batch of values,
never a local copy of the vocabulary. A write that persists a term_id (and
hashes it for identity fields) must resolve from the authority at write
time. It is the counterpart of wip_auth.resolve, which resolves identifiers
through the Registry.

Usage:
    from wip_auth.term_resolution import resolve_term_values, term_resolution_batch

    results = await resolve_term_values(
        [{"terminology_ref": "STATUS", "value": "approved"}], namespace="wip",
    )

    with term_resolution_batch():   # around one write request
        ...                         # identical values resolve once
"""

import asyncio
import contextlib
import contextvars
import os
from collections.abc import Iterator
from typing import Any, cast

import httpx

from .resolve import EntityNotFoundError, _looks_like_uuid, resolve_entity_id


class TermResolutionError(Exception):
    """def-store could not be asked, or answered with an error."""


# Transport injection for in-process testing (an ASGITransport over the real
# def-store app), the same pattern as wip_auth.resolve's set_resolve_transport.
_transport: httpx.AsyncBaseTransport | None = None


def set_def_store_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Set the httpx transport for def-store calls (for in-process testing)."""
    global _transport
    _transport = transport


def get_def_store_transport() -> httpx.AsyncBaseTransport | None:
    return _transport


def _get_def_store_url() -> str:
    return os.getenv("WIP_AUTH_DEF_STORE_URL", os.getenv("DEF_STORE_URL", "http://localhost:8002"))


def _get_api_key() -> str:
    return os.getenv("DEF_STORE_API_KEY", os.getenv("API_KEY", "dev_master_key_for_testing"))


# Resolutions shared by every document of one write request. Request-scoped by
# construction: the memo lives in a context variable set for one batch and is
# gone with it. Entries are futures, not results: a bulk write validates its
# documents concurrently, so the first document to need a value asks def-store
# and the others await that same answer instead of all asking at once.
_batch: contextvars.ContextVar[dict[tuple[str, str], asyncio.Future[dict[str, Any]]] | None] = (
    contextvars.ContextVar("wip_term_resolution_batch", default=None)
)

_stats = {"values": 0, "remote": 0, "batch_reused": 0}


@contextlib.contextmanager
def term_resolution_batch() -> Iterator[None]:
    """Share term resolutions across the documents of one write request.

    Inside the block, a value already resolved for this batch (same
    terminology, same spelling) is answered from the batch instead of asking
    def-store again — every document of one request sees one consistent
    resolution, and a bulk import repeating a value pays for it once.
    Nothing outlives the block.
    """
    token = _batch.set({})
    try:
        yield
    finally:
        _batch.reset(token)


def resolution_stats() -> dict[str, int]:
    """Counters since start (or reset_resolution_stats): values asked for,
    values sent to def-store, values answered from a write batch."""
    return dict(_stats)


def reset_resolution_stats() -> None:
    for key in _stats:
        _stats[key] = 0


async def resolve_term_values(
    items: list[dict[str, str]],
    namespace: str | None = None,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    """Resolve submitted values to terms — one def-store call for the batch.

    Each item has `terminology_ref` (canonical UUID, a bare value = the
    `namespace`'s terminology, or 'ns:VALUE') and `value`. Each result, in
    item order, has `valid`, `matched_term` ({term_id, value}),
    `matched_via` ('value' | 'alias'), `ambiguous` + `candidates` when
    several terms bear the matched name, `suggestion` on a miss, or `error`
    when the terminology itself does not resolve.

    Raises:
        TermResolutionError: def-store unreachable or answering an error —
            the caller decides (identity fields fail closed).
    """
    if not items:
        return []
    _stats["values"] += len(items)
    batch = _batch.get()

    results: list[dict[str, Any] | None] = [None] * len(items)
    pending: dict[tuple[str, str], list[int]] = {}
    shared: list[tuple[int, asyncio.Future[dict[str, Any]]]] = []
    canonical: dict[str, str | None] = {}
    for index, item in enumerate(items):
        ref = item["terminology_ref"]
        if ref not in canonical:
            canonical[ref] = await _resolve_terminology_ref(ref, namespace)
        terminology_id = canonical[ref]
        if terminology_id is None:
            results[index] = {"valid": False, "error": f"Terminology '{ref}' not found"}
            continue
        key = (terminology_id, item["value"])
        if batch is not None and key in batch:
            _stats["batch_reused"] += 1
            shared.append((index, batch[key]))
            continue
        pending.setdefault(key, []).append(index)

    if pending:
        keys = list(pending)
        owned: dict[tuple[str, str], asyncio.Future[dict[str, Any]]] = {}
        if batch is not None:
            loop = asyncio.get_running_loop()
            for key in keys:
                owned[key] = batch[key] = loop.create_future()
        try:
            resolved = await _resolve_remote(
                keys, base_url or _get_def_store_url(), api_key or _get_api_key(), timeout,
            )
        except BaseException as e:
            # Waiters see the same failure; later documents may retry.
            for key, future in owned.items():
                if batch is not None:
                    batch.pop(key, None)
                future.set_exception(e)
                future.exception()  # retrieved here: no waiter is required
            raise
        for key, result in zip(keys, resolved, strict=True):
            if key in owned:
                owned[key].set_result(result)
            for index in pending[key]:
                results[index] = result

    for index, future in shared:
        results[index] = await future
    return cast(list[dict[str, Any]], results)


async def _resolve_terminology_ref(terminology_ref: str, namespace: str | None) -> str | None:
    """A terminology ref's canonical UUID, or None when it does not resolve.

    Canonical UUIDs pass through without a Registry round-trip. Value forms
    resolve through the Registry with `namespace` as context — bare value =
    that namespace, 'ns:VALUE' = cross-namespace. A value form with no
    namespace context, or one the Registry cannot resolve, returns None
    (reported as terminology-not-found); there is no unscoped by-value
    fallback, because with one value live in several namespaces there is no
    correct arbitrary pick.
    """
    if _looks_like_uuid(terminology_ref):
        return terminology_ref
    if not namespace:
        return None
    try:
        return await resolve_entity_id(terminology_ref, "terminology", namespace)
    except EntityNotFoundError:
        return None


async def _resolve_remote(
    keys: list[tuple[str, str]], base_url: str, api_key: str, timeout: float,
) -> list[dict[str, Any]]:
    """POST /validate/bulk for (terminology_id, value) pairs, in order."""
    _stats["remote"] += len(keys)
    body = {"items": [{"terminology_id": tid, "value": value} for tid, value in keys]}
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=_transport) as client:
            response = await client.post(
                f"{base_url}/api/def-store/validate/bulk",
                headers={"X-API-Key": api_key, "Content-Type": "application/json"},
                json=body,
            )
    except httpx.RequestError as e:
        raise TermResolutionError(f"Request failed: {e!s}") from e
    if response.status_code != 200:
        raise TermResolutionError(f"Term validation failed: {response.status_code} - {response.text}")
    answers = response.json().get("results", [])
    if len(answers) != len(keys):
        raise TermResolutionError(f"Term validation answered {len(answers)} results for {len(keys)} values")
    return [shape_resolution(answer) for answer in answers]


def shape_resolution(answer: dict[str, Any]) -> dict[str, Any]:
    """Shape one def-store ValidateValueResponse into a resolution result."""
    def term_ref(term: dict[str, Any] | None) -> dict[str, Any] | None:
        return {"term_id": term.get("term_id"), "value": term.get("value")} if term else None

    if not answer.get("valid"):
        result: dict[str, Any] = {
            "valid": False,
            "matched_term": None,
            "matched_via": None,
            "suggestion": term_ref(answer.get("suggestion")),
        }
        if answer.get("error"):
            result["error"] = answer["error"]
        return result
    result = {
        "valid": True,
        "matched_term": term_ref(answer.get("matched_term")),
        "matched_via": answer.get("matched_via"),
    }
    if (answer.get("matches") or 0) > 1:
        result["ambiguous"] = True
        result["candidates"] = [term_ref(c.get("term")) for c in answer.get("candidates", [])]
    return result
