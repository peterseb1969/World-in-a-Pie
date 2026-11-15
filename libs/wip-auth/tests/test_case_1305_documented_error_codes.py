"""The served error-code list matches what the services stamp on an item.

bulk_models.py states, in text every service publishes, which error_code
values a bulk item carries. Two ways that drift: a code no service emits
survives in the list long after the behaviour retired, and a code a service
starts emitting never gets listed. Both leave callers branching on a
description that does not describe the API.

The emitted side is read from the services' source at the position where a
code is set on an item — the ``error_code=...`` keyword and the
``"error_code": ...`` dict entry — with comments and docstrings tokenised
away, so prose about a code never counts as emitting it.
"""

from __future__ import annotations

import ast
import io
import json
import re
import tokenize
from pathlib import Path

import pytest

from wip_auth import bulk_models
from wip_auth.bulk_models import BulkResultItemBase

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICE_SRC = REPO_ROOT / "components"
DOCSTRING = bulk_models.__doc__ or ""

# Codes set on items of a response model outside this contract, which carries
# its own wording: registry's lookup items report an ambiguous identifier in
# their `status` and name it `ambiguous` there, while the contract's item
# calls the same thing `ambiguous_reference`. def-store's term-validation
# answers (POST /validate and /validate/bulk) return `ambiguous_terminology`,
# documented in their own error_code description beside term_not_found; no
# bulk write item carries it, so this contract's list does not name it.
NON_CONTRACT_CODES = {"ambiguous", "ambiguous_terminology"}

# Retired behaviours. Nothing on any service stamps these any more; they are
# listed because each was once in the documented list and stayed after the
# behaviour it named was gone.
RETIRED_CODES = ("archived", "archived_relationship_endpoint", "incompatible_schema")

DESCRIPTION_PREFIX = "Machine-readable error code. Documented codes: "


def _code_positions(source: str) -> set[str]:
    """Codes at an `error_code=` / `"error_code":` position, ignoring comments
    and strings that are not values (docstrings)."""
    try:
        tokens = [
            t for t in tokenize.generate_tokens(io.StringIO(source).readline)
            if t.type not in (
                tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE,
                tokenize.INDENT, tokenize.DEDENT,
            )
        ]
    except (SyntaxError, tokenize.TokenError):
        return set()
    found: set[str] = set()
    for i, token in enumerate(tokens[:-2]):
        is_key = (token.type == tokenize.NAME and token.string == "error_code") or (
            token.type == tokenize.STRING and ast.literal_eval(token.string) == "error_code"
        )
        if not is_key:
            continue
        if tokens[i + 1].type != tokenize.OP or tokens[i + 1].string not in ("=", ":"):
            continue
        value = tokens[i + 2]
        if value.type == tokenize.STRING:
            found.add(ast.literal_eval(value.string))
    return found


def _emitted_codes() -> dict[str, list[str]]:
    """Every code the services set on a bulk item, with where it is set."""
    emitted: dict[str, set[str]] = {}
    for path in sorted(SERVICE_SRC.glob("*/src/**/*.py")):
        if "/tests/" in str(path):
            continue
        for code in _code_positions(path.read_text(encoding="utf-8", errors="replace")):
            emitted.setdefault(code, set()).add(str(path.relative_to(REPO_ROOT)))
    return {code: sorted(where) for code, where in emitted.items()}


def _documented_codes() -> set[str]:
    description = BulkResultItemBase.model_fields["error_code"].description
    assert description is not None
    assert description.startswith(DESCRIPTION_PREFIX), description[:80]
    codes = re.sub(r"\([^)]*\)", "", description[len(DESCRIPTION_PREFIX):])
    return set(re.findall(r"[a-z][a-z_]+", codes))


def test_every_emitted_code_is_documented():
    documented = _documented_codes()
    undocumented = {c: w for c, w in _emitted_codes().items() if c not in NON_CONTRACT_CODES}
    stale = sorted(c for c in undocumented if c not in documented)
    assert not stale, (
        "services stamp error_code values the published list does not name; add "
        f"them to BulkResultItemBase.error_code: {[(c, undocumented[c]) for c in stale]}"
    )


def test_no_retired_code_is_still_documented():
    documented = _documented_codes()
    emitted = set(_emitted_codes())
    listed = sorted(c for c in RETIRED_CODES if c in documented)
    assert not listed, f"documented codes no service emits: {listed}"
    # And the guard itself is live: the retired codes would be caught.
    assert emitted.isdisjoint(RETIRED_CODES), (
        f"a retired code is emitted again: {sorted(emitted & set(RETIRED_CODES))}"
    )


def test_documented_codes_are_not_a_hollow_list():
    # Guards the containment check above against comparing against nothing.
    documented = _documented_codes()
    assert len(documented) > 20, sorted(documented)
    assert {"not_found", "already_exists", "validation_failed"} <= documented


_CANONICAL_ITEM_RE = re.compile(r"BulkResultItem$")


def _publishers_of_the_description() -> tuple[set[str], dict[str, str]]:
    """Which service specs publish this module's error_code description, and
    which publish other wording for the field."""
    canonical = BulkResultItemBase.model_fields["error_code"].description
    same: set[str] = set()
    other: dict[str, str] = {}
    for path in sorted((REPO_ROOT / "schemas").glob("*.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        descriptions: set[str] = set()
        for name, schema in (spec.get("components", {}).get("schemas", {}) or {}).items():
            if not isinstance(schema, dict):
                continue
            props = schema.get("properties", {}) or {}
            if _CANONICAL_ITEM_RE.search(name):
                descriptions.add((props.get("error_code") or {}).get("description"))
            elif isinstance(props.get("error_code"), dict):
                other.setdefault(path.stem, (props["error_code"].get("description") or "")[:40])
        if canonical in descriptions:
            same.add(path.stem)
    return same, other


@pytest.mark.parametrize("service", ["def-store", "document-store", "registry", "template-store"])
def test_docstring_names_the_services_that_publish_the_description(service):
    """The module docstring promises the list is what the bulk-write services
    publish; the specs are the evidence, and the named set must match them."""
    publishers, _ = _publishers_of_the_description()
    assert service in publishers, f"schemas/{service}.json does not publish the description"
    assert service in DOCSTRING, (
        f"the module docstring must name {service}: it lists the services whose "
        "specs carry this description"
    )


def test_docstring_does_not_claim_more_than_the_specs_show():
    publishers, other_wording = _publishers_of_the_description()
    assert "reporting-sync" not in publishers, "reporting-sync returns no bulk write items"
    assert other_wording, (
        "the docstring explains per-domain wording for error_code; if no service "
        f"adds its own wording any more, drop that sentence: {sorted(other_wording)}"
    )
    for service in other_wording:
        assert service in DOCSTRING, f"{service} publishes its own error_code wording: name it"
