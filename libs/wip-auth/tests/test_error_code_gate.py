"""The error-code gate: every bulk error item names an error_code.

Provenance: CASE-934 — def-store, document-store and registry built error
items with a message and no code, so callers could only match English; a
change shipped the day after template-store's fix added two more.
"""

from pathlib import Path

import pytest

from wip_auth.testing import (
    assert_error_items_carry_codes,
    collect_codeless_error_items,
)


def _package(tmp_path: Path, source: str) -> Path:
    pkg = tmp_path / "svc"
    pkg.mkdir()
    (pkg / "api.py").write_text(source)
    return pkg


def test_flags_call_without_code(tmp_path):
    pkg = _package(tmp_path, 'Item(index=0, status="error", error="nope")\n')
    offenders, examined = collect_codeless_error_items(pkg)
    assert offenders == ["api.py:1"]
    assert examined == 1


def test_flags_enum_status_and_dict_literal(tmp_path):
    pkg = _package(tmp_path, (
        'Item(index=0, status=ItemStatus.ERROR, error="nope")\n'
        'results.append({"index": 1, "status": "error", "error": "nope"})\n'
    ))
    offenders, examined = collect_codeless_error_items(pkg)
    assert offenders == ["api.py:1", "api.py:2"]
    assert examined == 2


def test_accepts_named_code_and_unpacked_fields(tmp_path):
    pkg = _package(tmp_path, (
        'Item(index=0, status="error", error="nope", error_code="not_found")\n'
        'Item(index=1, status="error", **error_fields(exc))\n'
        'results.append({"status": "error", "error": "x", "error_code": "invalid_request"})\n'
        'results.append({"status": "error", **fields})\n'
    ))
    offenders, examined = collect_codeless_error_items(pkg)
    assert offenders == []
    assert examined == 4


def test_flags_an_explicit_none_code(tmp_path):
    pkg = _package(tmp_path, (
        'Item(status="error", error="a", error_code=None)\n'
        'results.append({"status": "error", "error": "b", "error_code": None})\n'
        'Item(status="error", error="c", error_code=code)\n'
    ))
    offenders, examined = collect_codeless_error_items(pkg)
    assert offenders == ["api.py:1", "api.py:2"]
    assert examined == 3


def test_ignores_non_error_statuses(tmp_path):
    pkg = _package(tmp_path, (
        'Item(index=0, status="created")\n'
        'Item(index=1, status="already_exists")\n'
        'Item(index=2, status=status)\n'
    ))
    assert collect_codeless_error_items(pkg) == ([], 0)


def test_walks_subpackages(tmp_path):
    pkg = _package(tmp_path, "")
    (pkg / "services").mkdir()
    (pkg / "services" / "svc.py").write_text('\n\nItem(status="error", error="x")\n')
    offenders, _ = collect_codeless_error_items(pkg)
    assert offenders == ["services/svc.py:3"]


def test_assert_names_every_offender(tmp_path):
    pkg = _package(tmp_path, (
        'Item(status="error", error="a")\n'
        'Item(status="error", error="b", error_code="validation_failed")\n'
        'Item(status="error", error="c")\n'
    ))
    with pytest.raises(AssertionError) as exc:
        assert_error_items_carry_codes(pkg)
    assert "api.py:1" in str(exc.value)
    assert "api.py:3" in str(exc.value)
    assert "api.py:2" not in str(exc.value)


def test_canary_fails_an_empty_walk(tmp_path):
    pkg = _package(tmp_path, 'Item(status="created")\n')
    with pytest.raises(AssertionError, match="ZERO error items"):
        assert_error_items_carry_codes(pkg)
    assert_error_items_carry_codes(pkg, expect_error_items=False)
