"""The platform's own group names.

Fixed names, not settings: the Registry, every store, the auth gateway and
the deployer all recognise these groups by name, so a service that spelled
one differently would disagree with the authority it asks. Every Python
service takes them from here; a source check in each suite
(``wip_auth.testing.assert_no_privileged_group_literals``) fails on a new
inline spelling, so the copies cannot drift apart again.

Leaf module: no imports from the rest of wip_auth, so providers, policies
and config can all use it without import order mattering.
"""

from typing import Final

# Platform administrators: admin on every namespace (a namespace locked for
# deletion excepted — the Registry answers for that).
ADMIN_GROUP: Final = "wip-admins"

# The platform's own services, calling each other with the install's keys.
SERVICES_GROUP: Final = "wip-services"

# Groups whose callers act for the platform rather than for one namespace:
# their keys are never scoped (namespaces=None, see privileged_scope_error),
# and they pass the platform-level gates (system endpoints, reserved
# synonyms, stack-wide backup). Not a permission on namespace data: that is
# the Registry's answer per request.
PRIVILEGED_GROUPS: Final = frozenset({ADMIN_GROUP, SERVICES_GROUP})


def privileged_scope_error(groups: list[str] | None, namespaces: list[str] | None) -> str | None:
    """Why a key in these groups may not carry this namespaces list, or None.

    A key in a privileged group is the platform acting on every namespace,
    so it is never scoped: a list on it, an empty one included, would scope
    it at some doors (a store's per-namespace permission) and not at others
    (the Registry's platform doors), a key no door agrees about. A key that
    needs a namespace scope carries no privileged group and gets its access
    through grants.
    """
    if namespaces is None:
        return None
    held = sorted(set(groups or []) & PRIVILEGED_GROUPS)
    if not held:
        return None
    return (
        f"a key in {', '.join(held)} acts on every namespace and is never "
        f"scoped, so it cannot carry `namespaces` ({namespaces!r}); drop the "
        "list, or drop the group and give the key grants"
    )
