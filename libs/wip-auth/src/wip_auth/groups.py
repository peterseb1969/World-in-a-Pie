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
# their keys may be unscoped (namespaces=None), and they pass the
# platform-level gates (system endpoints, reserved synonyms, stack-wide
# backup). Not a permission on namespace data: that is the Registry's
# answer per request.
PRIVILEGED_GROUPS: Final = frozenset({ADMIN_GROUP, SERVICES_GROUP})
