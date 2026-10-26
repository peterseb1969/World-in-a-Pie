"""The gateway applies the federated group policy file without a restart (CASE-947).

The policy is a file the deployer renders and re-renders on every
`wip-deploy group-map` change. The gateway reads it on each login callback,
re-reading only when the file changed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from auth_gateway import oidc
from auth_gateway.config import settings

FEDERATED = {"email": "ann@corp.example", "federated_claims": {"connector_id": "google"}}


def _write(path: Path, group: str | None) -> None:
    text = "" if group is None else json.dumps({
        "local_connectors": ["local"],
        "reserved_groups": ["wip-admins"],
        "mappings": [{"email": "ann@corp.example", "group": group}],
    })
    path.write_text(text)
    # A distinct mtime even within one clock tick.
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))


@pytest.fixture
def policy_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "federated-groups.json"
    monkeypatch.setattr(settings, "federated_groups_file", str(path))
    monkeypatch.setattr(settings, "federated_groups", "")
    oidc._policy_cache.clear()
    return path


def _groups() -> list[str] | None:
    policy = oidc.current_federated_policy()
    return None if policy is None else policy.apply(FEDERATED, ["wip-admins"])


def test_a_mapping_change_reaches_the_next_login(policy_file: Path) -> None:
    _write(policy_file, "wip-editors")
    assert _groups() == ["wip-editors"]
    _write(policy_file, "wip-viewers")
    assert _groups() == ["wip-viewers"]


def test_an_empty_file_means_no_policy(policy_file: Path) -> None:
    _write(policy_file, None)
    assert oidc.current_federated_policy() is None


def test_a_broken_or_missing_file_keeps_the_last_policy(policy_file: Path) -> None:
    _write(policy_file, "wip-editors")
    assert _groups() == ["wip-editors"]
    policy_file.write_text('{"mappings": [')
    assert _groups() == ["wip-editors"]
    policy_file.unlink()
    assert _groups() == ["wip-editors"]


def test_the_file_wins_over_the_env_policy(policy_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "federated_groups", json.dumps({
        "mappings": [{"email": "ann@corp.example", "group": "from-env"}],
    }))
    _write(policy_file, "from-file")
    assert _groups() == ["from-file"]
