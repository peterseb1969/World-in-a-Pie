#!/bin/sh
# Canonical dev-container entrypoint (CASE-58 pattern, CT-YAC's design via RC's
# reference impl). Used by Dockerfile.dev for wip-deploy's --app-source flow.
#
# Handles:
#   1. First-start populate: named volume /app/node_modules is empty → npm ci.
#   2. Staleness after host dep changes: package-lock.json hash changed → npm ci.
#   3. Corrupted install (killed mid-install): .package-lock.json absent → retry.
#   4. Base image change: a new Node version or libc (alpine musl vs slim
#      glibc) with an unchanged lockfile → npm ci, since native packages
#      (esbuild, rollup) and compiled modules belong to the old runtime.
set -e

NODE_MODULES=/app/node_modules
LOCKFILE=/app/package-lock.json
HASH_MARKER="$NODE_MODULES/.wip-lock-hash"

current_hash() {
  [ -f "$LOCKFILE" ] && {
    cat "$LOCKFILE"
    node -v
    if [ -e /etc/alpine-release ]; then echo musl; else echo glibc; fi
  } | sha256sum | cut -c1-64
}

run_install() {
  echo "[dev-entrypoint] $1"
  (cd /app && npm ci --prefer-offline)
  current_hash > "$HASH_MARKER"
}

if [ ! -f "$NODE_MODULES/.package-lock.json" ]; then
  run_install "node_modules empty — running npm ci (first run, expect 30-60s)"
elif [ "$(current_hash)" != "$(cat "$HASH_MARKER" 2>/dev/null)" ]; then
  run_install "package-lock.json or the Node runtime changed — re-running npm ci"
fi

exec "$@"
