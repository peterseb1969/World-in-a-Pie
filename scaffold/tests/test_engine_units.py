"""Unit tests for the surface engine.

Pure — no podman, no network, no scratch clones; these run in CI
unconditionally (unlike the WIP_GOLDEN-gated snapshot tests). Each policy
test pins the incident behavior that motivated it, written out in the
test itself, so the *why* survives refactors.
"""

from __future__ import annotations

import json as _json
import os
import shutil
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


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_refused_run_leaves_tree_unchanged(tmp_path):
    # A refresh once died on its wheel surface after the library surfaces had
    # already swapped the tarballs and wiped the old ones: the app's
    # package.json named files that were gone. A LATER surface that raises
    # must leave every earlier surface's writes AND wipes undone — nothing is
    # touched until every surface has planned — and no temp litter remains.
    (tmp_path / "libs").mkdir()
    (tmp_path / "libs/old-1.0.tgz").write_bytes(b"old")
    (tmp_path / "a.txt").write_bytes(b"before")
    before = _tree(tmp_path)

    def boom(ctx):
        raise RuntimeError("producer failed")

    ok = _surface(files={"a.txt": b"A", "libs/old-2.0.tgz": b"new"}, wipe_glob="libs/old-*.tgz")
    bad = Surface(name="bad", policy=Policy.REGENERATE, produce=boom)
    with pytest.raises(RuntimeError, match="producer failed"):
        run_surfaces([ok, bad], _ctx(tmp_path))
    assert _tree(tmp_path) == before
    litter = [p for p in tmp_path.rglob(".*") if p.is_file() and p.name.startswith(".")]
    assert litter == [], f"temp litter: {litter}"


def test_dry_run_touches_nothing(tmp_path):
    (tmp_path / "old.md").write_text("keep")
    s = _surface(files={"new.txt": b"x"}, wipe_glob="*.md")
    log = run_surfaces([s], _ctx(tmp_path, dry_run=True))
    assert (tmp_path / "old.md").exists(), "dry-run removed a file"
    assert not (tmp_path / "new.txt").exists(), "dry-run wrote a file"
    assert any("would remove" in line for line in log)
    assert any("would write" in line for line in log)


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


def test_wipe_with_nothing_to_write_refuses_and_keeps_installed_files(tmp_path):
    # A missing or emptied source dir produced {} and the wipe still ran:
    # every installed command was deleted and nothing replaced it.
    (tmp_path / ".claude/commands").mkdir(parents=True)
    (tmp_path / ".claude/commands/installed.md").write_text("keep")
    s = _surface(name="commands", files={}, wipe_glob=".claude/commands/*.md")
    with pytest.raises(RuntimeError, match="nothing to write"):
        run_surfaces([s], _ctx(tmp_path))
    assert (tmp_path / ".claude/commands/installed.md").read_text() == "keep"


def test_commands_from_a_missing_source_dir_refuse(tmp_path):
    from wip_scaffold.surfaces import _commands
    with pytest.raises(FileNotFoundError, match="slash-command source directory"):
        _commands(_ctx(tmp_path), "no-such-profile")


# --- CREATE_ONLY ---------------------------------------------------------------
# Starting points the app then owns (bootstrap templates, the query preset,
# .env). Only the wrappers kept a refresh from overwriting them; an engine run
# with --refresh and --query-scaffold would have replaced app code.

def test_create_only_on_refresh_keeps_existing_and_writes_absent(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/App.tsx").write_text("app code")
    s = _surface(name="query-scaffold", policy=Policy.CREATE_ONLY,
                 files={"src/App.tsx": b"starter", ".env": b"fresh"})
    ctx = _ctx(tmp_path, refresh=True)
    run_surfaces([s], ctx)
    assert (tmp_path / "src/App.tsx").read_text() == "app code"
    assert (tmp_path / ".env").read_bytes() == b"fresh"
    assert not (tmp_path / "src/App.tsx.refresh").exists()
    assert any("kept 1 existing file(s)" in n and "src/App.tsx" in n for n in ctx.notes)


def test_create_only_on_create_writes_everything(tmp_path):
    (tmp_path / ".gitignore").write_text("old")
    s = _surface(policy=Policy.CREATE_ONLY, files={".gitignore": b"old+new"})
    run_surfaces([s], _ctx(tmp_path, refresh=False))
    assert (tmp_path / ".gitignore").read_bytes() == b"old+new"


def test_create_only_surfaces_declare_the_policy():
    from wip_scaffold.surfaces import bootstrap_surface, env_surface, query_scaffold_surfaces
    surfaces = [bootstrap_surface(), env_surface("/k"), *query_scaffold_surfaces("A", "a", "a")]
    assert {s.name: s.policy for s in surfaces} == {
        "bootstrap-templates": Policy.CREATE_ONLY, "env": Policy.CREATE_ONLY,
        "query-scaffold": Policy.CREATE_ONLY, "query-entrypoint": Policy.CREATE_ONLY,
    }


def test_executable_bit(tmp_path):
    s = _surface(files={"hook.sh": b"#!/bin/sh\n"}, executable=True)
    run_surfaces([s], _ctx(tmp_path))
    mode = os.stat(tmp_path / "hook.sh").st_mode
    assert mode & stat.S_IXUSR


def _umask() -> int:
    umask = os.umask(0)
    os.umask(umask)
    return umask


def test_new_files_get_the_umask_mode_a_shell_write_would(tmp_path):
    # mkstemp creates 0600 whatever the umask; the bash scripts the engine
    # replaced left 0644 files and 0755 hooks under the usual umask.
    s = _surface(files={"a.txt": b"a", "hook.sh": b"#!/bin/sh\n"})
    x = _surface(name="x", files={"run.sh": b"#!/bin/sh\n"}, executable=True)
    old = os.umask(0o022)
    try:
        run_surfaces([s, x], _ctx(tmp_path))
    finally:
        os.umask(old)
    assert stat.S_IMODE(os.stat(tmp_path / "a.txt").st_mode) == 0o644
    assert stat.S_IMODE(os.stat(tmp_path / "hook.sh").st_mode) == 0o644
    assert stat.S_IMODE(os.stat(tmp_path / "run.sh").st_mode) == 0o755


def test_an_overwritten_file_keeps_its_mode(tmp_path):
    (tmp_path / "out.txt").write_bytes(b"old")
    os.chmod(tmp_path / "out.txt", 0o640)
    run_surfaces([_surface()], _ctx(tmp_path))
    assert (tmp_path / "out.txt").read_bytes() == b"data"
    assert stat.S_IMODE(os.stat(tmp_path / "out.txt").st_mode) == 0o640


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


def test_render_refresh_note_in_a_dry_run_says_would(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("app-authored")
    s = _surface(policy=Policy.RENDER_REFRESH, files={"CLAUDE.md": b"gen"})
    ctx = _ctx(tmp_path, refresh=True, dry_run=True)
    run_surfaces([s], ctx)
    assert not (tmp_path / "CLAUDE.md.refresh").exists()
    assert any("would be written to CLAUDE.md.refresh" in n for n in ctx.notes)


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
    for surfaces in (backend_surfaces(), app_surfaces({"APP_NAME": "x", "APP_SLUG": "x", "APP_NAMESPACE": "x", "PRESET": "standard"})):
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
        "scaffold/templates/settings/settings.json",
        "scaffold/templates/hooks/post-compact-reanchor.sh",
        "agent-scripts/src/wake_rollover.py",
        "docs/slash-commands/backend",
        "docs/slash-commands/app-builder",
        "docs/slash-commands/app-slim",
        "scaffold/templates/hooks/post-compact-reanchor-slim.sh",
        "docs/cards/core.md",
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


_APP_TOKENS = {"APP_NAME": "x", "APP_SLUG": "x", "APP_NAMESPACE": "x", "PRESET": "standard"}


def _claim_bearing_outputs(tmp_path: Path, *, tier3: bool) -> dict[str, str]:
    # The CLAUDE.md render and the slash commands of both roles, keyed by role.
    ctx = _ctx(tmp_path, tier3=tier3, tokens=dict(_APP_TOKENS))
    out: dict[str, str] = {}
    for role, surfaces in (
        ("backend", backend_surfaces()),
        ("app", app_surfaces(dict(_APP_TOKENS))),
        ("slim", app_surfaces(dict(_APP_TOKENS), profile="slim")),
    ):
        for s in surfaces:
            if s.name in ("claude-md", "commands"):
                for rel, data in s.produce(ctx).items():
                    out[f"{role}:{rel}"] = data.decode()
    return out


def test_case_claim_rule_reaches_only_kb_clones(tmp_path):
    # Claiming a case is a KB operation: a clone without a KB must never be
    # told to claim, while a KB clone gets the rule in CLAUDE.md and as a
    # step of the /wip-case command it runs to work a case.
    tier2 = _claim_bearing_outputs(tmp_path, tier3=False)
    assert not [k for k, v in tier2.items() if "CLAIM acquire" in v]
    tier3 = _claim_bearing_outputs(tmp_path, tier3=True)
    for role in ("backend", "app", "slim"):
        assert "CLAIM acquire" in tier3[f"{role}:CLAUDE.md"], role
        assert "CLAIM acquire" in tier3[f"{role}:.claude/commands/wip-case.md"], role
        # A held claim is not the only way an acquire fails: the step tells a
        # holder's refusal from a transport failure or a refused key, and the
        # CLAUDE.md rule says the same.
        command = tier3[f"{role}:.claude/commands/wip-case.md"]
        assert "exit 2 is a transport failure" in command, role
        # A transport failure says nothing about the claim: stop, then read
        # the case's claims before retrying.
        assert "report it verbatim and stop" in command, role
        assert "whether the claim was taken is unknown" in command, role
        assert "names a holder" in command, role
        assert "names a holder" in tier3[f"{role}:CLAUDE.md"], role
        # Whitespace is folded so the templates stay free to re-wrap at their
        # own width; the wording itself must match across profiles.
        claude_md = " ".join(tier3[f"{role}:CLAUDE.md"].split())
        # The rule is three parts in every profile, and the abbreviated profile
        # must not be where they quietly go missing: checking the claims before
        # picking a case, and naming what a non-holder failure looks like.
        assert "check them before you pick a case" in claude_md, role
        assert "any other failure (a transport error, a key the KB refused)" in claude_md, role
        # The stub header names every playbook rule it reproduces, so someone
        # fixing one copy knows the others carry it too.
        folded_command = " ".join(command.split())
        assert ("the claim step (write verbs, step 0) and the attribution line (step 1) "
                "both apply at the moment of acting") in folded_command, role
        assert "Change either one in every copy" in folded_command, role


def test_app_playbooks_never_wipe(tmp_path):
    # A pre-existing docs/playbooks/case-workflow.md may be WIP-KB's
    # SERVED source — it must survive a refresh even if the gene pool no
    # longer ships a file of that name.
    (tmp_path / "docs/playbooks").mkdir(parents=True)
    sentinel = tmp_path / "docs/playbooks/case-workflow.md"
    sentinel.write_text("served source — do not delete")
    surfaces = [s for s in app_surfaces({"APP_NAME": "x", "APP_SLUG": "x", "APP_NAMESPACE": "x", "PRESET": "standard"}) if s.name == "playbooks"]
    run_surfaces(surfaces, _ctx(tmp_path))
    assert sentinel.exists()


def test_session_role_preserved_without_prefix(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/.session-role").write_text("APP-OLD\n")
    surfaces = [s for s in app_surfaces({"APP_NAME": "x", "APP_SLUG": "x", "APP_NAMESPACE": "x", "PRESET": "standard"}) if s.name == "session-role"]
    ctx = _ctx(tmp_path, role_prefix="")
    run_surfaces(surfaces, ctx)
    assert (tmp_path / ".claude/.session-role").read_text() == "APP-OLD\n"
    assert any("Kept" in n for n in ctx.notes)


_APP_META = {"APP_NAME": "x", "APP_SLUG": "x", "APP_NAMESPACE": "x", "PRESET": "standard"}


def _app_surface(name):
    return [s for s in app_surfaces(_APP_META) if s.name == name]


def test_pre_commit_gate_written_executable_when_absent(tmp_path):
    run_surfaces(_app_surface("pre-commit-gate"), _ctx(tmp_path))
    hook = tmp_path / ".githooks/pre-commit"
    assert hook.read_text().startswith("#!/usr/bin/env bash")
    assert os.stat(hook).st_mode & stat.S_IXUSR


def test_pre_commit_gate_keeps_an_existing_hook(tmp_path):
    # Once present the hook is the app's to extend — WIP-KB carries its own.
    (tmp_path / ".githooks").mkdir()
    (tmp_path / ".githooks/pre-commit").write_text("#!/bin/sh\n# app's own\n")
    ctx = _ctx(tmp_path)
    run_surfaces(_app_surface("pre-commit-gate"), ctx)
    assert (tmp_path / ".githooks/pre-commit").read_text() == "#!/bin/sh\n# app's own\n"
    # An edited hook is kept, with the warning that the gate it may derive
    # from let quoted paths through.
    assert any("WARNING: .githooks/pre-commit differs" in n and "-z" in n for n in ctx.notes)


def test_pre_commit_gate_keeps_the_current_gate_quietly(tmp_path):
    run_surfaces(_app_surface("pre-commit-gate"), _ctx(tmp_path))
    ctx = _ctx(tmp_path)
    run_surfaces(_app_surface("pre-commit-gate"), ctx)
    assert any("Kept: .githooks/pre-commit" in n for n in ctx.notes)
    assert not any("WARNING" in n for n in ctx.notes)


# Every gate the scaffold shipped before the current one, by the commit that
# last wrote it: 969e63e2 read staged paths C-quoted, b3a5b907 read each
# secret file in its own subshell. A clone that never edited one gets the
# current gate on refresh.
SHIPPED_GATE_FIXTURES = ["pre-commit-gate-969e63e2.sh", "pre-commit-gate-b3a5b907.sh"]


def test_every_superseded_gate_has_its_shipped_bytes_as_a_fixture():
    import hashlib

    from wip_scaffold.surfaces import SUPERSEDED_PRE_COMMIT_GATES

    fixtures = {
        hashlib.sha256((REPO_ROOT / "scaffold/tests/fixtures" / name).read_bytes()).hexdigest()
        for name in SHIPPED_GATE_FIXTURES
    }
    assert fixtures == set(SUPERSEDED_PRE_COMMIT_GATES)


@pytest.mark.parametrize("fixture", SHIPPED_GATE_FIXTURES)
def test_pre_commit_gate_replaces_an_unmodified_earlier_shipped_gate(tmp_path, fixture):
    import hashlib

    from wip_scaffold.surfaces import SUPERSEDED_PRE_COMMIT_GATES

    # The shipped bytes, kept as a fixture: CI checks out too shallow to read
    # the old commit.
    old = (REPO_ROOT / "scaffold/tests/fixtures" / fixture).read_bytes()
    reason = SUPERSEDED_PRE_COMMIT_GATES[hashlib.sha256(old).hexdigest()]
    (tmp_path / ".githooks").mkdir()
    (tmp_path / ".githooks/pre-commit").write_bytes(old)
    ctx = _ctx(tmp_path)
    run_surfaces(_app_surface("pre-commit-gate"), ctx)
    current = (REPO_ROOT / "scaffold/templates/hooks/pre-commit-local-config.sh").read_bytes()
    assert (tmp_path / ".githooks/pre-commit").read_bytes() == current
    assert os.stat(tmp_path / ".githooks/pre-commit").st_mode & stat.S_IXUSR
    assert any("Replaced: .githooks/pre-commit" in n and reason in n for n in ctx.notes)


def test_the_current_gate_is_not_among_the_superseded():
    import hashlib

    from wip_scaffold.surfaces import SUPERSEDED_PRE_COMMIT_GATES

    current = (REPO_ROOT / "scaffold/templates/hooks/pre-commit-local-config.sh").read_bytes()
    assert hashlib.sha256(current).hexdigest() not in SUPERSEDED_PRE_COMMIT_GATES


def test_kb_json_example_tier3_only_and_placeholder(tmp_path):
    run_surfaces(_app_surface("kb-json-example"), _ctx(tmp_path, tier3=False))
    assert not (tmp_path / ".claude/kb.json.example").exists()
    run_surfaces(_app_surface("kb-json-example"), _ctx(tmp_path, tier3=True))
    example = _json.loads((tmp_path / ".claude/kb.json.example").read_text())
    assert set(example) == {"kb_app_url", "kb_api_key_file"}
    # A placeholder, never a guessed default path into some install's secrets.
    assert "wip-deploy" not in example["kb_api_key_file"]


def test_session_role_warns_when_absent_and_no_prefix(tmp_path):
    surfaces = [s for s in app_surfaces({"APP_NAME": "x", "APP_SLUG": "x", "APP_NAMESPACE": "x", "PRESET": "standard"}) if s.name == "session-role"]
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


def test_mcp_json_without_a_key_file_is_refused(tmp_path):
    # Written with no key, the MCP config would fail every call; the layer
    # that writes the file refuses it, not only the wrappers.
    from wip_scaffold.surfaces import mcp_json_surface

    with pytest.raises(ValueError, match="key file"):
        mcp_json_surface("/venv/bin/python", "https://localhost:8443", "")
    assert not (tmp_path / ".mcp.json").exists()


# --- bootstrap + env surfaces ------------------------------------------------

def test_bootstrap_surface_stamps_banner(tmp_path):
    from wip_scaffold.surfaces import BOOTSTRAP_TEMPLATES, bootstrap_surface

    run_surfaces([bootstrap_surface()], _ctx(tmp_path))
    for name in BOOTSTRAP_TEMPLATES:
        dest = tmp_path / "templates/bootstrap" / name
        assert dest.exists(), name
        text = dest.read_text()
        assert text.startswith("// ====="), "banner must lead the file"
        assert "GENESIS COPY" in text
        assert "DELETE templates/bootstrap/" in text
        # Stamp present: source commit + spawn date on the banner line.
        import re
        assert re.search(r"World-in-a-Pie@[0-9a-f]+, spawned \d{4}-\d{2}-\d{2}", text)
        # Original template content follows the banner.
        src = (REPO_ROOT / "apps/templates/bootstrap" / name).read_text()
        assert text.endswith(src)


def test_env_surface_points_at_key_file(tmp_path):
    from wip_scaffold.surfaces import env_surface

    run_surfaces([env_surface("/secrets/api-key")], _ctx(tmp_path))
    text = (tmp_path / ".env").read_text()
    assert text.rstrip().endswith("WIP_API_KEY_FILE=/secrets/api-key")
    assert "WIP_API_KEY=" not in text.replace("WIP_API_KEY_FILE=", "")


# --- query scaffold surfaces -------------------------------------------------

def test_query_scaffold_substitutions_and_curation(tmp_path):
    from wip_scaffold.surfaces import query_scaffold_surfaces

    ctx = _ctx(tmp_path)
    run_surfaces(query_scaffold_surfaces("My App", "my-app", "dev-my-app"), ctx)

    assert "my-app" in (tmp_path / "package.json").read_text()
    assert "SCAFFOLD_APP_SLUG" not in (tmp_path / "package.json").read_text()
    assert "My App" in (tmp_path / "index.html").read_text()
    assert "my-app" in (tmp_path / ".github/workflows/build.yaml").read_text()
    env_ex = (tmp_path / ".env.example").read_text()
    assert "WIP_NAMESPACE=dev-my-app" in env_ex
    assert "/path/to/WorldInPie" not in env_ex
    assert str(REPO_ROOT) in env_ex
    # Curation: the scaffold dir's package-lock.json is deliberately NOT shipped.
    assert not (tmp_path / "package-lock.json").exists()
    # Structure landed.
    assert (tmp_path / "server").is_dir() and (tmp_path / "src").is_dir()
    assert (tmp_path / ".gitignore").is_file()
    # Entrypoint executable.
    mode = os.stat(tmp_path / "docker-entrypoint-dev.sh").st_mode
    assert mode & stat.S_IXUSR


@pytest.mark.parametrize("missing", ["server", "src"])
def test_query_scaffold_without_a_code_dir_is_refused(tmp_path, missing):
    # rglob over a missing directory yields nothing: the app would have
    # shipped without its server or client code, exit 0.
    from wip_scaffold.surfaces import query_scaffold_surfaces

    wip = tmp_path / "wip"
    shutil.copytree(REPO_ROOT / "scripts/scaffold-query", wip / "scripts/scaffold-query")
    shutil.rmtree(wip / "scripts/scaffold-query" / missing)
    target = tmp_path / "app"
    target.mkdir()
    ctx = Context(wip_root=wip, target_root=target)
    with pytest.raises(FileNotFoundError, match=missing):
        run_surfaces(query_scaffold_surfaces("A", "a", "a"), ctx)
    assert list(target.iterdir()) == []


def test_query_scaffold_images_share_one_node_major():
    """Dev and production images run one Node major, so an app is not
    developed on one runtime and shipped on another; the dev entrypoint
    re-installs node_modules when that runtime changes."""
    import re

    src = REPO_ROOT / "scripts/scaffold-query"
    majors = {
        name: re.findall(r"^FROM node:(\d+)-", (src / name).read_text(), re.MULTILINE)
        for name in ("Dockerfile", "Dockerfile.dev")
    }
    assert all(majors.values()), majors
    assert len({m for found in majors.values() for m in found}) == 1, majors
    entrypoint = (src / "docker-entrypoint-dev.sh").read_text()
    assert "node -v" in entrypoint and "alpine-release" in entrypoint


def test_query_scaffold_gitignore_merges_existing(tmp_path):
    from wip_scaffold.surfaces import query_scaffold_surfaces

    (tmp_path / ".gitignore").write_text("existing-line\n")
    run_surfaces(query_scaffold_surfaces("X", "x", "dev-x"), _ctx(tmp_path))
    text = (tmp_path / ".gitignore").read_text()
    assert text.startswith("existing-line\n")
    assert len(text) > len("existing-line\n"), "scaffold additions must be appended"


# --- client-lib + toolkit surfaces -------------------------------------------

def _make_tgz(path: Path, with_dist: bool, readme: bytes | None = b"# README"):
    import io
    import tarfile

    def add(tf, name, data):
        info = tarfile.TarInfo(name)
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))

    with tarfile.open(path, "w:gz") as tf:
        add(tf, "package/package.json", b"{}")
        if with_dist:
            add(tf, "package/dist/index.js", b"module.exports={}")
        if readme is not None:
            add(tf, "package/README.md", readme)


def test_client_lib_copies_and_extracts_readme(tmp_path):
    from wip_scaffold.surfaces import client_lib_surface

    tgz = tmp_path / "src" / "wip-client-1.2.3.tgz"
    tgz.parent.mkdir()
    _make_tgz(tgz, with_dist=True)
    app = tmp_path / "app"
    run_surfaces([client_lib_surface("client", str(tgz))],
                 Context(wip_root=REPO_ROOT, target_root=app))
    assert (app / "libs/wip-client-1.2.3.tgz").read_bytes() == tgz.read_bytes()
    assert (app / "libs/wip-client-README.md").read_bytes() == b"# README"


def test_client_lib_wipes_stale_versions(tmp_path):
    from wip_scaffold.surfaces import client_lib_surface

    tgz = tmp_path / "wip-client-2.0.0.tgz"
    _make_tgz(tgz, with_dist=True)
    app = tmp_path / "app"
    (app / "libs").mkdir(parents=True)
    stale = app / "libs/wip-client-1.9.0.tgz"
    _make_tgz(stale, with_dist=True)
    other = app / "libs/wip-react-0.1.0.tgz"
    _make_tgz(other, with_dist=True)
    run_surfaces([client_lib_surface("client", str(tgz))],
                 Context(wip_root=REPO_ROOT, target_root=app))
    assert not stale.exists(), "stale same-lib version must be wiped (install glob ambiguity)"
    assert other.exists(), "other libs' tarballs are not this surface's business"
    assert (app / "libs/wip-client-2.0.0.tgz").exists()


def test_client_lib_immutability_violation_is_fatal(tmp_path):
    from wip_scaffold.surfaces import client_lib_surface

    tgz = tmp_path / "wip-client-1.0.0.tgz"
    _make_tgz(tgz, with_dist=True)
    app = tmp_path / "app"
    (app / "libs").mkdir(parents=True)
    conflicting = app / "libs/wip-client-1.0.0.tgz"
    _make_tgz(conflicting, with_dist=True, readme=b"different bytes inside")
    with pytest.raises(RuntimeError, match="different content"):
        run_surfaces([client_lib_surface("client", str(tgz))],
                     Context(wip_root=REPO_ROOT, target_root=app))
    # Fatal BEFORE any mutation: the conflicting file must be untouched.
    assert conflicting.exists()


def test_client_lib_invalid_dist_refuses_whole_lib_set(tmp_path):
    # Skipping one library while the others are swapped in leaves the app with
    # a half-updated library set and no refusal to say so. A tarball with no
    # compiled dist/ is fatal, and the libraries planned before it stay unswapped.
    from wip_scaffold.surfaces import client_lib_surface

    good = tmp_path / "wip-client-2.0.0.tgz"
    _make_tgz(good, with_dist=True)
    broken = tmp_path / "wip-react-1.0.0.tgz"
    _make_tgz(broken, with_dist=False)
    app = tmp_path / "app"
    (app / "libs").mkdir(parents=True)
    old = app / "libs/wip-client-1.0.0.tgz"
    _make_tgz(old, with_dist=True)
    before = _tree(app)
    with pytest.raises(RuntimeError, match="no compiled JS"):
        run_surfaces([client_lib_surface("client", str(good)),
                      client_lib_surface("react", str(broken))],
                     Context(wip_root=REPO_ROOT, target_root=app))
    assert _tree(app) == before


def _sri(data: bytes) -> str:
    import base64
    import hashlib

    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()


def _stage_lockfile(stage: Path, tgz: Path, integrity: str) -> None:
    # What `npm install --package-lock-only ./libs/<tgz>` leaves in the stage.
    stage.mkdir(parents=True, exist_ok=True)
    spec = f"file:libs/{tgz.name}"
    (stage / "package.json").write_text(_json.dumps({"dependencies": {"@wip/client": spec}}))
    (stage / "package-lock.json").write_text(_json.dumps({"packages": {
        "": {"dependencies": {"@wip/client": spec}},
        "node_modules/@wip/client": {"resolved": spec, "integrity": integrity},
    }}))


def test_lockfile_written_with_the_tarballs(tmp_path):
    from wip_scaffold.surfaces import client_lib_surface, lockfile_surface

    tgz = tmp_path / "wip-client-2.0.0.tgz"
    _make_tgz(tgz, with_dist=True)
    stage = tmp_path / "stage"
    _stage_lockfile(stage, tgz, _sri(tgz.read_bytes()))
    app = tmp_path / "app"
    (app / "libs").mkdir(parents=True)
    (app / "package.json").write_text('{"dependencies": {"@wip/client": "file:libs/wip-client-1.0.0.tgz"}}')
    run_surfaces([client_lib_surface("client", str(tgz)), lockfile_surface(str(stage), [str(tgz)])],
                 Context(wip_root=REPO_ROOT, target_root=app))
    assert (app / "package.json").read_bytes() == (stage / "package.json").read_bytes()
    assert (app / "package-lock.json").read_bytes() == (stage / "package-lock.json").read_bytes()
    assert (app / "libs/wip-client-2.0.0.tgz").exists()


def test_lockfile_integrity_mismatch_refuses_whole_refresh(tmp_path):
    # npm can satisfy a file: spec from a stale cache entry when a library's
    # content changed without a version bump; the lockfile then records the
    # wrong hash and `npm ci` fails with EINTEGRITY. The sync used to detect
    # this only after the tarballs were swapped and package.json rewritten.
    from wip_scaffold.surfaces import client_lib_surface, lockfile_surface

    tgz = tmp_path / "wip-client-2.0.0.tgz"
    _make_tgz(tgz, with_dist=True)
    stage = tmp_path / "stage"
    _stage_lockfile(stage, tgz, _sri(b"stale cached bytes"))
    app = tmp_path / "app"
    (app / "libs").mkdir(parents=True)
    _make_tgz(app / "libs/wip-client-1.0.0.tgz", with_dist=True)
    (app / "package.json").write_text('{"dependencies": {"@wip/client": "file:libs/wip-client-1.0.0.tgz"}}')
    before = _tree(app)
    with pytest.raises(RuntimeError, match="integrity does not match"):
        run_surfaces([client_lib_surface("client", str(tgz)), lockfile_surface(str(stage), [str(tgz)])],
                     Context(wip_root=REPO_ROOT, target_root=app))
    assert _tree(app) == before


def test_lockfile_unchanged_files_are_not_rewritten(tmp_path):
    from wip_scaffold.surfaces import lockfile_surface

    tgz = tmp_path / "wip-client-2.0.0.tgz"
    _make_tgz(tgz, with_dist=True)
    stage = tmp_path / "stage"
    _stage_lockfile(stage, tgz, _sri(tgz.read_bytes()))
    app = tmp_path / "app"
    app.mkdir()
    for f in ("package.json", "package-lock.json"):
        (app / f).write_bytes((stage / f).read_bytes())
    log = run_surfaces([lockfile_surface(str(stage), [str(tgz)])],
                       Context(wip_root=REPO_ROOT, target_root=app))
    assert log == []


@pytest.mark.skipif(__import__("shutil").which("npm") is None, reason="npm not installed")
def test_lockfile_surface_accepts_real_npm_output(tmp_path):
    # Pins the shape the surface reads (packages[...].resolved == "file:libs/<tgz>",
    # integrity = sha512 SRI of the file) against what npm actually writes for
    # the wrapper's `npm install --package-lock-only ./libs/<tgz>`.
    import subprocess

    from wip_scaffold.surfaces import lockfile_surface

    tgz = tmp_path / "src" / "wip-client-2.0.0.tgz"
    tgz.parent.mkdir()
    import io
    import tarfile
    with tarfile.open(tgz, "w:gz") as tf:
        for name, data in (("package/package.json", b'{"name": "@wip/client", "version": "2.0.0"}'),
                           ("package/dist/index.js", b"module.exports={}")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    stage = tmp_path / "stage"
    (stage / "libs").mkdir(parents=True)
    (stage / "package.json").write_text('{"name": "probe", "version": "1.0.0", "private": true}')
    (stage / "libs" / tgz.name).write_bytes(tgz.read_bytes())
    subprocess.run(["npm", "install", "--package-lock-only", "--offline", f"./libs/{tgz.name}",
                    "--no-audit", "--no-fund", "--loglevel=error"],
                   cwd=stage, check=True, capture_output=True, timeout=120)
    app = tmp_path / "app"
    app.mkdir()
    (app / "package.json").write_text('{"name": "probe", "version": "1.0.0", "private": true}')
    run_surfaces([lockfile_surface(str(stage), [str(tgz)])],
                 Context(wip_root=REPO_ROOT, target_root=app))
    lock = _json.loads((app / "package-lock.json").read_text())
    assert lock["packages"]["node_modules/@wip/client"]["integrity"] == _sri(tgz.read_bytes())


def test_toolkit_wheel_copies(tmp_path):
    from wip_scaffold.surfaces import toolkit_surface

    whl = tmp_path / "wip_toolkit-0.5.0-py3-none-any.whl"
    whl.write_bytes(b"PK\x03\x04fakewheel")
    app = tmp_path / "app"
    run_surfaces([toolkit_surface(str(whl))],
                 Context(wip_root=REPO_ROOT, target_root=app))
    assert (app / "libs/wip_toolkit-0.5.0-py3-none-any.whl").read_bytes() == whl.read_bytes()


# --- settings + hook parity --------------------------------------------------

def test_settings_one_baseline_both_roles(tmp_path):
    b, a = tmp_path / "b", tmp_path / "a"
    run_surfaces(backend_surfaces(), _ctx(b, tier3=True))
    run_surfaces(
        [s for s in app_surfaces({"APP_NAME": "x", "APP_SLUG": "x", "APP_NAMESPACE": "x", "PRESET": "standard"}) if s.name == "settings"],
        Context(wip_root=REPO_ROOT, target_root=a),
    )
    assert (b / ".claude/settings.json").read_bytes() == (a / ".claude/settings.json").read_bytes(), (
        "the two roles must ship the SAME settings baseline — per-role copies drift"
    )
    import json as j
    parsed = j.loads((b / ".claude/settings.json").read_text())
    assert "hooks" in parsed, "the post-compact hook registration ships in the baseline"
    assert any("wip-kb" in x for x in parsed["permissions"]["allow"])


# --- settings: three-way merge of the baseline -------------------------------
# A refresh used to regenerate .claude/settings.json whole and silently drop
# every hook an app had added — in WIP-KB that was the action-triggered gate
# refusing writes to the canonical KB. The baseline is now merged: app entries
# survive, baseline changes still land, and every change is announced.

SETTINGS = ".claude/settings.json"
RECORD = ".claude/.settings-baseline.json"
APP_GATE = {
    "matcher": "Bash",
    "hooks": [{
        "type": "command",
        "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/block-canonical-writes.sh",
        "timeout": 10,
    }],
}


def _baseline(allow=("Bash(cat:*)",), hooks=None) -> bytes:
    doc = {"permissions": {"allow": list(allow)}, "hooks": hooks or {}}
    return (_json.dumps(doc, indent=2) + "\n").encode()


def _merge_surface(data: bytes) -> Surface:
    return _surface(name="settings", policy=Policy.MERGE_JSON_BASELINE, files={SETTINGS: data})


def _read(root: Path, rel: str = SETTINGS):
    return _json.loads((root / rel).read_text())


def test_settings_refresh_keeps_app_added_hook(tmp_path):
    """The filer's case, through the real app settings surface."""
    settings = [s for s in app_surfaces(
        {"APP_NAME": "x", "APP_SLUG": "x", "APP_NAMESPACE": "x", "PRESET": "standard"}
    ) if s.name == "settings"]
    run_surfaces(settings, _ctx(tmp_path))
    doc = _read(tmp_path)
    doc["hooks"]["PreToolUse"] = [APP_GATE]
    (tmp_path / SETTINGS).write_text(_json.dumps(doc, indent=2) + "\n")

    run_surfaces(settings, _ctx(tmp_path, refresh=True))
    assert _read(tmp_path)["hooks"]["PreToolUse"] == [APP_GATE]


def test_settings_first_create_writes_baseline_and_record(tmp_path):
    data = _baseline()
    ctx = _ctx(tmp_path)
    run_surfaces([_merge_surface(data)], ctx)
    assert (tmp_path / SETTINGS).read_bytes() == data
    assert (tmp_path / RECORD).read_bytes() == data
    assert ctx.notes == []


def test_settings_new_baseline_entry_added_and_noted(tmp_path):
    run_surfaces([_merge_surface(_baseline(["A"]))], _ctx(tmp_path))
    ctx = _ctx(tmp_path, refresh=True)
    run_surfaces([_merge_surface(_baseline(["A", "B"]))], ctx)
    assert _read(tmp_path)["permissions"]["allow"] == ["A", "B"]
    assert any("+ B" in n for n in ctx.notes), ctx.notes


def test_settings_retired_baseline_entry_removed(tmp_path):
    run_surfaces([_merge_surface(_baseline(["A", "OLD"]))], _ctx(tmp_path))
    ctx = _ctx(tmp_path, refresh=True)
    run_surfaces([_merge_surface(_baseline(["A"]))], ctx)
    assert _read(tmp_path)["permissions"]["allow"] == ["A"]
    assert any("- OLD" in n for n in ctx.notes), ctx.notes


def test_settings_app_removed_baseline_entry_stays_removed(tmp_path):
    run_surfaces([_merge_surface(_baseline(["A", "B"]))], _ctx(tmp_path))
    doc = _read(tmp_path)
    doc["permissions"]["allow"] = ["A"]  # the app chose to drop B
    (tmp_path / SETTINGS).write_text(_json.dumps(doc))
    run_surfaces([_merge_surface(_baseline(["A", "B"]))], _ctx(tmp_path, refresh=True))
    assert _read(tmp_path)["permissions"]["allow"] == ["A"]


def test_settings_app_edited_scaffold_hook_is_kept(tmp_path):
    scaffold_group = {"matcher": "compact", "hooks": [{"type": "command", "command": "s.sh"}]}
    run_surfaces([_merge_surface(_baseline(hooks={"SessionStart": [scaffold_group]}))], _ctx(tmp_path))
    edited = {"matcher": "compact", "hooks": [{"type": "command", "command": "s.sh", "timeout": 5}]}
    doc = _read(tmp_path)
    doc["hooks"]["SessionStart"] = [edited]
    (tmp_path / SETTINGS).write_text(_json.dumps(doc))
    changed = {"matcher": "compact", "hooks": [{"type": "command", "command": "s2.sh"}]}
    run_surfaces([_merge_surface(_baseline(hooks={"SessionStart": [changed]}))], _ctx(tmp_path, refresh=True))
    # The app's edit made the group its own: kept; the new baseline group lands beside it.
    assert _read(tmp_path)["hooks"]["SessionStart"] == [edited, changed]


def test_settings_no_record_only_adds(tmp_path):
    """First run after the merge policy landed: nothing to retire against."""
    (tmp_path / ".claude").mkdir()
    (tmp_path / SETTINGS).write_text(_json.dumps(
        {"permissions": {"allow": ["APP", "LEGACY"]}, "hooks": {"PreToolUse": [APP_GATE]}}
    ))
    run_surfaces([_merge_surface(_baseline(["A"]))], _ctx(tmp_path, refresh=True))
    doc = _read(tmp_path)
    assert doc["permissions"]["allow"] == ["APP", "LEGACY", "A"]
    assert doc["hooks"]["PreToolUse"] == [APP_GATE]
    assert (tmp_path / RECORD).exists()


def test_settings_unchanged_file_is_not_rewritten(tmp_path):
    # Keeps the app's own formatting when there is nothing to merge.
    run_surfaces([_merge_surface(_baseline(["A"]))], _ctx(tmp_path))
    (tmp_path / SETTINGS).write_text('{"permissions":{"allow":["A"]},"hooks":{}}')
    log = run_surfaces([_merge_surface(_baseline(["A"]))], _ctx(tmp_path, refresh=True))
    assert (tmp_path / SETTINGS).read_text() == '{"permissions":{"allow":["A"]},"hooks":{}}'
    assert not any(line.endswith(f"wrote {SETTINGS}") for line in log)


def test_settings_unparseable_file_gets_sidecar(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / SETTINGS).write_text("{ not json")
    ctx = _ctx(tmp_path, refresh=True)
    run_surfaces([_merge_surface(_baseline())], ctx)
    assert (tmp_path / SETTINGS).read_text() == "{ not json"
    assert (tmp_path / (SETTINGS + ".refresh")).read_bytes() == _baseline()
    assert not (tmp_path / RECORD).exists()
    assert any("not valid JSON" in n for n in ctx.notes)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
def test_settings_unreadable_file_gets_sidecar(tmp_path):
    (tmp_path / ".claude").mkdir()
    target = tmp_path / SETTINGS
    target.write_text('{"permissions":{"allow":["Mine"]}}')
    target.chmod(0)
    ctx = _ctx(tmp_path, refresh=True)
    try:
        run_surfaces([_merge_surface(_baseline()), _surface(files={"after.txt": b"x"})], ctx)
    finally:
        target.chmod(0o600)
    assert target.read_text() == '{"permissions":{"allow":["Mine"]}}'
    assert (tmp_path / (SETTINGS + ".refresh")).read_bytes() == _baseline()
    assert not (tmp_path / RECORD).exists()
    assert (tmp_path / "after.txt").exists(), "the surfaces after it were skipped"
    assert any("could not be read" in n for n in ctx.notes)


def test_settings_notes_in_a_dry_run_say_would(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / SETTINGS).write_text("{ not json")
    ctx = _ctx(tmp_path, refresh=True, dry_run=True)
    run_surfaces([_merge_surface(_baseline())], ctx)
    assert not (tmp_path / (SETTINGS + ".refresh")).exists()
    assert any("would be written" in n for n in ctx.notes)
    assert not any("was written" in n for n in ctx.notes)

    (tmp_path / SETTINGS).write_text('{"permissions":{"allow":["Mine"]},"hooks":{}}')
    ctx = _ctx(tmp_path, refresh=True, dry_run=True)
    run_surfaces([_merge_surface(_baseline(["A"]))], ctx)
    assert any("would merge the scaffold baseline" in n for n in ctx.notes)
    assert not any(": merged" in n for n in ctx.notes)


def test_settings_merge_dry_run_touches_nothing(tmp_path):
    run_surfaces([_merge_surface(_baseline(["A"]))], _ctx(tmp_path))
    before = (tmp_path / SETTINGS).read_bytes()
    log = run_surfaces([_merge_surface(_baseline(["A", "B"]))], _ctx(tmp_path, refresh=True, dry_run=True))
    assert (tmp_path / SETTINGS).read_bytes() == before
    assert any("would write" in line for line in log)


def test_backend_gets_post_compact_hook(tmp_path):
    run_surfaces(backend_surfaces(), _ctx(tmp_path))
    hook = tmp_path / ".claude/hooks/post-compact-reanchor.sh"
    assert hook.exists(), "backend hook gap was accidental — parity is deliberate"
    assert os.stat(hook).st_mode & stat.S_IXUSR


def test_toolkit_wheel_wipes_stale_versions(tmp_path):
    from wip_scaffold.surfaces import toolkit_surface

    whl = tmp_path / "wip_toolkit-0.6.0-py3-none-any.whl"
    whl.write_bytes(b"PK\x03\x04new")
    app = tmp_path / "app"
    (app / "libs").mkdir(parents=True)
    stale = app / "libs/wip_toolkit-0.2.3-py3-none-any.whl"
    stale.write_bytes(b"PK\x03\x04old")
    run_surfaces([toolkit_surface(str(whl))],
                 Context(wip_root=REPO_ROOT, target_root=app))
    assert not stale.exists(), "stale wheels accumulate otherwise (0.2.3 + 0.5.0 seen in the wild)"
    assert (app / "libs/wip_toolkit-0.6.0-py3-none-any.whl").exists()


def test_app_meta_records_role_prefix(tmp_path):
    # .session-role is gitignored; the committed .app-meta must carry the
    # prefix or fresh checkouts dead-end at the identity pre-flight.
    surfaces = [s for s in app_surfaces({"APP_NAME": "x", "APP_SLUG": "x", "APP_NAMESPACE": "x", "PRESET": "standard"}) if s.name == "app-meta"]
    ctx = _ctx(tmp_path, role_prefix="APP-XY")
    run_surfaces(surfaces, ctx)
    assert 'ROLE_PREFIX="APP-XY"' in (tmp_path / ".claude/.app-meta").read_text()


def test_app_meta_role_prefix_falls_back_to_local_file(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/.session-role").write_text("APP-LOCAL\n")
    surfaces = [s for s in app_surfaces({"APP_NAME": "x", "APP_SLUG": "x", "APP_NAMESPACE": "x", "PRESET": "standard"}) if s.name == "app-meta"]
    run_surfaces(surfaces, _ctx(tmp_path, role_prefix=""))
    assert 'ROLE_PREFIX="APP-LOCAL"' in (tmp_path / ".claude/.app-meta").read_text()


def _make_wheel(path, *, generator="hatchling 1.32.4", code=b"x = 1\n", requires=b"Requires-Dist: pydantic\n"):
    """A minimal real wheel: one module, METADATA, WHEEL (with the build
    backend's Generator stamp) and a RECORD that hashes them."""
    import base64
    import hashlib
    import zipfile

    info = "wip_archive-0.3.0.dist-info"
    members = {
        "wip_archive/__init__.py": code,
        f"{info}/METADATA": b"Metadata-Version: 2.4\nName: wip-archive\nVersion: 0.3.0\n" + requires,
        f"{info}/WHEEL": f"Wheel-Version: 1.0\nGenerator: {generator}\nRoot-Is-Purelib: true\nTag: py3-none-any\n".encode(),
    }
    record = []
    for name, body in members.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(body).digest()).rstrip(b"=").decode()
        record.append(f"{name},sha256={digest},{len(body)}")
    record.append(f"{info}/RECORD,,")
    with zipfile.ZipFile(path, "w") as zf:
        for name, body in members.items():
            zf.writestr(name, body)
        zf.writestr(f"{info}/RECORD", "\n".join(record) + "\n")


def _ship_over(tmp_path, existing_kwargs, new_kwargs):
    from wip_scaffold.surfaces import archive_wheel_surface

    app = tmp_path / "app"
    (app / "libs").mkdir(parents=True)
    name = "wip_archive-0.3.0-py3-none-any.whl"
    existing = app / "libs" / name
    _make_wheel(existing, **existing_kwargs)
    built = tmp_path / name
    _make_wheel(built, **new_kwargs)
    return existing, built, lambda: run_surfaces(
        [archive_wheel_surface(str(built))], Context(wip_root=REPO_ROOT, target_root=app)
    )


def test_wheel_differing_only_in_build_backend_stamp_is_kept(tmp_path):
    # An unchanged tree rebuilt by a newer hatchling differs only in WHEEL's
    # Generator line (and RECORD's hash of it): no version bump is owed, and
    # the app keeps the wheel it has.
    existing, built, ship = _ship_over(
        tmp_path, {"generator": "hatchling 1.32.4"}, {"generator": "hatchling 1.33.0"}
    )
    before = existing.read_bytes()
    assert built.read_bytes() != before

    ship()

    assert existing.read_bytes() == before


def test_wheel_with_changed_code_under_same_version_is_fatal(tmp_path):
    _, _, ship = _ship_over(tmp_path, {"code": b"x = 1\n"}, {"code": b"x = 2\n"})
    with pytest.raises(RuntimeError, match="built from the same source"):
        ship()


def test_wheel_with_changed_metadata_under_same_version_is_fatal(tmp_path):
    _, _, ship = _ship_over(
        tmp_path, {}, {"requires": b"Requires-Dist: pydantic\nRequires-Dist: httpx\n"}
    )
    with pytest.raises(RuntimeError, match="built from the same source"):
        ship()


# --- .mcp.json: only the wip server is the scaffold's ------------------------
# A refresh rewrote .mcp.json whole and silently dropped an MCP server the app
# had added itself (a browser driver for QA).

def _mcp(tmp_path, existing=None):
    from wip_scaffold.surfaces import mcp_json_surface
    if existing is not None:
        (tmp_path / ".mcp.json").write_text(existing)
    surface = mcp_json_surface("/venv/bin/python", "https://localhost:8443", key_file="/keys/api-key")
    ctx = _ctx(tmp_path)
    run_surfaces([surface], ctx)
    return (tmp_path / ".mcp.json").read_text(), ctx


def test_mcp_json_fresh_has_only_wip(tmp_path):
    text, _ = _mcp(tmp_path)
    assert list(_json.loads(text)["mcpServers"]) == ["wip"]


def test_mcp_json_keeps_servers_the_app_added(tmp_path):
    existing = {"mcpServers": {
        "playwright": {"command": "npx", "args": ["@playwright/mcp@0.0.83"]},
        "wip": {"type": "stdio", "command": "/old/python", "args": [], "env": {"WIP_API_KEY_FILE": "/old"}},
    }}
    text, ctx = _mcp(tmp_path, _json.dumps(existing))
    servers = _json.loads(text)["mcpServers"]
    assert servers["playwright"] == existing["mcpServers"]["playwright"]
    assert servers["wip"]["command"] == "/venv/bin/python"
    assert servers["wip"]["env"]["WIP_API_KEY_FILE"] == "/keys/api-key"
    assert any("Kept in .mcp.json: playwright" in n for n in ctx.notes)


def test_mcp_json_without_extras_is_byte_identical_to_a_fresh_write(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    fresh, _ = _mcp(tmp_path / "a")
    again, _ = _mcp(tmp_path / "b", fresh)
    assert again == fresh


# .mcp.json is gitignored: a rewrite of a file the engine could not read
# destroyed MCP servers the project had added, with no copy to restore.

@pytest.mark.parametrize("existing, problem", [
    ('{"mcpServers": {"mine": {"command": "x"}},}', "not valid JSON"),  # trailing comma
    ('["wip"]', "not a JSON object"),
    ('{"mcpServers": ["mine"]}', '"mcpServers" that is not an object'),
])
def test_mcp_json_it_cannot_merge_is_left_untouched_with_a_sidecar(tmp_path, existing, problem):
    text, ctx = _mcp(tmp_path, existing)
    assert text == existing
    sidecar = _json.loads((tmp_path / ".mcp.json.refresh").read_text())
    assert list(sidecar["mcpServers"]) == ["wip"]
    assert any(problem in n and ".mcp.json.refresh" in n for n in ctx.notes)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
def test_mcp_json_unreadable_is_left_untouched_with_a_sidecar(tmp_path):
    # The atomic replace needs the directory's permissions, not the file's,
    # so a rewrite would succeed over content nobody read. Keep the file,
    # put the scaffold's version beside it, and let the run go on.
    from wip_scaffold.surfaces import mcp_json_surface
    target = tmp_path / ".mcp.json"
    target.write_text('{"mcpServers": {"mine": {}}}')
    target.chmod(0)
    ctx = _ctx(tmp_path)
    try:
        run_surfaces([mcp_json_surface("/p", "https://h", key_file="/k"),
                      _surface(files={"after.txt": b"x"})], ctx)
    finally:
        target.chmod(0o600)
    assert target.read_text() == '{"mcpServers": {"mine": {}}}'
    sidecar = _json.loads((tmp_path / ".mcp.json.refresh").read_text())
    assert list(sidecar["mcpServers"]) == ["wip"]
    assert (tmp_path / "after.txt").exists(), "the surfaces after it were skipped"
    assert any("could not be read" in n and "Make .mcp.json readable" in n for n in ctx.notes)


def test_mcp_json_sidecar_note_in_a_dry_run_says_would(tmp_path):
    from wip_scaffold.surfaces import mcp_json_surface
    (tmp_path / ".mcp.json").write_text("{ not json")
    ctx = _ctx(tmp_path, dry_run=True)
    run_surfaces([mcp_json_surface("/p", "https://h", key_file="/k")], ctx)
    assert not (tmp_path / ".mcp.json.refresh").exists()
    note = next(n for n in ctx.notes if ".mcp.json.refresh" in n)
    assert "would be written" in note and "was written" not in note


# --- slim re-anchor hook ---------------------------------------------------------
# After a compaction the hook prints the core card without its card-sources
# header. Its sed range once ended only at a "-->" in column 1: a closer the
# drift test accepts anywhere on the line made it delete to the end of the
# card, and the slim agent got the banner and no rules, silently.

SLIM_HOOK = REPO_ROOT / "scaffold/templates/hooks/post-compact-reanchor-slim.sh"


def _run_slim_hook(project: Path) -> str:
    import subprocess
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project)}
    return subprocess.run(["bash", str(SLIM_HOOK)], env=env, check=True,
                          capture_output=True, text=True).stdout


def _card_body(card: str) -> str:
    """The card after its header, as test_card_drift.py's HEADER_RE reads it."""
    import re
    match = re.match(r"\A<!-- card-sources:.*?\n.*?-->[^\n]*\n", card, re.DOTALL)
    assert match, "card has no card-sources header"
    return card[match.end():]


def test_slim_hook_prints_the_shipped_card_without_its_header(tmp_path):
    card = (REPO_ROOT / "docs/cards/core.md").read_text()
    (tmp_path / "docs/cards").mkdir(parents=True)
    (tmp_path / "docs/cards/core.md").write_text(card)
    out = _run_slim_hook(tmp_path)
    assert "card-sources" not in out and "sha256:" not in out
    assert out.endswith(_card_body(card))


def test_slim_hook_strips_a_header_whose_closer_is_not_in_column_one(tmp_path):
    (tmp_path / "docs/cards").mkdir(parents=True)
    (tmp_path / "docs/cards/core.md").write_text(
        "<!-- card-sources: x\ndocs/Vision.md sha256:0 reviewed:0\n  -->\n# Card\nrule one\n")
    out = _run_slim_hook(tmp_path)
    assert out.endswith("# Card\nrule one\n") and "sha256:" not in out


@pytest.mark.parametrize("card", [None, "<!-- card-sources: x\nno closer at all\n"])
def test_slim_hook_falls_back_to_the_invariant_when_no_card_text_is_left(tmp_path, card):
    if card is not None:
        (tmp_path / "docs/cards").mkdir(parents=True)
        (tmp_path / "docs/cards/core.md").write_text(card)
    out = _run_slim_hook(tmp_path)
    assert "missing or empty" in out and "metadata.* is never identity" in out


@pytest.mark.parametrize("profile", ["full", "slim"])
def test_generated_hook_and_app_meta_carry_no_case_token(tmp_path, profile):
    # Generated surfaces are read where there may be no KB: a case number
    # there is a dead link. Provenance belongs in commit messages.
    import re

    meta = {"APP_NAME": "x", "APP_SLUG": "x", "APP_NAMESPACE": "x", "PRESET": "standard"}
    app = [s for s in app_surfaces(meta, profile=profile) if s.name in ("hook", "app-meta")]
    backend = [s for s in backend_surfaces() if s.name == "hook"]
    assert len(app) == 2 and len(backend) == 1
    for s in app + backend:
        for rel, data in s.produce(_ctx(tmp_path, role_prefix="APP-XY")).items():
            assert not re.search(rb"CASE-\d", data), f"{s.name}: {rel}"
