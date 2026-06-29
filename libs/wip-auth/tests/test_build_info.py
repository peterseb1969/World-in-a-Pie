"""Tests for the uniform build-provenance block (CASE-526)."""

from __future__ import annotations

import pytest

from wip_auth import build_metadata
from wip_auth.build_info import DEV_SENTINEL

_VARS = ("WIP_BUILD_SHA", "WIP_BUILD_STAMP", "WIP_IMAGE_TAG")


@pytest.fixture(autouse=True)
def _clear_build_env(monkeypatch):
    for v in _VARS:
        monkeypatch.delenv(v, raising=False)


def test_defaults_to_dev_sentinel_when_unstamped():
    b = build_metadata("1.2.3")
    assert b == {
        "version": "1.2.3",
        "sha": DEV_SENTINEL,
        "built_at": DEV_SENTINEL,
        "image_tag": DEV_SENTINEL,
    }


def test_reads_build_env_when_stamped(monkeypatch):
    monkeypatch.setenv("WIP_BUILD_SHA", "abc1234")
    monkeypatch.setenv("WIP_BUILD_STAMP", "2026-06-29T12:00:00Z")
    monkeypatch.setenv("WIP_IMAGE_TAG", "1.0.0")
    b = build_metadata("1.2.3")
    assert b == {
        "version": "1.2.3",
        "sha": "abc1234",
        "built_at": "2026-06-29T12:00:00Z",
        "image_tag": "1.0.0",
    }


def test_version_is_passed_through_not_from_env():
    # The package __version__ is the caller's, never overridden by build env.
    b = build_metadata("9.9.9")
    assert b["version"] == "9.9.9"
