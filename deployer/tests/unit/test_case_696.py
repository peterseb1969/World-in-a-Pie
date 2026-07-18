"""CASE-696 — --api-keys-file gives a human-friendly way to declare spec
API keys (YAML/JSON), merged with any --api-key JSON values, with clean
parse-time validation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

from wip_deploy.cli import _parse_api_key_options


def _write(tmp_path: Path, text: str) -> str:
    p = tmp_path / "keys.yaml"
    p.write_text(text)
    return str(p)


class TestApiKeysFile:
    def test_keys_list_form(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "keys:\n"
            "  - name: web-yac\n"
            "    namespaces: [library, kb]\n"
            "    grants: {kb: write}\n",
        )
        out = _parse_api_key_options(None, path)
        assert [k["name"] for k in out] == ["web-yac"]
        assert out[0]["grants"] == {"kb": "write"}

    def test_bare_list_form(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path, "- {name: a, namespaces: [wip]}\n- {name: b, namespaces: [wip]}\n"
        )
        out = _parse_api_key_options(None, path)
        assert [k["name"] for k in out] == ["a", "b"]

    def test_file_and_inline_merge(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "keys:\n  - {name: from-file, namespaces: [wip]}\n")
        out = _parse_api_key_options(
            ['{"name": "from-flag", "namespaces": ["wip"]}'], path
        )
        assert {k["name"] for k in out} == {"from-file", "from-flag"}

    def test_grants_outside_namespaces_clean_error(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "keys:\n  - {name: bad, namespaces: [library], grants: {kb: write}}\n",
        )
        with pytest.raises(typer.Exit) as exc:
            _parse_api_key_options(None, path)
        assert exc.value.exit_code == 1

    def test_unreadable_file_errors(self, tmp_path: Path) -> None:
        with pytest.raises(typer.Exit):
            _parse_api_key_options(None, str(tmp_path / "does-not-exist.yaml"))

    def test_malformed_yaml_errors(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "keys: [unterminated\n")
        with pytest.raises(typer.Exit):
            _parse_api_key_options(None, path)

    def test_top_level_not_a_list_errors(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "keys:\n  name: not-a-list\n")
        with pytest.raises(typer.Exit):
            _parse_api_key_options(None, path)

    def test_none_and_none_is_empty(self) -> None:
        assert _parse_api_key_options(None, None) == []
