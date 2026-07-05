"""The surface matrix — content-family surfaces for both roles (CASE-612).

Step-3 phase 1 ports the CONTENT-PROPAGATION family: CLAUDE.md, settings,
commands, playbooks, reference docs, hook, wake-rollover, session-role,
app-meta. Environment surfaces (.mcp.json, venv), distribution surfaces
(tarballs, wheel, bootstrap templates, query preset), and network actions
(enable_kb, namespace) remain in the bash wrappers for now — they migrate
in later phases, one gated commit at a time.

Every entry carries its CASE provenance; the unit tests pin each policy's
incident behavior so "why is this here" survives refactors (design doc,
Problem #6).
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
            continue  # tier 2: /wip-case is a tier-3 artifact (CASE-463)
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
            case_refs="CASE-463 tier filter; __WIP_ROOT__ substitution",
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
            case_refs="CASE-463 tier gate; wipe stops retired commands lingering (CASE-390 era)",
            wipe_glob=".claude/commands/*.md",
            produce=lambda ctx: _commands(ctx, "backend"),
        ),
        Surface(
            name="wake-rollover",
            policy=Policy.REGENERATE,
            case_refs="CASE-604",
            produce=_wake_rollover,
        ),
        Surface(
            name="settings",
            policy=Policy.REGENERATE,
            case_refs="CASE-446 — scaffold-owned, regenerated every run; settings.local.json never touched",
            produce=lambda ctx: {
                ".claude/settings.json": _template(ctx, "settings/backend.json").encode()
            },
        ),
        Surface(
            name="session-role",
            policy=Policy.REGENERATE,
            case_refs="CASE-389 — backend role is fixed",
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
    # deleted — WIP-KB serves that path as a load-bearing source (CASE-522).
    return {f"docs/playbooks/{p.name}": p.read_bytes() for p in sorted(src.glob("*.md"))}


def app_surfaces(app_meta: dict[str, str]) -> list[Surface]:
    """app_meta: APP_NAME / APP_SLUG / DEV_NAMESPACE / PRESET (CASE-418)."""

    def _app_meta_file(ctx: Context) -> dict[str, bytes]:
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
            case_refs="CASE-463 tier gate; wipe stops retired commands lingering",
            wipe_glob=".claude/commands/*.md",
            produce=lambda ctx: _commands(ctx, "app-builder"),
        ),
        Surface(
            name="wake-rollover",
            policy=Policy.REGENERATE,
            case_refs="CASE-604",
            produce=_wake_rollover,
        ),
        Surface(
            name="session-role",
            policy=Policy.PRESERVE_OR_SET,
            case_refs="CASE-389 — --prefix sets; existing preserved; neither → warn",
            produce=_session_role,
        ),
        Surface(
            name="app-meta",
            policy=Policy.REGENERATE,
            case_refs="CASE-418 — rewritten every run from resolved values",
            produce=_app_meta_file,
        ),
        Surface(
            name="settings",
            policy=Policy.REGENERATE,
            case_refs="CASE-446",
            produce=lambda ctx: {
                ".claude/settings.json": _template(ctx, "settings/app.json").encode()
            },
        ),
        Surface(
            name="hook",
            policy=Policy.REGENERATE,
            case_refs="CASE-480 — post-compaction re-anchor",
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
            case_refs="CASE-522 — copy-only, never wipes (case-workflow.md is served by WIP-KB)",
            produce=_app_playbooks,
        ),
        Surface(
            name="reference-docs",
            policy=Policy.REGENERATE,
            case_refs="doc list is data here, not script edits",
            produce=_app_docs,
        ),
        Surface(
            name="claude-md",
            policy=Policy.RENDER_REFRESH,
            case_refs="CASE-418 — app CLAUDE.md is generated-then-customised; refresh renders sidecar",
            produce=_claude_md,
        ),
    ]
