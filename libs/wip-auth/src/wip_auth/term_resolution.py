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
from dataclasses import dataclass, field
from typing import Any, cast

import httpx

from .resolve import EntityNotFoundError, _looks_like_uuid, resolve_entity_id
from .service_keys import DEF_STORE_KEY, service_api_key


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
    """The key for Def-Store calls; MissingServiceKeyError when none is set."""
    return service_api_key(*DEF_STORE_KEY)


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
    item order, has `terminology_id` (the canonical id of the terminology
    the value was resolved in, absent only when the terminology itself did
    not resolve), `valid`, `matched_term` ({term_id, value}),
    `matched_via` ('value' | 'alias'), `ambiguous` + `candidates` when
    several terms bear the matched name, `suggestion` on a miss, or `error`
    when the terminology itself does not resolve.

    Raises:
        TermResolutionError: def-store unreachable or answering an error —
            the caller decides (identity fields fail closed).
        RegistryUnavailableError: the Registry did not answer while
            resolving a value-form terminology ref — never reported as
            "Terminology not found".
    """
    if not items:
        return []
    _stats["values"] += len(items)
    batch = _batch.get()
    plan = await _plan_term_resolution(items, namespace, batch)
    if plan.pending:
        keys = list(plan.pending)
        resolved = await _resolve_keys(
            keys, batch, base_url or _get_def_store_url(), api_key or _get_api_key(), timeout,
        )
        for key, result in zip(keys, resolved, strict=True):
            for index in plan.pending[key]:
                plan.results[index] = result
    for index, future in plan.shared:
        plan.results[index] = await future
    # Each result names the terminology its value was resolved in, valid or
    # not: the caller decides by terminology (e.g. whether the writer may
    # reference its namespace), so a present and an absent value must carry
    # it alike. A copy per item: resolutions are shared across a batch.
    for index, terminology_id in enumerate(plan.terminology_ids):
        if terminology_id is not None:
            plan.results[index] = {**cast(dict[str, Any], plan.results[index]),
                                   "terminology_id": terminology_id}
    return cast(list[dict[str, Any]], plan.results)


_TermKey = tuple[str, str]


@dataclass
class _TermPlan:
    """How a request's items will be answered: results known already (an
    unresolved terminology), each item's terminology id, the keys to ask
    def-store for (with the items waiting on each), and items answered by a
    resolution another caller in the batch already has in flight."""

    results: list[dict[str, Any] | None]
    terminology_ids: list[str | None]
    pending: dict[_TermKey, list[int]] = field(default_factory=dict)
    shared: list[tuple[int, asyncio.Future[dict[str, Any]]]] = field(default_factory=list)


async def _plan_term_resolution(
    items: list[dict[str, str]],
    namespace: str | None,
    batch: dict[_TermKey, asyncio.Future[dict[str, Any]]] | None,
) -> _TermPlan:
    """Resolve each distinct terminology ref once and sort the items into
    errors, shared resolutions and keys to ask."""
    plan = _TermPlan(results=[None] * len(items), terminology_ids=[None] * len(items))
    canonical: dict[str, str | None] = {}
    for index, item in enumerate(items):
        ref = item["terminology_ref"]
        if ref not in canonical:
            canonical[ref] = await _resolve_terminology_ref(ref, namespace)
        terminology_id = canonical[ref]
        if terminology_id is None:
            plan.results[index] = {"valid": False, "error": f"Terminology '{ref}' not found"}
            continue
        plan.terminology_ids[index] = terminology_id
        key = (terminology_id, item["value"])
        if batch is not None and key in batch:
            _stats["batch_reused"] += 1
            plan.shared.append((index, batch[key]))
            continue
        plan.pending.setdefault(key, []).append(index)
    return plan


async def _resolve_keys(
    keys: list[_TermKey],
    batch: dict[_TermKey, asyncio.Future[dict[str, Any]]] | None,
    base_url: str,
    api_key: str,
    timeout: float,
) -> list[dict[str, Any]]:
    """Ask def-store for the keys in one call. Inside a batch each key's
    resolution is published as a future first, so concurrent callers wait
    on this call instead of asking again; a failure reaches every waiter and
    leaves the batch, so later documents may retry."""
    owned: dict[_TermKey, asyncio.Future[dict[str, Any]]] = {}
    if batch is not None:
        loop = asyncio.get_running_loop()
        for key in keys:
            owned[key] = batch[key] = loop.create_future()
    try:
        resolved = await _resolve_remote(keys, base_url, api_key, timeout)
    except BaseException as e:
        for key, future in owned.items():
            if batch is not None:
                batch.pop(key, None)
            future.set_exception(e)
            future.exception()  # retrieved here: no waiter is required
        raise
    for key, result in zip(keys, resolved, strict=True):
        if key in owned:
            owned[key].set_result(result)
    return resolved


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
