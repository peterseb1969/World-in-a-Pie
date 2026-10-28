"""Drift guard for the rule cards in ``docs/cards/``.

A card condenses longer documents (Vision, the PoNIFs, the served MCP
resources, the deployable-app contract, ...) into the few rules an app agent
reads instead of them. A card that silently falls behind its sources teaches
the old rule with full authority, so each card records, in a ``card-sources``
comment at its top, every source it condenses with a hash of that source and
the commit at which the card was last reviewed against it:

    docs/Vision.md sha256:<first 16 hex> reviewed:<commit>
    wip://ponifs sha256:<first 16 hex> reviewed:<commit>

A file source is hashed as bytes. A ``wip://`` source is hashed as the text the
MCP server serves, not as ``server.py``, which changes for unrelated reasons
every few days. When a source changes, the test fails and names the review to
do: diff the source since the recorded commit, update the card if the change
touches a rule on it, then record the new hash.

The cards also hold a size budget (they exist to be short) and carry no case
tokens or scaffold placeholders: apps receive them byte for byte, and their
readers may have no KB.

This lives in the mcp-server suite because it is the one suite that imports
the served resource texts.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

import pytest

import wip_mcp.server as server_module

REPO_ROOT = Path(__file__).resolve().parents[3]
CARDS_DIR = REPO_ROOT / "docs" / "cards"
SERVER_PATH = "components/mcp-server/src/wip_mcp/server.py"

# Card -> size budget in characters (about 4 characters per token).
CARD_BUDGETS = {
    "core.md": 8_000,
    "model.md": 10_000,
    "build.md": 10_000,
    "ship.md": 6_000,
}

RESOURCE_FUNCTIONS = {
    "wip://conventions": "get_conventions",
    "wip://data-model": "get_data_model",
    "wip://development-guide": "get_development_guide",
    "wip://ponifs": "get_ponifs",
}

HEADER_RE = re.compile(r"\A<!-- card-sources:.*?\n(.*?)-->", re.DOTALL)
SOURCE_LINE_RE = re.compile(r"^(\S+) sha256:(\S+) reviewed:(\S+)$")


def _resource_text(uri: str) -> str:
    fn = getattr(server_module, RESOURCE_FUNCTIONS[uri])
    fn = getattr(fn, "fn", None) or getattr(fn, "__wrapped__", None) or fn
    return fn()


def _current_hash(source: str) -> str:
    if source.startswith("wip://"):
        return hashlib.sha256(_resource_text(source).encode("utf-8")).hexdigest()[:16]
    return hashlib.sha256((REPO_ROOT / source).read_bytes()).hexdigest()[:16]


def _sources(card: str) -> list[tuple[str, str, str]]:
    text = (CARDS_DIR / card).read_text(encoding="utf-8")
    match = HEADER_RE.match(text)
    if not match:
        return []
    out = []
    for line in match.group(1).splitlines():
        parsed = SOURCE_LINE_RE.match(line.strip())
        if parsed:
            out.append(parsed.groups())
    return out


def _head() -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "<current commit>"


def _source_pairs() -> list:
    pairs = []
    for card in CARD_BUDGETS:
        if (CARDS_DIR / card).is_file():
            pairs.extend(
                pytest.param(card, *entry, id=f"{card}:{entry[0]}") for entry in _sources(card)
            )
    return pairs


@pytest.mark.parametrize("card", sorted(CARD_BUDGETS))
def test_card_names_its_sources(card):
    assert (CARDS_DIR / card).is_file(), f"docs/cards/{card} is missing"
    sources = _sources(card)
    assert sources, (
        f"docs/cards/{card} must open with a <!-- card-sources: ... --> comment "
        "listing each source as '<path or wip:// uri> sha256:<hash> reviewed:<commit>'"
    )
    for source, _, _ in sources:
        if source.startswith("wip://"):
            assert source in RESOURCE_FUNCTIONS, f"docs/cards/{card}: unknown resource {source}"
        else:
            assert (REPO_ROOT / source).is_file(), f"docs/cards/{card}: source {source} does not exist"


@pytest.mark.parametrize(
    ("card", "source", "recorded", "reviewed"),
    _source_pairs(),
)
def test_card_reviewed_against_current_source(card, source, recorded, reviewed):
    current = _current_hash(source)
    if current == recorded:
        return
    diff_path = SERVER_PATH if source.startswith("wip://") else source
    what = f"the served {source} text" if source.startswith("wip://") else source
    pytest.fail(
        f"docs/cards/{card}: {what} changed since the card was reviewed.\n"
        f"  1. Run `git diff {reviewed} -- {diff_path}` and read what changed.\n"
        f"  2. Update docs/cards/{card} if the change touches a rule on the card.\n"
        f"  3. Then replace the card's source line with:\n"
        f"     {source} sha256:{current} reviewed:{_head()}"
    )


@pytest.mark.parametrize("card", sorted(CARD_BUDGETS))
def test_card_within_budget(card):
    size = len((CARDS_DIR / card).read_text(encoding="utf-8"))
    budget = CARD_BUDGETS[card]
    assert size <= budget, (
        f"docs/cards/{card} is {size} characters, over its {budget} budget. "
        "A card holds only the rules that prevent a wrong action; move detail to the depth documents."
    )


@pytest.mark.parametrize("card", sorted(CARD_BUDGETS))
def test_card_carries_no_case_tokens_or_placeholders(card):
    text = (CARDS_DIR / card).read_text(encoding="utf-8")
    assert not re.search(r"\bCASE-\d+", text), (
        f"docs/cards/{card} cites a case number; its readers may have no KB — state the rule in full"
    )
    placeholder = re.search(r"__[A-Z][A-Z_]*__", text)
    assert not placeholder, (
        f"docs/cards/{card} contains {placeholder.group(0)}; cards are copied into apps verbatim, "
        "never rendered"
    )
