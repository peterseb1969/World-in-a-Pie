"""The surface matrix — content-family surfaces for both roles.

Step-3 phase 1 ports the CONTENT-PROPAGATION family: CLAUDE.md, settings,
commands, playbooks, reference docs, hook, wake-rollover, session-role,
app-meta. Environment surfaces (.mcp.json, venv), distribution surfaces
(tarballs, wheel, bootstrap templates, query preset), and network actions
(enable_kb, namespace) remain in the bash wrappers for now — they migrate
in later phases, one gated commit at a time.

Every entry carries a substantive rationale — the incident lesson written
out, not a ticket number — and the unit tests pin each policy's behavior
so "why is this here" survives refactors.
"""

from __future__ import annotations

from pathlib import Path

from .engine import Context, Policy, Surface
from .render import render, tier_filter


# --- shared producers --------------------------------------------------------

def _template(ctx: Context, rel: str) -> str:
    return (ctx.wip_root / "scaffold/templates" / rel).read_text()


def _commands(ctx: Context, source_subdir: str) -> dict[str, bytes]:
    src = ctx.wip_root / "docs/slash-commands" / source_subdir
    out: dict[str, bytes] = {}
    for p in sorted(src.glob("*.md")):
        if p.name == "wip-case.md" and not ctx.tier3:
            continue  # tier 2 has no KB: shipping /wip-case would error on use
        out[f".claude/commands/{p.name}"] = p.read_bytes()
    return out


def _wake_rollover(ctx: Context) -> dict[str, bytes]:
    src = ctx.wip_root / "agent-scripts/src/wake_rollover.py"
    return {".claude/scripts/wake-rollover.py": src.read_bytes()}


def _session_role(ctx: Context) -> dict[str, bytes]:
    dest = ctx.target_root / ".claude/.session-role"
    if ctx.role_prefix:
        return {".claude/.session-role": (ctx.role_prefix + "\n").encode()}
    if dest.exists():
        ctx.notes.append(f"Kept: .claude/.session-role ({dest.read_text().strip()})")
        return {}
    ctx.notes.append(
        "WARNING: no --prefix given and no .claude/.session-role present. "
        "/wip-setup and /wip-wake need it to mint session IDs."
    )
    return {}


# --- backend matrix ----------------------------------------------------------

def backend_surfaces() -> list[Surface]:
    return [
        Surface(
            name="claude-md",
            policy=Policy.REGENERATE,
            rationale="tier filter drops KB-collaboration sections on tier-2 repos; __WIP_ROOT__ becomes the clone's absolute path",
            produce=lambda ctx: {
                "CLAUDE.md": render(
                    _template(ctx, "claude-md/backend.md"),
                    {"__WIP_ROOT__": str(ctx.wip_root)},
                    ctx.tier3,
                ).encode()
            },
        ),
        Surface(
            name="commands",
            policy=Policy.REGENERATE,
            rationale="wipe-then-copy so renamed/retired gene-pool commands never linger; tier 2 omits /wip-case",
            wipe_glob=".claude/commands/*.md",
            produce=lambda ctx: _commands(ctx, "backend"),
        ),
        Surface(
            name="wake-rollover",
            policy=Policy.REGENERATE,
            rationale="session rollover must be a vendored script, not prose an agent may skip",
            produce=_wake_rollover,
        ),
        Surface(
            name="settings",
            policy=Policy.REGENERATE,
            rationale="scaffold-owned, regenerated every run so allowlist improvements reach every clone; settings.local.json is operator-owned and never touched",
            produce=lambda ctx: {
                ".claude/settings.json": _template(ctx, "settings/backend.json").encode()
            },
        ),
        Surface(
            name="session-role",
            policy=Policy.REGENERATE,
            rationale="role prefix for session-ID minting; the backend role is fixed",
            produce=lambda ctx: {".claude/.session-role": b"BE-YAC\n"},
        ),
    ]


# --- app matrix --------------------------------------------------------------

APP_REFERENCE_DOCS = [
    "Vision.md",
    "AI-Assisted-Development.md",
    "WIP_PoNIFs.md",
    "WIP_DevGuardrails.md",
    "wip-guide.md",
    "technology-stack.md",
    "ui-guidance.md",
    "wip-deployable-app-contract.md",
]


def _app_docs(ctx: Context) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for name in APP_REFERENCE_DOCS:
        src = ctx.wip_root / "docs" / name
        if src.is_file():
            out[f"docs/{name}"] = src.read_bytes()
        else:
            ctx.notes.append(f"Warning: docs/{name} not found, skipping")
    onto = ctx.wip_root / "docs/design/ontology-support.md"
    if onto.is_file():
        out["docs/ontology-support.md"] = onto.read_bytes()
    else:
        ctx.notes.append("Warning: docs/design/ontology-support.md not found, skipping")
    return out


def _app_playbooks(ctx: Context) -> dict[str, bytes]:
    src = ctx.wip_root / "docs/playbooks/app-builder"
    if not src.is_dir():
        ctx.notes.append(f"Warning: {src} not found, skipping playbooks")
        return {}
    # Copy-only, no wipe: docs/playbooks/case-workflow.md must NEVER be
    # deleted — WIP-KB serves that path as a load-bearing source, and the
    # scaffold cannot tell a stale copy from a served one.
    return {f"docs/playbooks/{p.name}": p.read_bytes() for p in sorted(src.glob("*.md"))}


def app_surfaces(app_meta: dict[str, str]) -> list[Surface]:
    """app_meta: APP_NAME / APP_SLUG / DEV_NAMESPACE / PRESET — persisted so
    a set-up-in-place run regenerates CLAUDE.md from recorded metadata
    instead of deriving it from the directory name."""

    def _app_meta_file(ctx: Context) -> dict[str, bytes]:
        # The header below is EMITTED OUTPUT reproducing the legacy scripts
        # byte-for-byte (golden-pinned) — its citation predates the
        # no-citations rule; changing it is a content edit, not cleanup.
        body = (
            "# Generated by create-app-project.sh (CASE-418). Read on set-up-in-place to\n"
            "# regenerate CLAUDE.md. Edit deliberately if the app's metadata changes.\n"
            + "".join(f'{k}="{v}"\n' for k, v in app_meta.items())
        )
        return {".claude/.app-meta": body.encode()}

    def _claude_md(ctx: Context) -> dict[str, bytes]:
        return {
            "CLAUDE.md": render(_template(ctx, "claude-md/app.md"), ctx.tokens, ctx.tier3).encode()
        }

    return [
        Surface(
            name="commands",
            policy=Policy.REGENERATE,
            rationale="wipe-then-copy so renamed/retired gene-pool commands never linger; tier 2 omits /wip-case",
            wipe_glob=".claude/commands/*.md",
            produce=lambda ctx: _commands(ctx, "app-builder"),
        ),
        Surface(
            name="wake-rollover",
            policy=Policy.REGENERATE,
            rationale="session rollover must be a vendored script, not prose an agent may skip",
            produce=_wake_rollover,
        ),
        Surface(
            name="session-role",
            policy=Policy.PRESERVE_OR_SET,
            rationale="--prefix sets; an existing role file is operator state and preserved; neither → warn loudly",
            produce=_session_role,
        ),
        Surface(
            name="app-meta",
            policy=Policy.REGENERATE,
            rationale="rewritten every run from the resolved values, never from the directory name",
            produce=_app_meta_file,
        ),
        Surface(
            name="settings",
            policy=Policy.REGENERATE,
            rationale="scaffold-owned, regenerated every run; settings.local.json never touched",
            produce=lambda ctx: {
                ".claude/settings.json": _template(ctx, "settings/app.json").encode()
            },
        ),
        Surface(
            name="hook",
            policy=Policy.REGENERATE,
            rationale="compaction evicts the baseline reading exactly when drift starts; the hook re-anchors",
            executable=True,
            produce=lambda ctx: {
                ".claude/hooks/post-compact-reanchor.sh": _template(
                    ctx, "hooks/post-compact-reanchor.sh"
                ).encode()
            },
        ),
        Surface(
            name="playbooks",
            policy=Policy.REGENERATE,
            rationale="copy-only, never wipes: a pre-existing case-workflow.md may be WIP-KB's served source",
            produce=_app_playbooks,
        ),
        Surface(
            name="reference-docs",
            policy=Policy.REGENERATE,
            rationale="the doc list is data here — adding a canonical doc is a list edit, not a script edit",
            produce=_app_docs,
        ),
        Surface(
            name="claude-md",
            policy=Policy.RENDER_REFRESH,
            rationale="app CLAUDE.md is generated-then-customised; refresh renders a sidecar so app-authored content survives",
            produce=_claude_md,
        ),
    ]
