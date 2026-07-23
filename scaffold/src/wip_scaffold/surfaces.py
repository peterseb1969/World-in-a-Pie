"""The surface matrix — content-family surfaces for both roles.

All file surfaces live here: content propagation (CLAUDE.md, settings,
commands, playbooks, reference docs, hook, wake-rollover, session-role,
app-meta), .mcp.json, distribution (client-lib tarballs, toolkit wheel,
bootstrap templates, query preset), and .env. ACTIONS — anything with an
external side effect (venv bootstrap, enable_kb, the namespace upsert,
npm/wheel builds, the lockfile sync, git init) — stay in the bash
wrappers permanently; the boundary rule is in the design doc §3b: if it
can't be expressed as "write these bytes under this policy", it's an
action. Create-only surfaces (bootstrap, query preset, .env) are
REGENERATE here; their create-only semantics are enforced at the wrapper
call site, which passes their flags only on create.

Every entry carries a substantive rationale — the incident lesson written
out, not a ticket number — and the unit tests pin each policy's behavior
so "why is this here" survives refactors.
"""

from __future__ import annotations

from pathlib import Path

from .engine import Context, Policy, Surface
from .render import render

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


QUERY_SCAFFOLD_ROOT_FILES = [
    "package.json",
    "tsconfig.json",
    "vite.config.ts",
    "tailwind.config.js",
    "postcss.config.js",
    "index.html",
    ".env.example",
    "Dockerfile",
    "Dockerfile.dev",
    ".dockerignore",
]


def query_scaffold_surfaces(app_name: str, app_slug: str, dev_namespace: str) -> list[Surface]:
    """NL-query preset starting files (create-only; the wrapper gates when).
    A curated subset is copied — package-lock.json and any future strays in
    the scaffold dir are deliberately NOT shipped. Placeholder substitution
    happens during the copy (SCAFFOLD_APP_SLUG/NAME, the WIP clone path and
    the dev namespace in .env.example) — in-process string replacement, so
    no sed -i flavor dependency. The dev entrypoint keeps its exec bit via
    its own surface."""

    def produce(ctx: Context) -> dict[str, bytes]:
        src_root = ctx.wip_root / "scripts/scaffold-query"
        if not src_root.is_dir():
            raise FileNotFoundError(f"scaffold template directory not found: {src_root}")
        out: dict[str, bytes] = {}
        for sub_dir in ("server", "src"):
            for f in sorted((src_root / sub_dir).rglob("*")):
                if f.is_file():
                    out[str(f.relative_to(src_root))] = f.read_bytes()
        for name in QUERY_SCAFFOLD_ROOT_FILES:
            out[name] = (src_root / name).read_bytes()
        out[".github/workflows/build.yaml"] = (
            src_root / ".github/workflows/build.yaml"
        ).read_bytes()

        for name in ("package.json", ".github/workflows/build.yaml"):
            text = out[name].decode()
            out[name] = text.replace("SCAFFOLD_APP_SLUG", app_slug).encode()
        out["index.html"] = out["index.html"].decode().replace(
            "SCAFFOLD_APP_NAME", app_name).encode()
        env_example = out[".env.example"].decode()
        env_example = env_example.replace("/path/to/WorldInPie", str(ctx.wip_root))
        env_example = env_example.replace(
            "# WIP_NAMESPACE=myapp", f"WIP_NAMESPACE={dev_namespace}")
        out[".env.example"] = env_example.encode()

        # .gitignore merge: scaffold additions append to whatever the app
        # already has (create mode normally has none).
        existing = ctx.target_root / ".gitignore"
        scaffold_ignore = (src_root / ".gitignore").read_bytes()
        if existing.is_file():
            out[".gitignore"] = existing.read_bytes() + scaffold_ignore
        else:
            out[".gitignore"] = scaffold_ignore
        return out

    def produce_entrypoint(ctx: Context) -> dict[str, bytes]:
        src_root = ctx.wip_root / "scripts/scaffold-query"
        return {
            "docker-entrypoint-dev.sh": (src_root / "docker-entrypoint-dev.sh").read_bytes()
        }

    return [
        Surface(
            name="query-scaffold",
            policy=Policy.REGENERATE,
            rationale="curated create-time copy with in-process placeholder substitution — no sed -i flavor dependency, no accidental strays",
            produce=produce,
        ),
        Surface(
            name="query-entrypoint",
            policy=Policy.REGENERATE,
            rationale="dev entrypoint must stay executable; exec bit is a surface property",
            executable=True,
            produce=produce_entrypoint,
        ),
    ]


def client_lib_surface(lib: str, tarball_path: str) -> Surface:
    """One vendored client library: validate, immutability-check, wipe stale
    versions, copy, extract README — the file-surface half of distribution.
    Building/rebuilding a tarball is an npm ACTION and stays in the wrapper.

    Versioned tarballs are immutable: the same filename must always mean the
    same bytes, because the app's package-lock.json records a content hash
    against the `file:` spec — same-name-different-content silently
    re-points a version at new bytes and `npm ci` then fails with
    EINTEGRITY. That violation is fatal here, never papered over."""

    def produce(ctx: Context) -> dict[str, bytes]:
        import tarfile

        src = Path(tarball_path)
        data = src.read_bytes()

        # Belt on top of the wrapper's pre-validation: a tarball with no
        # compiled dist/*.js means npm pack ran without npm run build.
        with tarfile.open(src, "r:gz") as tf:
            names = tf.getnames()
            if not any(n.startswith("package/dist/") and n.endswith(".js") for n in names):
                ctx.notes.append(
                    f"ERROR: {src.name} contains no compiled JS in dist/ — skipped. "
                    f"Fix: npm run build && npm pack in libs/wip-{lib}."
                )
                return {}
            readme: bytes | None = None
            try:
                member = tf.extractfile("package/README.md")
                readme = member.read() if member else None
            except KeyError:
                readme = None

        dest = ctx.target_root / "libs" / src.name
        if dest.is_file() and dest.read_bytes() != data:
            raise RuntimeError(
                f"{src.name} already exists in the app with different content. "
                f"The library's content changed without a version bump (versioned "
                f"tarballs are immutable — the lockfile pins a content hash per "
                f"filename). Bump the version in the lib's package.json, npm pack, "
                f"commit the new tarball, then re-run this script."
            )

        out: dict[str, bytes] = {f"libs/{src.name}": data}
        if readme is not None:
            out[f"libs/wip-{lib}-README.md"] = readme
        else:
            ctx.notes.append(f"Copied: {src.name} (README extraction failed)")
        return out

    return Surface(
        name=f"lib-{lib}",
        policy=Policy.REGENERATE,
        rationale="stale versioned tarballs are wiped so the app's libs/*.tgz install glob resolves to exactly one file; same-name-different-content is fatal (lockfile pins content hashes)",
        wipe_glob=f"libs/wip-{lib}-*.tgz",
        produce=produce,
    )


def _wheel_produce(wheel_path: str):
    """Copy a built wheel into the app's libs/, refusing silent re-pointing.

    Versioned wheels are immutable the same way the npm tarballs are: pip
    treats an already-installed version as satisfied, so shipping changed
    bytes under an unchanged filename means consumers keep running the OLD
    code with no error anywhere — the drift is invisible until someone
    hits a bug the new bytes already fixed. If the app holds a same-name
    wheel with different content, the version was not bumped: fatal, never
    papered over. (The wheels are reproducible builds — hatchling pins
    member timestamps — so an unchanged tree produces identical bytes and
    never trips this.)
    """

    def produce(ctx: Context) -> dict[str, bytes]:
        src = Path(wheel_path)
        data = src.read_bytes()
        dest = ctx.target_root / "libs" / src.name
        if dest.is_file() and dest.read_bytes() != data:
            raise RuntimeError(
                f"{src.name} already exists in the app with different content. "
                f"The wheel's content changed without a version bump (versioned "
                f"wheels are immutable — pip will not reinstall a version it "
                f"already has, so same-name-new-bytes ships code nobody runs). "
                f"Bump the version in the project's pyproject.toml, rebuild, "
                f"then re-run this script."
            )
        return {f"libs/{src.name}": data}

    return produce


def toolkit_surface(wheel_path: str) -> Surface:
    """Vendored wip-toolkit wheel copy. Building the wheel is an action the
    wrapper owns; this only ships an existing artifact."""
    return Surface(
        name="toolkit-wheel",
        policy=Policy.REGENERATE,
        rationale="ship the built wheel into the app's libs/, wiping stale versions so pip install libs/*.whl resolves to one file; same-name-different-content is fatal (pip never reinstalls an unchanged version); the build itself is a wrapper action",
        wipe_glob="libs/wip_toolkit-*.whl",
        produce=_wheel_produce(wheel_path),
    )


def archive_wheel_surface(wheel_path: str) -> Surface:
    """Vendored wip-archive wheel copy — the toolkit wheel's dependency
    (archive format + remap library). Ships alongside the toolkit wheel so
    `pip install libs/*.whl` resolves the toolkit's `wip-archive` requirement
    offline; same wrapper-builds / engine-ships split as toolkit_surface."""
    return Surface(
        name="archive-wheel",
        policy=Policy.REGENERATE,
        rationale="the toolkit wheel declares wip-archive as a dependency that is not on PyPI; shipping its wheel beside the toolkit's keeps app installs offline and version-matched; same-name-different-content is fatal",
        wipe_glob="libs/wip_archive-*.whl",
        produce=_wheel_produce(wheel_path),
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
            rationale="ONE baseline for both roles — the old per-role pair drifted apart; scaffold-owned, regenerated every run; settings.local.json is operator-owned and never touched",
            produce=lambda ctx: {
                ".claude/settings.json": _template(ctx, "settings/settings.json").encode()
            },
        ),
        Surface(
            name="hook",
            policy=Policy.REGENERATE,
            rationale="compaction evicts the baseline reading exactly when drift starts; the re-anchor now fires for BOTH roles — the backend gap was accidental, not designed",
            executable=True,
            produce=lambda ctx: {
                ".claude/hooks/post-compact-reanchor.sh": _template(
                    ctx, "hooks/post-compact-reanchor.sh"
                ).encode()
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
        # ROLE_PREFIX is persisted here because .session-role is gitignored:
        # a fresh checkout has no local role file, and without a committed
        # record nothing can restore it — /wip-setup then dead-ends on a
        # brand-new clone. Precedence: this run's --prefix, else the local
        # file (which the session-role surface has already settled by the
        # time this surface runs).
        role = ctx.role_prefix
        if not role:
            role_file = ctx.target_root / ".claude/.session-role"
            if role_file.is_file():
                role = role_file.read_text().strip()
        # The header below is EMITTED OUTPUT reproducing the legacy scripts
        # byte-for-byte (golden-pinned) — its citation predates the
        # no-citations rule; changing it is a content edit, not cleanup.
        body = (
            "# Generated by create-app-project.sh (CASE-418). Read on set-up-in-place to\n"
            "# regenerate CLAUDE.md. Edit deliberately if the app's metadata changes.\n"
            + "".join(f'{k}="{v}"\n' for k, v in app_meta.items())
            + f'ROLE_PREFIX="{role}"\n'
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
            rationale="ONE baseline for both roles — the old per-role pair drifted apart; settings.local.json never touched",
            produce=lambda ctx: {
                ".claude/settings.json": _template(ctx, "settings/settings.json").encode()
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
