"""The deployer's reserved groups are the platform's privileged groups.

wip-deploy does not depend on wip-auth, so it carries its own copy of the
set (RESERVED_GROUPS) and renders it into the federation policy. This
reads wip-auth's definition from the repository and fails when the two
drift apart: a group wip-auth treats as privileged that a federated login
could still be handed would be an escalation path.
"""

import ast
from pathlib import Path

from wip_deploy.spec.deployment import RESERVED_GROUPS

GROUPS_PY = Path(__file__).resolve().parents[4] / "libs" / "wip-auth" / "src" / "wip_auth" / "groups.py"


def _constants() -> dict[str, str]:
    tree = ast.parse(GROUPS_PY.read_text(encoding="utf-8"))
    found: dict[str, str] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            found[node.target.id] = node.value.value
    return found


def test_reserved_groups_are_wip_auths_privileged_groups():
    names = _constants()
    assert set(RESERVED_GROUPS) == {names["ADMIN_GROUP"], names["SERVICES_GROUP"]}
