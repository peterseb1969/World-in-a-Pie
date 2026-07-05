"""Unit tests for the surface engine.

Pure — no podman, no network, no scratch clones; these run in CI
unconditionally (unlike the WIP_GOLDEN-gated snapshot tests). Each policy
test pins the incident behavior that motivated it, written out in the
test itself, so the *why* survives refactors.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from wip_scaffold.engine import Context, Policy, Surface, run_surfaces
from wip_scaffold.render import render, substitute, tier_filter
from wip_scaffold.surfaces import APP_REFERENCE_DOCS, app_surfaces, backend_surfaces

REPO_ROOT = Path(__file__).resolve().parents[2]


# --- render ------------------------------------------------------------------

def test_substitute_is_literal_not_regex():
    # Values with sed/regex metacharacters must pass through untouched —
    # the reason the app render is python replace(), not sed (step 2).
    out = substitute("x __T__ y", {"__T__": r"a|b&c\1$HOME"})
    assert out == r"x a|b&c\1$HOME y"


def test_tier_filter_tier3_strips_markers_keeps_content():
    text = "a\n<!--TIER3-->\nkb-only\n<!--/TIER3-->\nb\n"
    assert tier_filter(text, tier3=True) == "a\nkb-only\nb\n"


def test_tier_filter_tier2_drops_region_inclusive():
    text = "a\n<!--TIER3-->\nkb-only\n<!--/TIER3-->\nb\n"
    assert tier_filter(text, tier3=False) == "a\nb\n"


def test_tier_filter_marker_must_be_whole_line():
    # The bash sed anchors ^...$ — an inline mention is NOT a marker.
    text = "see <!--TIER3--> inline\n"
    assert tier_filter(text, tier3=False) == text


def test_render_composes():
    text = "__X__\n<!--TIER3-->\nt3\n<!--/TIER3-->\n"
    assert render(text, {"__X__": "v"}, tier3=False) == "v\n"
    assert render(text, {"__X__": "v"}, tier3=True) == "v\nt3\n"


# --- engine core -------------------------------------------------------------

def _ctx(tmp_path: Path, **kw) -> Context:
    return Context(wip_root=REPO_ROOT, target_root=tmp_path, **kw)


def _surface(name="s", policy=Policy.REGENERATE, files=None, **kw) -> Surface:
    files = files if files is not None else {"out.txt": b"data"}
    return Surface(name=name, policy=policy, produce=lambda ctx: dict(files), **kw)


def test_write_is_atomic_no_partial_on_failure(tmp_path):
    # Failure inside produce of a LATER surface must not corrupt an
    # earlier surface's completed write, and no temp litter remains.
    def boom(ctx):
        raise RuntimeError("producer failed")

    ok = _surface(files={"a.txt": b"A"})
    bad = Surface(name="bad", policy=Policy.REGENERATE, produce=boom)
    with pytest.raises(RuntimeError):
        run_surfaces([ok, bad], _ctx(tmp_path))
    assert (tmp_path / "a.txt").read_bytes() == b"A"
    litter = [p for p in tmp_path.rglob(".*") if p.is_file() and p.name.startswith(".")]
    assert litter == [], f"temp litter: {litter}"


def test_dry_run_touches_nothing(tmp_path):
    (tmp_path / "old.md").write_text("keep")
    s = _surface(files={"new.txt": b"x"}, wipe_glob="*.md")
    log = run_surfaces([s], _ctx(tmp_path, dry_run=True))
    assert (tmp_path / "old.md").exists(), "dry-run removed a file"
    assert not (tmp_path / "new.txt").exists(), "dry-run wrote a file"
    assert any("would remove" in l for l in log)
    assert any("would write" in l for l in log)


def test_idempotent_second_run_identical(tmp_path):
    s = _surface(files={"a/b.txt": b"stable"})
    run_surfaces([s], _ctx(tmp_path))
    first = (tmp_path / "a/b.txt").read_bytes()
    run_surfaces([s], _ctx(tmp_path))
    assert (tmp_path / "a/b.txt").read_bytes() == first


def test_wipe_glob_removes_stale_dest_files(tmp_path):
    # The commands-dir contract: retired gene-pool commands must not linger.
    (tmp_path / ".claude/commands").mkdir(parents=True)
    (tmp_path / ".claude/commands/retired.md").write_text("old")
    s = _surface(
        files={".claude/commands/current.md": b"new"},
        wipe_glob=".claude/commands/*.md",
    )
    run_surfaces([s], _ctx(tmp_path))
    assert not (tmp_path / ".claude/commands/retired.md").exists()
    assert (tmp_path / ".claude/commands/current.md").exists()


def test_executable_bit(tmp_path):
    s = _surface(files={"hook.sh": b"#!/bin/sh\n"}, executable=True)
    run_surfaces([s], _ctx(tmp_path))
    mode = os.stat(tmp_path / "hook.sh").st_mode
    assert mode & stat.S_IXUSR


def test_render_refresh_create_writes_target(tmp_path):
    s = _surface(policy=Policy.RENDER_REFRESH, files={"CLAUDE.md": b"gen"})
    run_surfaces([s], _ctx(tmp_path, refresh=False))
    assert (tmp_path / "CLAUDE.md").read_bytes() == b"gen"
    assert not (tmp_path / "CLAUDE.md.refresh").exists()


def test_render_refresh_existing_writes_sidecar(tmp_path):
    # App-authored CLAUDE.md is sacred on refresh — a silent overwrite
    # would destroy customisation; the render goes to a sidecar instead.
    (tmp_path / "CLAUDE.md").write_text("app-authored")
    s = _surface(policy=Policy.RENDER_REFRESH, files={"CLAUDE.md": b"gen"})
    ctx = _ctx(tmp_path, refresh=True)
    run_surfaces([s], ctx)
    assert (tmp_path / "CLAUDE.md").read_text() == "app-authored"
    assert (tmp_path / "CLAUDE.md.refresh").read_bytes() == b"gen"
    assert any(".refresh" in n for n in ctx.notes)


def test_render_refresh_force_overwrites(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("app-authored")
    s = _surface(policy=Policy.RENDER_REFRESH, files={"CLAUDE.md": b"gen"})
    run_surfaces([s], _ctx(tmp_path, refresh=True, force_claude_md=True))
    assert (tmp_path / "CLAUDE.md").read_bytes() == b"gen"
    assert not (tmp_path / "CLAUDE.md.refresh").exists()


def test_render_refresh_missing_target_on_refresh_writes_target(tmp_path):
    # Refresh of a dir that never had a CLAUDE.md: generate it directly
    # (nothing to clobber) — the create-app-project.sh contract.
    s = _surface(policy=Policy.RENDER_REFRESH, files={"CLAUDE.md": b"gen"})
    run_surfaces([s], _ctx(tmp_path, refresh=True))
    assert (tmp_path / "CLAUDE.md").read_bytes() == b"gen"
    assert not (tmp_path / "CLAUDE.md.refresh").exists()


# --- matrix sanity -----------------------------------------------------------

def test_matrix_names_unique_and_rationale_present():
    for surfaces in (backend_surfaces(), app_surfaces({"APP_NAME": "x", "APP_SLUG": "x", "DEV_NAMESPACE": "x", "PRESET": "standard"})):
        names = [s.name for s in surfaces]
        assert len(names) == len(set(names))
        for s in surfaces:
            assert s.rationale, f"surface {s.name} lacks a rationale"


def test_matrix_sources_exist_in_repo():
    # Template/source files every producer reads must exist — a renamed
    # gene-pool source should fail HERE, not at scaffold time on a user box.
    for rel in (
        "scaffold/templates/claude-md/backend.md",
        "scaffold/templates/claude-md/app.md",
        "scaffold/templates/settings/backend.json",
        "scaffold/templates/settings/app.json",
        "scaffold/templates/hooks/post-compact-reanchor.sh",
        "agent-scripts/src/wake_rollover.py",
        "docs/slash-commands/backend",
        "docs/slash-commands/app-builder",
        "docs/playbooks/app-builder",
        "docs/design/ontology-support.md",
    ):
        assert (REPO_ROOT / rel).exists(), rel
    for doc in APP_REFERENCE_DOCS:
        assert (REPO_ROOT / "docs" / doc).is_file(), doc


def test_backend_tier2_omits_wip_case_command(tmp_path):
    ctx = _ctx(tmp_path, tier3=False)
    run_surfaces(backend_surfaces(), ctx)
    cmds = {p.name for p in (tmp_path / ".claude/commands").glob("*.md")}
    assert "wip-case.md" not in cmds
    assert "wip-setup.md" in cmds


def test_backend_tier3_includes_wip_case_command(tmp_path):
    ctx = _ctx(tmp_path, tier3=True)
    run_surfaces(backend_surfaces(), ctx)
    cmds = {p.name for p in (tmp_path / ".claude/commands").glob("*.md")}
    assert "wip-case.md" in cmds


def test_app_playbooks_never_wipe(tmp_path):
    # A pre-existing docs/playbooks/case-workflow.md may be WIP-KB's
    # SERVED source — it must survive a refresh even if the gene pool no
    # longer ships a file of that name.
    (tmp_path / "docs/playbooks").mkdir(parents=True)
    sentinel = tmp_path / "docs/playbooks/case-workflow.md"
    sentinel.write_text("served source — do not delete")
    surfaces = [s for s in app_surfaces({"APP_NAME": "x", "APP_SLUG": "x", "DEV_NAMESPACE": "x", "PRESET": "standard"}) if s.name == "playbooks"]
    run_surfaces(surfaces, _ctx(tmp_path))
    assert sentinel.exists()


def test_session_role_preserved_without_prefix(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/.session-role").write_text("APP-OLD\n")
    surfaces = [s for s in app_surfaces({"APP_NAME": "x", "APP_SLUG": "x", "DEV_NAMESPACE": "x", "PRESET": "standard"}) if s.name == "session-role"]
    ctx = _ctx(tmp_path, role_prefix="")
    run_surfaces(surfaces, ctx)
    assert (tmp_path / ".claude/.session-role").read_text() == "APP-OLD\n"
    assert any("Kept" in n for n in ctx.notes)


def test_session_role_warns_when_absent_and_no_prefix(tmp_path):
    surfaces = [s for s in app_surfaces({"APP_NAME": "x", "APP_SLUG": "x", "DEV_NAMESPACE": "x", "PRESET": "standard"}) if s.name == "session-role"]
    ctx = _ctx(tmp_path, role_prefix="")
    run_surfaces(surfaces, ctx)
    assert not (tmp_path / ".claude/.session-role").exists()
    assert any("WARNING" in n for n in ctx.notes)


# --- mcp-json surface --------------------------------------------------------

def test_mcp_json_key_file_variant(tmp_path):
    from wip_scaffold.surfaces import mcp_json_surface

    s = mcp_json_surface("/venv/bin/python", "https://localhost:8443",
                         key_file="/secrets/api-key")
    run_surfaces([s], _ctx(tmp_path))
    text = (tmp_path / ".mcp.json").read_text()
    assert '"WIP_API_KEY_FILE": "/secrets/api-key",' in text
    assert '"WIP_API_KEY"' not in text.replace("WIP_API_KEY_FILE", "")
    assert text.count('"https://localhost:8443"') == 5
    assert text.endswith("}\n")
    import json as j
    parsed = j.loads(text)
    assert parsed["mcpServers"]["wip"]["command"] == "/venv/bin/python"


def test_mcp_json_literal_key_variant(tmp_path):
    from wip_scaffold.surfaces import mcp_json_surface

    s = mcp_json_surface("/venv/bin/python", "https://localhost:8443",
                         key_literal="dev_master_key_for_testing")
    run_surfaces([s], _ctx(tmp_path))
    text = (tmp_path / ".mcp.json").read_text()
    assert '"WIP_API_KEY": "dev_master_key_for_testing",' in text
    assert "WIP_API_KEY_FILE" not in text
