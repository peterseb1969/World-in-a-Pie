"""The namespace a composite key names, and the rule that it is its entry's.

Key claims are unique per (namespace, entity_type, key hash), so the Registry
keeps one key apart in two namespaces by the entry that holds it. The keys the
platform builds also NAME their namespace: a template's is
``{"ns": <namespace>, "type": "template", "value": ...}``, a terminology's and
a term's carry ``ns`` too. A key whose ``ns`` differs from its entry's
namespace is therefore claimed in one namespace while naming another, and a
lookup by that key (which hashes the key, not its holder) finds both entries:
a writer of namespace Y could plant X's template key on a Y entry and have X's
references resolve into Y. So a key that carries ``ns`` must carry its own
entry's namespace, and a lookup by such a key is scoped to the namespace it
names.

Exempt are system-qualified keys, those with a member starting with "@" (a
document's identity synonym, ``{"@template": <value>, <field>: <value>}``).
Their other members are the document's own identity fields, which may well be
called ``ns``; only system callers may write such keys.
"""

from typing import Any


def _is_system_qualified(key: dict[str, Any]) -> bool:
    return any(isinstance(k, str) and k.startswith("@") for k in key)


def named_namespace(key: dict[str, Any] | None) -> str | None:
    """The namespace a composite key names, or None when it names none."""
    if not key or _is_system_qualified(key):
        return None
    ns = key.get("ns")
    return ns if isinstance(ns, str) and ns else None


def key_scope_refusal(
    key: dict[str, Any] | None, namespace: str, *, rule: str = "key_scope_mismatch",
) -> dict[str, Any] | None:
    """The per-item refusal for a key whose ``ns`` is not its entry's
    ``namespace``, or None when the key may be written there."""
    if not key or _is_system_qualified(key) or "ns" not in key or key["ns"] == namespace:
        return None
    return {
        "error": (
            f"A composite key's 'ns' must be its entry's namespace '{namespace}' "
            f"(got {key['ns']!r}): the key would name one namespace and be held in another."
        ),
        "error_code": "invalid_request",
        "details": {"rule": rule, "namespace": namespace, "key_namespace": key["ns"]},
    }
