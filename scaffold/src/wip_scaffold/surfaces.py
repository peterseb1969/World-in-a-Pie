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


def mcp_json_surface(
    python_path: str, base_url: str, key_file: str = "", key_literal: str = ""
) -> Surface:
    """Shared .mcp.json writer for both roles (the two bash heredocs had the
    same shape and were drifting apart independently). A key FILE is
    preferred — rotation then applies without re-running the scaffold; a
    literal key exists only for the no-install dev fixture. The remote
    (ssh/http) backend transports write different shapes and stay in bash;
    they simply don't request this surface."""
    key_line = (
        f'"WIP_API_KEY_FILE": "{key_file}"' if key_file
        else f'"WIP_API_KEY": "{key_literal}"'
    )
    body = f"""{{
  "mcpServers": {{
    "wip": {{
      "type": "stdio",
      "command": "{python_path}",
      "args": ["-m", "wip_mcp.server"],
      "env": {{
        {key_line},
        "REGISTRY_URL": "{base_url}",
        "DEF_STORE_URL": "{base_url}",
        "TEMPLATE_STORE_URL": "{base_url}",
        "DOCUMENT_STORE_URL": "{base_url}",
        "REPORTING_SYNC_URL": "{base_url}",
        "WIP_VERIFY_TLS": "false"
      }}
    }}
  }}
}}
"""
    return Surface(
        name="mcp-json",
        policy=Policy.REGENERATE,
        rationale="one writer for both roles ends the two-heredoc drift; key file over literal so rotation needs no re-scaffold",
        produce=lambda ctx: {".mcp.json": body.encode()},
    )


BOOTSTRAP_TEMPLATES = [
    "bootstrap.server.ts.template",
    "bootstrap.routes.ts.template",
    "BootstrapGate.tsx.template",
]


def bootstrap_surface() -> Surface:
    """Genesis copies of the bootstrap starting points, each stamped with a
    banner naming the source commit and a delete-after-use instruction — an
    unmarked frozen copy invites grepping it as canonical scaffold behavior
    (that misread has shipped fixes against long-drifted code). The wrapper
    decides WHEN to seed (create: always; refresh: only on explicit retrofit
    of a dir that is absent — never resurrect a deliberately deleted one);
    this surface only knows HOW. The emitted banner text reproduces the
    legacy scripts byte-for-byte (golden-pinned output)."""

    def produce(ctx: Context) -> dict[str, bytes]:
        import datetime
        import subprocess

        src_dir = ctx.wip_root / "apps/templates/bootstrap"
        if not src_dir.is_dir():
            ctx.notes.append(f"Warning: {src_dir} not found, skipping bootstrap templates")
            return {}
        try:
            sha = subprocess.run(
                ["git", "-C", str(ctx.wip_root), "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=15,
            ).stdout.strip() or "unknown"
        except Exception:
            sha = "unknown"
        date = datetime.date.today().isoformat()
        banner = f"""// ============================================================================
// GENESIS COPY (CASE-415) — not canonical, not live. One-time Phase-1 start.
//   Source: World-in-a-Pie@{sha}, spawned {date}.
//   Canonical scaffold: World-in-a-Pie/apps/templates/bootstrap/ — this frozen
//   copy WILL drift from it. NEVER grep this dir as evidence of platform or
//   scaffold behavior (an agent once did, and shipped fixes against long-drifted code).
//   After you build server/lib/bootstrap.ts from this, DELETE templates/bootstrap/.
// ============================================================================
"""
        out: dict[str, bytes] = {}
        for name in BOOTSTRAP_TEMPLATES:
            src = src_dir / name
            if src.is_file():
                out[f"templates/bootstrap/{name}"] = banner.encode() + src.read_bytes()
            else:
                ctx.notes.append(f"Warning: {name} not found in {src_dir}, skipping")
        return out

    return Surface(
        name="bootstrap-templates",
        policy=Policy.REGENERATE,
        rationale="genesis-stamped one-time starting points; the wrapper gates when, the banner stops canonical-misreads",
        produce=produce,
    )


def env_surface(key_file: str) -> Surface:
    """Create-time .env pointing the runtime at the live secrets FILE, not a
    baked plaintext key — a baked key strands the fleet the moment the
    deploy key rotates; a file path makes rotation a restart-only event.
    The emitted text reproduces the legacy script byte-for-byte
    (golden-pinned output, citation predates the no-citations rule)."""
    body = f"""# Runtime key SOURCE — the live wip-deploy secrets file (CASE-495).
# Resolved at app startup (like the MCP server's WIP_API_KEY_FILE), so a key
# rotation or target-redeploy is picked up on restart rather than baked stale
# here. This is the deployment's admin/proxy key and spans all namespaces — a
# cross-namespace console uses it as-is. A data-model app that wants a
# least-privilege, namespace-scoped key can provision one (POST
# /api/registry/api-keys with "namespaces" + "grant_permission") and repoint
# WIP_API_KEY_FILE below at its own secrets file.
WIP_API_KEY_FILE={key_file}
"""
    return Surface(
        name="env",
        policy=Policy.REGENERATE,
        rationale="key FILE over baked plaintext so rotation is a restart, not a fleet-wide re-scaffold; create-time only (wrapper-gated)",
        produce=lambda ctx: {".env": body.encode()},
    )


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
