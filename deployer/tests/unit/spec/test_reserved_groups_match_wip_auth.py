"""The deployer's reserved groups are the platform's privileged groups.

wip-deploy does not depend on wip-auth, so it carries its own copy of the
set (RESERVED_GROUPS) and renders it into the federation policy. This
reads wip-auth's definition from the repository and fails when the two
drift apart: a group wip-auth treats as privileged that a federated login
could still be handed would be an escalation path.
"""

import ast
from pathlib import Path

from wip_deploy.spec.deployment import ADMIN_GROUP, RESERVED_GROUPS, SERVICES_GROUP

GROUPS_PY = Path(__file__).resolve().parents[4] / "libs" / "wip-auth" / "src" / "wip_auth" / "groups.py"


def _module() -> ast.Module:
    return ast.parse(GROUPS_PY.read_text(encoding="utf-8"))


def _constants(tree: ast.Module) -> dict[str, str]:
    """The module's top-level string constants, by name."""
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


def _privileged_groups(tree: ast.Module) -> set[str]:
    """The members of PRIVILEGED_GROUPS as wip-auth builds it: each element
    of its frozenset/set literal, a name resolved through the module's
    constants or a string written in place. Fails on any other shape, so a
    rewrite of the definition cannot slip past the pin unread."""
    constants = _constants(tree)
    for node in tree.body:
        target = node.target if isinstance(node, ast.AnnAssign) else (
            node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else None
        )
        if not (isinstance(target, ast.Name) and target.id == "PRIVILEGED_GROUPS"):
            continue
        value = node.value
        if (
            isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
            and value.func.id in {"frozenset", "set"} and len(value.args) == 1
        ):
            value = value.args[0]
        assert isinstance(value, ast.Set | ast.Tuple | ast.List), (
            f"PRIVILEGED_GROUPS has a shape this pin does not read: {ast.dump(node.value)}"
        )
        members: set[str] = set()
        for element in value.elts:
            if isinstance(element, ast.Name):
                members.add(constants[element.id])
            elif isinstance(element, ast.Constant) and isinstance(element.value, str):
                members.add(element.value)
            else:
                raise AssertionError(f"unreadable PRIVILEGED_GROUPS member: {ast.dump(element)}")
        return members
    raise AssertionError(f"PRIVILEGED_GROUPS not found in {GROUPS_PY}")


def test_reserved_groups_are_wip_auths_privileged_groups():
    assert set(RESERVED_GROUPS) == _privileged_groups(_module())


def test_the_admin_and_services_names_are_wip_auths():
    constants = _constants(_module())
    assert constants["ADMIN_GROUP"] == ADMIN_GROUP
    assert constants["SERVICES_GROUP"] == SERVICES_GROUP
