"""Lockstep guard for WIP's two PoNIF surfaces.

The PoNIFs are published twice: as the served MCP resource ``wip://ponifs``
(a string returned by ``wip_mcp.server.get_ponifs``) and as prose in
``docs/WIP_PoNIFs.md``. The two are maintained by hand and independently, which
is a drift generator: a contract added to one surface silently misses the other,
and nothing fails. That is not hypothetical — a commit adding the privileged
`rollback_uncommitted` deletion deviation landed on the served resource only, and
CI stayed green.

These tests assert *structural* agreement, not textual equality: the prose is
deliberately longer and differently worded. What must hold is that both surfaces
declare the same set of PoNIFs and that every load-bearing invariant named below
appears in both. When you add or change a PoNIF invariant, update BOTH surfaces —
this test is how you find out that you didn't.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import wip_mcp.server as server_module

REPO_ROOT = Path(__file__).resolve().parents[3]
PROSE_PATH = REPO_ROOT / "docs" / "WIP_PoNIFs.md"

# Invariants that must be documented on BOTH surfaces. Each entry is a marker
# whose absence from either copy means one surface is teaching a contract the
# other omits. Keep this list as the checklist for "what a PoNIF reader must
# not miss" — add to it when a new cross-cutting invariant ships.
REQUIRED_ON_BOTH_SURFACES = [
    "deletion_mode",      # PoNIF #1 — "nothing ever dies" is the default, not absolute
    "rollback",           # PoNIF #1 — privileged uncommitted-entry bypass
    "mutable",            # PoNIF #1 — mutable-terminology terms are hard-deletable
    "append_only",        # PoNIF #3 — identity-less templates reject PATCH
    "identity_fields",    # PoNIF #3 / #8 — identity drives dedup; #8 requires non-empty
    "versioned",          # PoNIF #8 — overwrite-in-place opt-out
    "usage",              # PoNIF #7 — entity vs relationship templates
]


def _served_ponifs() -> str:
    """The served wip://ponifs text (unwrapped from the FastMCP decorator)."""
    fn = server_module.get_ponifs
    fn = getattr(fn, "fn", None) or getattr(fn, "__wrapped__", None) or fn
    return fn()


def _prose_ponifs() -> str:
    return PROSE_PATH.read_text(encoding="utf-8")


def _numbers(text: str) -> set[int]:
    """PoNIF numbers declared by markdown headings (## 1. / ### 1.)."""
    return {int(m) for m in re.findall(r"^#{2,3}\s+(\d+)\.\s", text, re.MULTILINE)}


def test_prose_surface_exists():
    assert PROSE_PATH.is_file(), f"prose PoNIF copy missing at {PROSE_PATH}"


def test_both_surfaces_declare_the_same_ponif_numbers():
    """A PoNIF added to one surface only is the drift this guard exists to catch."""
    served = _numbers(_served_ponifs())
    prose = _numbers(_prose_ponifs())

    assert served, "no numbered PoNIF headings found in the served resource"
    assert served == prose, (
        "PoNIF surfaces disagree on which PoNIFs exist — "
        f"served-only={sorted(served - prose)}, prose-only={sorted(prose - served)}. "
        "Update both wip_mcp/server.py (wip://ponifs) and docs/WIP_PoNIFs.md."
    )


@pytest.mark.parametrize("marker", REQUIRED_ON_BOTH_SURFACES)
def test_invariant_is_documented_on_both_surfaces(marker: str):
    """Each load-bearing invariant must appear in the served AND the prose copy."""
    served = _served_ponifs()
    prose = _prose_ponifs()

    in_served = marker in served
    in_prose = marker in prose

    assert in_served and in_prose, (
        f"PoNIF invariant '{marker}' is documented on only one surface "
        f"(served={in_served}, prose={in_prose}). Both wip://ponifs "
        f"(components/mcp-server/src/wip_mcp/server.py) and docs/WIP_PoNIFs.md "
        f"must carry it."
    )
