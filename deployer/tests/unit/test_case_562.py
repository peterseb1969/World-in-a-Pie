"""CASE-562: find_manifest_for_source treated exact-normalized and
substring matches as equal strength, so a parallel-instance manifest
(apps/dev-kb, deliberately alongside the canonical apps/wip-kb for the
same app source) made package name 'kb' ambiguous — failing
check_app_deployability on the zero-config path and turning the local
deployer suite red on any machine with a WIP-KB checkout.

Fix under test: an exact normalized match outranks substring matches; a
lone exact candidate wins the tie. Multiple exacts stay a loud
ambiguity, and substring-only resolution is unchanged (the
hyphenation-drift cases test_case_379 guards).
"""

from __future__ import annotations

from pathlib import Path

from wip_deploy.check_app import find_manifest_for_source

from .test_case_379 import _make_app_source, _make_manifest


class TestExactBeatsSubstring:
    def test_canonical_wins_over_parallel_instance(self, tmp_path: Path) -> None:
        """The live failure: pkg 'kb' vs apps/wip-kb (exact after
        normalization) + apps/dev-kb (substring only)."""
        src = _make_app_source(tmp_path, pkg_name="kb")
        _make_manifest(tmp_path, name="wip-kb", http_port=3012)
        _make_manifest(tmp_path, name="dev-kb", http_port=3012)
        m, name, note = find_manifest_for_source(src, tmp_path)
        assert note is None
        assert name == "wip-kb"
        assert m == tmp_path / "apps" / "wip-kb" / "wip-app.yaml"

    def test_order_independent(self, tmp_path: Path) -> None:
        """dev-kb sorts before wip-kb in the glob; the exact rank must
        decide, not scan order. Also cover a suffix twin."""
        src = _make_app_source(tmp_path, pkg_name="kb")
        _make_manifest(tmp_path, name="kb-staging", http_port=3012)
        _make_manifest(tmp_path, name="wip-kb", http_port=3012)
        m, name, note = find_manifest_for_source(src, tmp_path)
        assert note is None
        assert name == "wip-kb"

    def test_two_exact_matches_still_ambiguous(self, tmp_path: Path) -> None:
        """apps/kb and apps/wip-kb both normalize to 'kb' — genuinely
        ambiguous; the error must list only the exact contenders.

        NB: apps/kb/ would normally win the stage-1 direct check; point
        the source at a pkg name matching neither dir literally."""
        src = _make_app_source(tmp_path, pkg_name="wip-kb")
        _make_manifest(tmp_path, name="kb", http_port=3012)
        _make_manifest(tmp_path, name="dev-kb", http_port=3012)
        # pkg 'wip-kb' normalizes to 'kb': apps/kb exact, apps/dev-kb
        # substring — exact wins, no ambiguity...
        m, name, note = find_manifest_for_source(src, tmp_path)
        assert note is None
        assert name == "kb"
        # ...now add a second exact contender ('k-b' also normalizes to
        # 'kb'; 'wipkb' would NOT — no hyphen, so the wip- strip misses
        # and it stays a substring match) and it must go ambiguous,
        # listing only the exacts (not the substring dev-kb).
        _make_manifest(tmp_path, name="k-b", http_port=3012)
        m, name, note = find_manifest_for_source(src, tmp_path)
        assert m is None
        assert note is not None and "ambiguous" in note
        assert "dev-kb" not in note

    def test_substring_only_resolution_unchanged(self, tmp_path: Path) -> None:
        """No exact candidate: a lone substring match resolves exactly
        as before (test_case_379's hyphenation-drift contract)."""
        src = _make_app_source(tmp_path, pkg_name="clintrial-explorer")
        _make_manifest(tmp_path, name="clintrial", http_port=3001)
        m, name, note = find_manifest_for_source(src, tmp_path)
        assert note is None
        assert name == "clintrial"

    def test_multiple_substring_only_still_ambiguous(self, tmp_path: Path) -> None:
        """'clintrial' and 'explorer' are both substrings of the
        normalized pkg name with no exact candidate — stays ambiguous.
        (A name like 'clintrial-viewer' would not match at all: neither
        normalized string contains the other.)"""
        src = _make_app_source(tmp_path, pkg_name="clintrial-explorer")
        _make_manifest(tmp_path, name="clintrial", http_port=3001)
        _make_manifest(tmp_path, name="explorer", http_port=3002)
        m, name, note = find_manifest_for_source(src, tmp_path)
        assert m is None
        assert note is not None and "ambiguous" in note
