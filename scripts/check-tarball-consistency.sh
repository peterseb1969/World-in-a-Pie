#!/usr/bin/env bash
set -euo pipefail

# CASE-500: Mechanical guard for @wip/* vendored-tarball version consistency.
#
# Each app-facing client lib (wip-client, wip-react, wip-proxy) ships to apps as
# a VERSIONED tarball wip-<lib>-<version>.tgz (CASE-442). The recurring failure
# (3×: @wip/react 0.14; CASE-498/495; @wip/client 0.21->0.23) is: bump
# package.json + rebuild dist/, but never `git add -f` the freshly-named tarball.
# package.json + dist/ look bumped; apps re-vendoring the tracked .tgz silently
# get old code. CASE-499 documented the rule in CLAUDE.md; this turns it loud.
#
# For each vendoring lib this asserts:
#   (1) the tarball wip-<lib>-<version>.tgz is TRACKED IN GIT (not merely
#       present on disk — an unstaged fresh .tgz passes `test -f` but is the bug)
#   (2) the tarball's INTERNAL package.json version matches (catches a
#       renamed-but-not-rebuilt tarball that the filename-only check would miss)
#
# Sibling: scripts/create-app-project.sh:lib_tarball() derives the same
# wip-<lib>-<version>.tgz path at DISTRIBUTION time; this is the CI/audit guard.
#
# Usage:
#   scripts/check-tarball-consistency.sh             # guard the repo's libs
#   scripts/check-tarball-consistency.sh --self-test # verify the guard itself
#
# Exit 0 = all consistent (or self-test passed); exit 1 = mismatch (or guard bug).

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Libs that vendor a tarball for app distribution. Authoritative list mirrors
# create-app-project.sh's lib_tarball() calls. A lib named here with a
# missing/uncommitted/mismatched tarball is a hard FAIL — never a skip; "no
# tarball tracked at all" is precisely the failure mode being guarded.
VENDORING_LIBS=(wip-client wip-react wip-proxy)

# Extract the "version" string from a package.json on stdin. sed (not a JSON
# parser) keeps the guard dependency-free, matching create-app-project.sh.
parse_version() {
    sed -n 's/^[[:space:]]*"version":[[:space:]]*"\([^"]*\)".*/\1/p' | head -1
}

# check_lib <libs_root> <lib> -> prints one status line, returns 0 (ok) / 1 (fail)
check_lib() {
    local libs_root="$1" lib="$2"
    local pj="$libs_root/$lib/package.json"

    if [ ! -f "$pj" ]; then
        echo "FAIL  $lib: $pj not found"
        return 1
    fi

    local ver tgz
    ver="$(parse_version < "$pj")"
    if [ -z "$ver" ]; then
        echo "FAIL  $lib: could not read version from $pj"
        return 1
    fi
    tgz="$libs_root/$lib/$lib-$ver.tgz"

    # (1) tracked in git, not just on disk
    if ! git -C "$libs_root" ls-files --error-unmatch "$lib/$lib-$ver.tgz" >/dev/null 2>&1; then
        echo "FAIL  $lib: package.json is $ver but $lib-$ver.tgz is not tracked in git"
        echo "      fix: (cd libs/$lib && npm run build && npm pack) && git add -f libs/$lib/$lib-$ver.tgz"
        return 1
    fi

    # (2) internal package.json version inside the tarball matches the filename/version
    local inner
    inner="$(tar -xzOf "$tgz" package/package.json 2>/dev/null | parse_version || true)"
    if [ "$inner" != "$ver" ]; then
        echo "FAIL  $lib: $lib-$ver.tgz internal version '${inner:-<none>}' != package.json '$ver' (renamed but not rebuilt?)"
        return 1
    fi

    echo "OK    $lib: $ver (tracked, internal version matches)"
    return 0
}

# run_check <libs_root> <lib...> -> 0 if all libs pass, 1 if any fail
run_check() {
    local libs_root="$1"; shift
    local rc=0
    local lib
    for lib in "$@"; do
        check_lib "$libs_root" "$lib" || rc=1
    done
    return "$rc"
}

# ─── --self-test: prove the guard catches the failure modes ──────────────────
# Builds a throwaway git fixture and asserts the guard PASSES a clean lib and
# FAILS the two recurrence shapes (uncommitted tarball; wrong internal version).
# Runs in CI alongside the real check so the guard can't silently rot.
self_test() {
    local tmp; tmp="$(mktemp -d)"
    git -C "$tmp" init -q
    git -C "$tmp" config user.email t@t && git -C "$tmp" config user.name t
    local libs="$tmp/libs"

    make_pkg() {  # <dir> <version>
        mkdir -p "$1"
        printf '{\n  "name": "fixture",\n  "version": "%s"\n}\n' "$2" > "$1/package.json"
    }
    make_tgz() {  # <out.tgz> <internal-version>
        local stage; stage="$(mktemp -d)"
        make_pkg "$stage/package" "$2"
        tar -czf "$1" -C "$stage" package
        rm -rf "$stage"
    }

    # good: version matches, tarball tracked, internal version matches
    make_pkg "$libs/good" "1.2.3"
    make_tgz "$libs/good/good-1.2.3.tgz" "1.2.3"
    # bad-untracked: tarball exists on disk but is NOT committed
    make_pkg "$libs/bad-untracked" "1.0.0"
    make_tgz "$libs/bad-untracked/bad-untracked-1.0.0.tgz" "1.0.0"
    # bad-internal: tarball tracked + correctly named, but rebuilt-stale inside
    make_pkg "$libs/bad-internal" "2.0.0"
    make_tgz "$libs/bad-internal/bad-internal-2.0.0.tgz" "1.9.9"

    # commit everything EXCEPT bad-untracked's tarball
    git -C "$tmp" add libs/good libs/bad-internal libs/bad-untracked/package.json
    git -C "$tmp" commit -qm fixture

    local failures=0
    assert() {  # <description> <expected-rc> <actual-rc>
        if [ "$2" -eq "$3" ]; then
            echo "self-test OK   : $1"
        else
            echo "self-test FAIL : $1 (expected rc=$2, got rc=$3)"
            failures=1
        fi
    }
    local rc
    rc=0; run_check "$libs" good          >/dev/null 2>&1 || rc=$?; assert "clean lib passes"          0 "$rc"
    rc=0; run_check "$libs" bad-untracked >/dev/null 2>&1 || rc=$?; assert "uncommitted tarball fails" 1 "$rc"
    rc=0; run_check "$libs" bad-internal  >/dev/null 2>&1 || rc=$?; assert "stale-internal tarball fails" 1 "$rc"
    # a mixed run with one bad lib must fail overall
    rc=0; run_check "$libs" good bad-untracked >/dev/null 2>&1 || rc=$?; assert "any bad lib fails the run" 1 "$rc"

    rm -rf "$tmp"
    if [ "$failures" -ne 0 ]; then
        echo "SELF-TEST FAILED — the guard is not behaving correctly."
        return 1
    fi
    echo "Self-test passed."
    return 0
}

main() {
    if [ "${1:-}" = "--self-test" ]; then
        self_test
        return $?
    fi

    echo "Checking vendored-tarball consistency (${VENDORING_LIBS[*]})..."
    if run_check "$ROOT_DIR/libs" "${VENDORING_LIBS[@]}"; then
        echo ""
        echo "All vendored tarballs consistent."
        return 0
    fi
    echo ""
    echo "Vendored-tarball consistency check FAILED — see FAIL lines above."
    echo "A bumped package.json/dist with an un-committed (or stale) .tgz means apps"
    echo "re-vendoring the tracked tarball get OLD code. Rebuild + git add -f the tarball."
    return 1
}

main "$@"
