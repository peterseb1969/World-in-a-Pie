"""FastAPI helper functions for synonym resolution at API boundaries.

An ID resolved here — canonical or synonym — is verified against Registry,
with no format-based bypass, whenever there is namespace context (given,
derived from a single-namespace key, or carried by a qualified ``NS:VALUE``).
Without it nothing is asked of the Registry: resolve_or_404 passes the
identifier through unverified (a UUID always, a value unless ``strict``), and
resolve_bulk_ids leaves such an item unresolved for the store to report.

Usage:
    from wip_auth.fastapi_helpers import resolve_or_404, resolve_bulk_ids

    # Single ID resolution (raises 404 on failure)
    template_id = await resolve_or_404(template_id, "template", namespace)

    # Bulk resolution (mutates items in place)
    await resolve_bulk_ids(items, "template_id", "template", namespace)
"""

import logging
import typing
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from fastapi import HTTPException

from .identity import get_current_identity
from .resolve import (
    AmbiguousEntityError,
    EntityNotFoundError,
    TermNamespaceRequiredError,
    _looks_like_uuid,
    resolve_entity_id,
    resolve_entity_ids,
    resolve_term_by_fields,
)

logger = logging.getLogger(__name__)


def _ambiguous_409(e: AmbiguousEntityError) -> HTTPException:
    """409 for an identifier that names several entities, with its candidates.

    Ambiguity is a definite answer, not a miss: the caller can fix it by
    choosing one, so the candidates travel with the error.
    """
    return HTTPException(
        status_code=409,
        detail={
            "error": "ambiguous",
            "error_code": "ambiguous_reference",
            "message": str(e),
            "identifier": e.identifier,
            "candidates": e.candidates,
            "total": e.total,
            "truncated": e.truncated,
        },
    )


def _reject_ambiguous_term_form(raw_id: str, param_name: str | None) -> None:
    """Reject the lossy 2-part ``TERMINOLOGY:VALUE`` term shorthand with 422.

    A term's identity is the tuple (namespace, terminology, value). The
    2-part colon shorthand is a lossy serialization of it: no parser can
    tell a structural colon from a colon inside the value (OBO ids like
    ``GO:0000278``), so a 2-part string can silently resolve to a term in
    a decoy terminology — on a write door, that mutates the wrong entity.
    Term doors therefore accept only the unambiguous forms: canonical
    UUID, fully qualified ``ns:terminology:value`` (split on the first two
    colons, so the value keeps any colons it contains), or structured
    fields (a ``terminology`` scope with the raw value passed opaque).

    Called for every term identifier that resolves WITHOUT a terminology
    scope. Identifiers under a terminology scope are opaque values and are
    never colon-parsed, so no rejection applies there.
    """
    if _looks_like_uuid(raw_id):
        return
    if ":" not in raw_id:
        return
    if len(raw_id.split(":", 2)) == 2:
        label = param_name or "term"
        raise HTTPException(
            status_code=422,
            detail=(
                f"Ambiguous term identifier '{raw_id}' for {label}: the "
                "2-part 'TERMINOLOGY:VALUE' shorthand is not accepted — a "
                "value that itself contains ':' (e.g. OBO ids like "
                "GO:0000278) cannot be distinguished from it. Use the "
                "canonical UUID, the fully qualified "
                "'ns:terminology:value' form, or pass terminology= "
                "separately with the raw value."
            ),
        )


async def _derive_namespace_from_identity() -> str | None:
    """The namespace the current request's API key implies, or None (the
    caller must name one). The rule is ``permissions.implied_namespace``."""
    from .permissions import implied_namespace

    identity = get_current_identity()
    if identity is None:
        return None
    return await implied_namespace(identity)


async def resolve_or_404(
    raw_id: str,
    entity_type: str,
    namespace: str | None,
    *,
    param_name: str | None = None,
    strict: bool = False,
    bypass_cache: bool = False,
) -> str:
    """Resolve any identifier to a canonical ID, raising HTTP 404 on failure.

    Both canonical IDs and synonyms are verified against Registry when
    there is namespace context. If ``namespace`` is None, attempts to
    derive it from the caller's identity (single-namespace API keys) or a
    qualified identifier's prefix. If that fails, nothing is asked of the
    Registry: a UUID, and (unless ``strict``) a value, is returned as-is,
    unverified (caller must handle value-based fallback). Unverified
    includes its type: a UUID of another entity type comes back too, and the
    caller's own lookup in its collection is what then answers 404.

    Args:
        raw_id: The identifier to resolve (canonical ID or human-readable synonym).
        entity_type: Entity type for resolution (terminology, term, template, document).
        namespace: Namespace for resolution context. If None, derived from identity.
        param_name: Optional parameter name for error messages.
        bypass_cache: Skip the resolution-cache read and always ask
            Registry. REQUIRED on write doors: after a hard-delete and
            re-create under the same value, the cache serves the dead
            canonical ID for up to its TTL — a write that trusts it
            either fails on an id that names nothing or pins a stale id
            into durable state. Reads keep the default (cache on).
        strict: When True, a non-UUID identifier that cannot be resolved for
            lack of namespace context raises HTTP 422 instead of passing the
            raw value through. Use this in **filter contexts** that have no
            value-based fallback (e.g. a query's ``template_id``): there, the
            raw value flows into the storage filter, matches nothing, and the
            request "succeeds" with zero rows — a silent misconfiguration of
            the CASE-316/317/318 class (CASE-457). Endpoints that DO have a
            value fallback (a GET's by-value branch) must leave this False so
            the pass-through remains load-bearing.

    Returns:
        Canonical entity ID.

    Raises:
        HTTPException(404): When the identifier cannot be resolved.
        HTTPException(409): When the identifier names several entities;
            the detail carries the candidates.
        RegistryUnavailableError: When the Registry does not answer —
            setup_auth's handler answers 503.
        HTTPException(422): When ``strict`` and the identifier is a non-UUID
            value with no namespace context to resolve it against; or when
            the identifier is a term in the ambiguous 2-part colon form
            (see ``_reject_ambiguous_term_form`` — term doors accept only
            UUID, ``ns:terminology:value``, or field form).
    """
    if entity_type == "term":
        _reject_ambiguous_term_form(raw_id, param_name)

    if namespace is None:
        namespace = await _derive_namespace_from_identity()

    if namespace is None and ":" in raw_id and not _looks_like_uuid(raw_id):
        # A qualified identifier carries its own namespace context — the
        # prefix names the namespace to resolve in (for terms only the
        # 3-part ns:terminology:value form reaches here; the 2-part form was
        # rejected above). "No namespace context" is false for these, so
        # resolve instead of passing the raw string through to a lookup that
        # cannot know the qualified form. A MISS returns the raw id — the
        # same outcome the pass-through produced — so this only ADDS
        # resolution; the explicit-namespace path below keeps 404-on-miss.
        from .resolve import split_qualified_value

        ns_prefix, _ = split_qualified_value(raw_id)
        try:
            return await resolve_entity_id(
                raw_id, entity_type, cast(str, ns_prefix), bypass_cache=bypass_cache
            )
        except AmbiguousEntityError as e:
            raise _ambiguous_409(e) from None
        except EntityNotFoundError:
            if strict:
                # Filter context with real (identifier-carried) namespace
                # context and a genuine miss: same contract as the
                # explicit-namespace path — fail loud, never let the
                # unresolved value silently match nothing.
                label = param_name or entity_type
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"Could not resolve {label} '{raw_id}' in namespace "
                        f"'{ns_prefix}'"
                    ),
                ) from None
            return raw_id

    if namespace is None:
        # Still no namespace — cannot resolve.
        if not _looks_like_uuid(raw_id):
            if strict:
                # No value-based fallback downstream: fail loud instead of
                # letting the unresolved value silently match nothing.
                label = param_name or entity_type
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Cannot resolve {label} '{raw_id}': no namespace "
                        "context. Pass ?namespace= or use a single-namespace "
                        "API key."
                    ),
                )
            logger.warning(
                "resolve_or_404: no namespace for %s=%s — synonym resolution skipped. "
                "Use a namespace-scoped API key or pass namespace explicitly.",
                param_name or entity_type, raw_id,
            )
        # UUID (canonical, self-resolving) or non-strict caller → pass through.
        return raw_id

    try:
        return await resolve_entity_id(
            raw_id, entity_type, namespace, bypass_cache=bypass_cache
        )
    except AmbiguousEntityError as e:
        raise _ambiguous_409(e) from None
    except EntityNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=await unresolved_detail(raw_id, entity_type, namespace, param_name),
        ) from None


async def _resolution_namespace(
    raw_id: str, namespace: str | None, strict: bool = False,
) -> str | None:
    """The namespace whose 404 resolve_or_404 raises for ``raw_id``: the one
    passed, else the caller key's, else, when ``strict``, the one a qualified
    identifier (``ns:VALUE``, ``ns:terminology:value``) names. Without
    ``strict`` a qualified miss passes through unresolved, and the door's own
    miss answers it. Else None."""
    if namespace is None:
        namespace = await _derive_namespace_from_identity()
    if namespace is None and strict and ":" in raw_id and not _looks_like_uuid(raw_id):
        from .resolve import split_qualified_value

        namespace, _ = split_qualified_value(raw_id)
    return namespace


async def unresolved_detail(
    raw_id: str,
    entity_type: str,
    namespace: str | None,
    param_name: str | None = None,
) -> str:
    """The 404 detail resolve_or_404 gives an identifier that names nothing.

    A read that resolves an id and then finds the entity in a namespace the
    caller cannot read answers with this same text, so the answer cannot tell
    "exists, hidden" from "does not exist". ``namespace`` is the one the
    caller passed (None derives it as resolve_or_404 does). With none, the
    text names no namespace: "namespace 'None'" would name one that does not
    exist.
    """
    namespace = await _resolution_namespace(raw_id, namespace)
    label = param_name or entity_type
    where = f" in namespace '{namespace}'" if namespace is not None else ""
    return f"Could not resolve {label} '{raw_id}'{where}"


async def resolve_term_by_fields_or_404(
    value: str,
    terminology: str,
    namespace: str | None,
    *,
    param_name: str = "term",
) -> str:
    """Resolve a term from structured (terminology, value) fields, 404 on miss.

    The field-form counterpart to resolve_or_404: the value is an opaque
    scalar (never colon-parsed), so colon-carrying vocabularies resolve
    like any other. The terminology may be its canonical id, ``NS:VALUE``
    or a bare value. If ``namespace`` is None it is derived from a
    single-namespace key. Only a bare terminology value needs one: with
    neither, the answer is 422, not a silent miss.
    """
    if namespace is None:
        namespace = await _derive_namespace_from_identity()
    try:
        return await resolve_term_by_fields(value, terminology, namespace)
    except TermNamespaceRequiredError:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Cannot resolve {param_name} by terminology+value: no "
                f"namespace context for terminology '{terminology}'. Pass "
                "?namespace=, name the terminology by id or as 'ns:VALUE', "
                "or use a single-namespace API key."
            ),
        ) from None
    except AmbiguousEntityError as e:
        raise _ambiguous_409(e) from None
    except EntityNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=await field_unresolved_detail(value, terminology, namespace, param_name),
        ) from None


async def field_unresolved_detail(
    value: str, terminology: str, namespace: str | None, param_name: str = "term",
) -> str:
    """The 404 detail resolve_term_by_fields_or_404 gives a value that names
    no term; ``namespace`` as the door passed it, else the caller key's one
    namespace. With neither, the text names no namespace: the resolver then
    takes the terminology's own (from its ns: prefix or its id), which this
    text cannot know, and "namespace 'None'" would name one that does not
    exist."""
    if namespace is None:
        namespace = await _derive_namespace_from_identity()
    where = f" (namespace '{namespace}')" if namespace is not None else ""
    return (
        f"Could not resolve {param_name} value '{value}' in "
        f"terminology '{terminology}'{where}"
    )


async def resolution_miss(
    raw_id: str,
    entity_type: str,
    namespace: str | None,
    param_name: str | None = None,
    *,
    strict: bool = False,
) -> HTTPException | None:
    """The 404 resolve_or_404 raises, in this request, for an id that names
    nothing — or None when it would pass such an id through unresolved (no
    namespace given and none derivable from the caller's key), leaving the
    door's own miss as the answer.

    A door that resolved an id and found the entity in a namespace the
    caller cannot read answers exactly what an unknown id gets: this 404
    when there is one, its own miss otherwise. ``namespace`` is what the
    door passed to resolve_or_404, and ``strict`` what it passed there: a
    strict door raises a qualified identifier's miss in the namespace the
    identifier names, so a hidden one gets that same 404.
    """
    namespace = await _resolution_namespace(raw_id, namespace, strict)
    if namespace is None:
        return None
    return HTTPException(
        status_code=404, detail=await unresolved_detail(raw_id, entity_type, namespace, param_name),
    )


async def miss_detail(
    raw_id: str,
    entity_type: str,
    namespace: str | None,
    own: str,
    param_name: str | None = None,
) -> str:
    """The 404 text an unknown id gets on a door whose own miss is the 404
    ``own``: resolve_or_404's text when it resolves in a namespace, else
    ``own`` (see resolution_miss)."""
    miss = await resolution_miss(raw_id, entity_type, namespace, param_name)
    return own if miss is None else str(miss.detail)


async def resolve_bulk_ids(
    items: list,
    id_field: str,
    entity_type: str,
    namespace: str | None,
    *,
    bypass_cache: bool = False,
    terminology: str | None = None,
    terminology_field: str | None = None,
) -> dict[int, "AmbiguousItem"]:
    """Batch-resolve IDs on bulk request items, mutating in place.

    For each item, resolves ``getattr(item, id_field)`` from any identifier
    to its canonical ID, verified against Registry. An item with no
    namespace (none given, none derivable, none on the item) is not
    resolved: it is left as submitted, unverified, for the store to report.

    A miss is logged and does not raise — the downstream service handles
    the unresolved ID (typically a per-item error in the BulkResponse). Three
    things do raise, for the whole request: the ambiguous 2-part term
    shorthand, a request-shape error answered 422 (like a body-validation
    failure) before any item resolves; a Registry that does not answer
    (RegistryUnavailableError, a 503), since items are never passed on
    unverified because of an outage; and a Registry that refuses the request
    itself (RegistryError).

    An identifier that names several entities is left unresolved too, never
    picked, and is returned: the items it was found on, by their position in
    ``items``. The door holds those items out of its write and answers each
    as ambiguous, naming the candidates (``AmbiguousItems``); passed on, the
    store would report the value as not found.

    Args:
        items: List of request items (Pydantic models or dicts).
        id_field: Attribute name containing the ID to resolve.
        entity_type: Entity type for resolution.
        namespace: Namespace for resolution. Per-item namespace is used if
            the item has a ``namespace`` attribute and ``namespace`` is None;
            the namespace a single-namespace key derives is used only for an
            item that names none.
        bypass_cache: Skip the resolution-cache read and always ask
            Registry. REQUIRED on write doors: a resolved ID that will be
            persisted (a deprecation's replacement pointer, a relation's
            endpoints) must not come from a stale cache entry — answering
            writes from the cache pins dead IDs into durable state. Reads
            keep the default (cache on).
        terminology: Call-scoped terminology for term resolution. When set,
            every non-UUID identifier is treated as the OPAQUE raw term
            value (never colon-parsed) and resolves through the structured
            field door — required for values that themselves contain ':'.
        terminology_field: Name of a per-item attribute carrying the
            terminology scope (for request shapes where each item scopes
            itself, e.g. a relation's ``source_terminology``). An item
            without the attribute set falls back to the string path.
    """
    # A namespace the caller names outranks each item's own; one derived
    # from a single-namespace key only fills in for an item that names none.
    derived = await _derive_namespace_from_identity() if namespace is None else None

    def _item_context(item: object) -> tuple[str | None, str | None, str | None]:
        if isinstance(item, dict):
            raw_id = item.get(id_field)
            item_ns = namespace or item.get("namespace") or derived
            item_terminology = terminology or (
                item.get(terminology_field) if terminology_field else None
            )
        else:
            raw_id = getattr(item, id_field, None)
            item_ns = namespace or getattr(item, "namespace", None) or derived
            item_terminology = terminology or (
                getattr(item, terminology_field, None) if terminology_field else None
            )
        return raw_id, item_ns, item_terminology

    # Form-validation pre-pass: a request-shape error must reject the
    # request before ANY item resolves — otherwise earlier items reach
    # Registry (and populate the cache) for a request that then 422s.
    # Ambiguity is a property of the identifier's form, not the namespace,
    # so this fires even for items whose resolution would be skipped.
    if entity_type == "term":
        for item in items:
            raw_id, _, item_terminology = _item_context(item)
            if raw_id and not item_terminology:
                _reject_ambiguous_term_form(raw_id, id_field)

    # Partition the items: field-door items (terminology-scoped opaque
    # values) resolve per item through the structured tuple door;
    # string-door items BATCH through resolve_entity_ids — one deduped
    # Registry list call per distinct namespace instead of one call per
    # item. The per-item loop this replaces cost a bulk write door two
    # serial Registry round-trips per item (a 200-relation request made
    # 400 resolve calls); the batch door with in-request dedup collapses
    # that to one list call per namespace, and a hierarchy that names the
    # same broad term many times resolves it once.
    string_door: list[tuple[int, Any, str, str]] = []  # (index, item, raw_id, ns)
    field_door: list[tuple[int, Any, str, str, str]] = []  # (index, item, raw_id, ns, terminology)

    for index, item in enumerate(items):
        raw_id, item_ns, item_terminology = _item_context(item)

        if not raw_id:
            continue

        if not item_ns:
            logger.warning(
                "resolve_bulk_ids: no namespace for item %s=%s — synonym resolution skipped. "
                "Use a namespace-scoped API key or pass namespace explicitly.",
                id_field, raw_id,
            )
            continue

        if item_terminology and not _looks_like_uuid(raw_id):
            field_door.append((index, item, raw_id, item_ns, item_terminology))
        else:
            string_door.append((index, item, raw_id, item_ns))

    def _write_back(item: Any, resolved: str) -> None:
        if isinstance(item, dict):
            item[id_field] = resolved
        else:
            setattr(item, id_field, resolved)

    ambiguous = await _resolve_string_door(string_door, entity_type, bypass_cache, _write_back)
    ambiguous.update(await _resolve_field_door(field_door, entity_type, bypass_cache, _write_back))
    return {index: AmbiguousItem(id_field, error) for index, error in ambiguous.items()}


async def _resolve_string_door(
    string_door: list[tuple[int, Any, str, str]],
    entity_type: str,
    bypass_cache: bool,
    write_back: Callable[[Any, str], None],
) -> dict[int, AmbiguousEntityError]:
    """resolve_bulk_ids' string door: one batched, deduped resolve per
    namespace. Non-strict — a miss leaves the item's id untouched (the
    downstream service reports it per item in the BulkResponse), fallback
    door included. Returns the ambiguous items by index."""
    ids_by_ns: dict[str, list[str]] = {}
    for _index, _item, raw_id, item_ns in string_door:
        ids_by_ns.setdefault(item_ns, []).append(raw_id)
    resolved_by_ns: dict[str, dict[str, str]] = {}
    ambiguous_by_ns: dict[str, dict[str, AmbiguousEntityError]] = {}
    # A Registry that does not answer raises RegistryUnavailableError out of
    # here (503): items are never passed on unverified because of an outage.
    for item_ns, ns_ids in ids_by_ns.items():
        ambiguous_by_ns[item_ns] = {}
        resolved_by_ns[item_ns] = await resolve_entity_ids(
            ns_ids, entity_type, item_ns,
            bypass_cache=bypass_cache, strict=False, ambiguous=ambiguous_by_ns[item_ns],
        )
    ambiguous: dict[int, AmbiguousEntityError] = {}
    warned: set[tuple[str, str]] = set()
    for index, item, raw_id, item_ns in string_door:
        resolved = resolved_by_ns[item_ns].get(raw_id)
        if resolved is not None:
            write_back(item, resolved)
        elif (error := ambiguous_by_ns[item_ns].get(raw_id)) is not None:
            ambiguous[index] = error
        elif (item_ns, raw_id) not in warned:
            warned.add((item_ns, raw_id))
            logger.warning(
                "Could not resolve %s '%s' in namespace '%s' — passing through",
                entity_type, raw_id, item_ns,
            )
    return ambiguous


async def _resolve_field_door(
    field_door: list[tuple[int, Any, str, str, str]],
    entity_type: str,
    bypass_cache: bool,
    write_back: Callable[[Any, str], None],
) -> dict[int, AmbiguousEntityError]:
    """resolve_bulk_ids' field door: per item — the structured tuple door
    has no batch Registry endpoint yet. If a profile ever shows it hot on a
    bulk path, batching it is a Registry endpoint addition, not a loop
    change. Returns the ambiguous items by index."""
    ambiguous: dict[int, AmbiguousEntityError] = {}
    for index, item, raw_id, item_ns, item_terminology in field_door:
        try:
            resolved = await resolve_term_by_fields(
                raw_id, item_terminology, item_ns, bypass_cache=bypass_cache,
            )
            write_back(item, resolved)
        except AmbiguousEntityError as error:
            # A subclass of the miss: caught first, so it is reported as
            # ambiguous rather than passed on as a value nothing bears.
            ambiguous[index] = error
        except EntityNotFoundError:
            logger.warning(
                "Could not resolve %s '%s' in namespace '%s' — passing through",
                entity_type, raw_id, item_ns,
            )
    return ambiguous


@dataclass(frozen=True)
class AmbiguousItem:
    """A bulk item whose ``field`` holds an identifier several entities bear."""

    field: str
    error: AmbiguousEntityError

    def result_fields(self) -> dict[str, Any]:
        """The item's per-item error: the 409 a single door gives, as fields."""
        return {
            "error": f"{self.field}: {self.error}",
            "error_code": "ambiguous_reference",
            "details": {
                "field": self.field,
                "identifier": self.error.identifier,
                "candidates": self.error.candidates,
                "total": self.error.total,
                "truncated": self.error.truncated,
            },
        }


class AmbiguousItems:
    """A bulk request's items split by what resolve_bulk_ids found ambiguous.

    ``items`` are the rest, to be written as the request's items would be;
    ``answer`` turns the BulkResponse for those back into the response for
    the whole request: every result at its own item's index, and each held
    item answered as ambiguous (status error, code ambiguous_reference).
    An item ambiguous in several fields is answered for the first.

    ``stop_at_first`` is for a request that stops at its first failing item
    (continue_on_error=false): only the items before the first ambiguous one
    are written, and every item after it is skipped — as is the ambiguous
    one itself when an item before it failed.
    """

    def __init__(
        self, items: list, *found: dict[int, AmbiguousItem], stop_at_first: bool = False,
    ):
        held: dict[int, AmbiguousItem] = {}
        for ambiguities in found:
            for index, ambiguity in ambiguities.items():
                held.setdefault(index, ambiguity)
        self.skipped: list[int] = []
        self._stop_at_first = stop_at_first
        if stop_at_first and held:
            first = min(held)
            self.skipped = list(range(first + 1, len(items)))
            held = {first: held[first]}
            self.positions = list(range(first))
        else:
            self.positions = [i for i in range(len(items)) if i not in held]
        self.held = held
        self.items = [items[i] for i in self.positions]

    def answer(self, response: Any) -> Any:
        """``response`` (for ``items``) as the response for every item."""
        if not self.held:
            return response
        item_model = typing.get_args(type(response).model_fields["results"].annotation)[0]
        results = list(response.results)
        stopped = self._stop_at_first and any(r.status in ("error", "skipped") for r in results)
        for result in results:
            result.index = self.positions[result.index]
        skipped = self.skipped + (list(self.held) if stopped else [])
        results += [
            item_model(index=index, status="skipped", error="Stopped due to previous error")
            for index in skipped
        ]
        if not stopped:
            results += [
                item_model(index=index, status="error", **ambiguity.result_fields())
                for index, ambiguity in self.held.items()
            ]
            response.failed += len(self.held)
        results.sort(key=lambda result: result.index)
        response.results = results
        response.total += len(self.held) + len(self.skipped)
        return response
