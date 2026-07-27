#!/usr/bin/env bash
# WIP single-host installer — fetch a release, install the wip-deploy CLI.
#
#   curl -fsSL https://raw.githubusercontent.com/peterseb1969/World-in-a-Pie/develop/install.sh | bash
#
# What it does: downloads the release source tarball (no git needed), creates
# a private venv, installs the wip-deploy CLI, and drops a `wip-deploy`
# wrapper on your PATH that knows where the repo lives. It does NOT start
# any containers — it prints the install command for you to run.
#
# Environment overrides:
#   WIP_VERSION  release tag to install (default: latest GitHub release)
#   WIP_HOME     where to put the tree (default: ~/wip)
set -euo pipefail

REPO="peterseb1969/World-in-a-Pie"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARN:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# ── Preflight ────────────────────────────────────────────────────────────
for cmd in curl tar python3; do
    command -v "$cmd" >/dev/null 2>&1 || die "'$cmd' is required but not found"
done
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
    || die "Python 3.11+ is required (found $(python3 --version 2>&1))"

runtime=""
if command -v podman >/dev/null 2>&1; then
    runtime="podman"
    command -v podman-compose >/dev/null 2>&1 \
        || warn "podman found but podman-compose is missing — install it before running 'wip-deploy install'"
elif command -v docker >/dev/null 2>&1; then
    runtime="docker"
else
    warn "no container runtime found (podman or docker) — the CLI will install, but 'wip-deploy install' needs one"
fi

# ── Resolve version ──────────────────────────────────────────────────────
VERSION="${WIP_VERSION:-}"
if [ -z "$VERSION" ]; then
    say "Resolving latest release..."
    VERSION=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])') \
        || die "could not resolve the latest release tag (set WIP_VERSION=vX.Y.Z to pin one)"
fi
say "Installing WIP $VERSION"

# ── Fetch the tree ───────────────────────────────────────────────────────
DEST="${WIP_HOME:-$HOME/wip}"
if [ -e "$DEST" ] && [ -n "$(ls -A "$DEST" 2>/dev/null)" ]; then
    die "$DEST already exists and is not empty — remove it or set WIP_HOME elsewhere"
fi
mkdir -p "$DEST"
say "Downloading source tarball to $DEST ..."
curl -fSL "https://github.com/$REPO/archive/refs/tags/$VERSION.tar.gz" \
    | tar -xz -C "$DEST" --strip-components=1 \
    || die "download/extract failed for tag $VERSION"

# ── Install the CLI ──────────────────────────────────────────────────────
say "Creating venv and installing the wip-deploy CLI..."
python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install --quiet --upgrade pip
"$DEST/.venv/bin/pip" install --quiet "$DEST/deployer"

# Wrapper: makes `wip-deploy` work from any directory — the CLI locates the
# component/app manifests via WIP_REPO_ROOT, which the wrapper pins to the
# tree we just installed. A pre-existing wrapper is replaced only if this
# installer wrote it (marker line); anything else is preserved as .bak.
wrapper_dir="$HOME/.local/bin"
wrapper="$wrapper_dir/wip-deploy"
mkdir -p "$wrapper_dir"
if [ -e "$wrapper" ] && ! grep -q "wip-installer-wrapper" "$wrapper" 2>/dev/null; then
    warn "$wrapper exists and was not written by this installer — keeping it as wip-deploy.bak"
    mv "$wrapper" "$wrapper.bak"
fi
cat > "$wrapper" <<WRAP
#!/usr/bin/env bash
# wip-installer-wrapper (written by install.sh — safe to regenerate)
exec env WIP_REPO_ROOT="$DEST" "$DEST/.venv/bin/wip-deploy" "\$@"
WRAP
chmod +x "$wrapper"

on_path=""
case ":$PATH:" in
    *":$wrapper_dir:"*) on_path=yes ;;
esac

# ── Done ─────────────────────────────────────────────────────────────────
say "Installed. WIP $VERSION lives in $DEST"
echo
echo "The 'wip-deploy' command was written to $wrapper_dir/wip-deploy"
if [ -z "$on_path" ]; then
    echo "NOTE: $wrapper_dir is not on your PATH — add it, or call the wrapper by full path:"
    echo "  export PATH=\"$wrapper_dir:\$PATH\""
fi
if [ "$runtime" = "podman" ]; then
    echo
    echo "Make sure podman is running before installing (on macOS: podman machine init && podman machine start)."
fi
cat <<NEXT

Next — bring up the platform with published images matching this release:

  wip-deploy install \\
    --preset standard \\
    --target compose \\
    --name wip \\
    --hostname localhost \\
    --registry ghcr.io/peterseb1969 \\
    --tag $VERSION

Then verify:   curl -k https://localhost:8443/api/registry/health

Want the React Console admin UI or other apps on top? See
$DEST/docs/deploy/podman/README.md (section "Installing apps").
NEXT
