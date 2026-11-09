"""The platform's own vocabularies live in the `wip` namespace, under `_` names.

System terminologies (`_ONTOLOGY_RELATIONSHIP_TYPES`, `_NAME_TYPES`,
`_TIME_UNITS`, …) are seeded into `wip` and read from there only. Terminology
values are unique per namespace, so a lookup by value alone could pick up a
tenant's terminology of the same name — and the seeding check, finding it,
would never create the platform's copy. Values starting with `_` are
therefore reserved to `wip`: no other namespace may create or rename a
terminology to one.
"""

from ..models.terminology import Terminology
from .errors import DefStoreError

SYSTEM_NAMESPACE = "wip"
RESERVED_PREFIX = "_"


async def find_system_terminology(value: str) -> Terminology | None:
    """The platform's terminology of this value, in `wip`, or None."""
    return await Terminology.find_one({"namespace": SYSTEM_NAMESPACE, "value": value})


def refuse_reserved_value(namespace: str, value: str | None) -> None:
    """Refuse a `_`-prefixed terminology value outside `wip`."""
    if value and value.startswith(RESERVED_PREFIX) and namespace != SYSTEM_NAMESPACE:
        raise DefStoreError(
            f"Terminology values starting with '{RESERVED_PREFIX}' are reserved for the "
            f"platform's own vocabularies in namespace '{SYSTEM_NAMESPACE}'; "
            f"'{value}' cannot be used in namespace '{namespace}'",
            "validation_failed", rule="reserved_value",
        )
