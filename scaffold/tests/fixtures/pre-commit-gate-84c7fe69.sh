#!/usr/bin/env bash
# Refuses commits that carry per-installation config or secrets.
#
# Three checks on what is staged:
#   1. Paths that only ever hold local config or secrets (.env, .mcp.json,
#      .claude/kb.json, anything under a secrets/ directory, private keys).
#      .gitignore alone does not stop `git add -f` or a file that was tracked
#      before it was ignored.
#   2. The literal value of any secret on this machine: every file under
#      ~/.wip-deploy/*/secrets/ plus every key file the local config names
#      (*_FILE entries in .env, kb_api_key_file in .claude/kb.json). Catches a
#      real key pasted anywhere, whatever its shape.
#   3. Shapes of known credentials (Anthropic keys, private-key blocks), and
#      gitleaks when it is installed.
#
# The WIP app scaffold ships this file and enables it on every run when the
# clone has no core.hooksPath of its own. By hand:  git config core.hooksPath .githooks
# Bypass a false positive deliberately:  git commit --no-verify
set -uo pipefail

fail=0
report() { echo "pre-commit: $*" >&2; fail=1; }

# The staged paths, NUL-delimited: a path git would print C-quoted (any
# non-ASCII byte, a '"', a backslash or a control character) arrives as itself.
# Quoted, it matched no pattern below and named no file when handed back to
# git, so all three checks passed it. Read into an array in this shell, not
# a piped loop, which would run in a subshell where `fail` is lost; `read -d
# ''` rather than mapfile, which bash 3.2 (macOS) lacks.
staged=()
while IFS= read -r -d '' f; do staged+=("$f"); done \
  < <(git diff --cached -z --name-only --diff-filter=ACMR)
[ "${#staged[@]}" -eq 0 ] && exit 0

# A path handed back to git as a pathspec is taken literally: a name starting
# with ':' or holding glob characters would otherwise name other files, or none.
staged_diff() { git diff --cached -U0 -- ":(literal)$1"; }

# 1. Forbidden paths
for f in "${staged[@]}"; do
  case "$f" in
    .env.example|*.example) ;;
    .env|.env.*|*/.env|*/.env.*) report "$f is local environment config — keep it untracked" ;;
    .mcp.json|*/.mcp.json) report "$f carries instance URLs and key paths — keep it untracked" ;;
    .claude/kb.json) report "$f is per-installation KB config — commit .claude/kb.json.example instead" ;;
    secrets/*|*/secrets/*) report "$f is under a secrets/ directory" ;;
    *.pem|*.key|*.p12|*.pfx|id_rsa*|id_ed25519*) report "$f looks like a private key or certificate bundle" ;;
  esac
done

# 2. Literal secret values from this machine, collected into $vals: each
# file's content with every CR and LF removed, kept when 12 characters or
# longer (shorter values, and empty files, would match ordinary text).
vals="$(mktemp)"; chmod 600 "$vals"
trap 'rm -f "$vals"' EXIT
key_files=()
add_val_file() {
  local p="$1"
  p="${p/#\~/$HOME}"
  [ -f "$p" ] && [ -r "$p" ] && key_files+=("$p")
}
for p in "$HOME"/.wip-deploy/*/secrets/*; do
  case "$p" in *.DS_Store|*.crt) continue ;; esac
  add_val_file "$p"
done
if [ -f .env ]; then
  while IFS= read -r line; do add_val_file "${line#*=}"; done < <(grep -E '^[A-Z0-9_]+_FILE=' .env)
fi
if [ -f .claude/kb.json ]; then
  add_val_file "$(python3 -c 'import json;print(json.load(open(".claude/kb.json")).get("kb_api_key_file",""))' 2>/dev/null)"
fi
# One awk over every file, not a subshell per file: a host with many installs
# holds hundreds of secret files, and forking per file cost over a second on
# every commit. The count guard matters twice: awk with no file reads stdin,
# and bash 3.2 calls an empty "${key_files[@]}" unbound under set -u.
if [ "${#key_files[@]}" -gt 0 ]; then
  awk 'function flush() { if (length(v) >= 12) print v; v = "" }
       FNR == 1 && NR > 1 { flush() }
       { gsub(/\r/, ""); v = v $0 }
       END { flush() }' "${key_files[@]}" >> "$vals"
fi

# Checks 2 and 3 read the same added lines: one diff per staged file. Only
# lines inside a hunk count, so the "+++ b/<path>" header is skipped by
# position, not by prefix: an added line that itself starts with "++" shows
# as "+++…" in the diff and is still checked.
added_lines() { staged_diff "$1" | awk '/^@@/ { h = 1; next } h && /^\+/'; }
for f in "${staged[@]}"; do
  added="$(added_lines "$f")"
  if [ -s "$vals" ] && grep -qF -f "$vals" <<< "$added"; then
    report "$f contains the value of a secret from this machine (value not shown)"
  fi

  # 3. Known credential shapes
  [ "$f" = ".githooks/pre-commit" ] && continue
  if grep -qE 'sk-ant-[A-Za-z0-9_-]{20,}' <<< "$added"; then
    report "$f contains what looks like an Anthropic API key"
  fi
  if grep -qE -- '-----BEGIN [A-Z ]*PRIVATE KEY-----' <<< "$added"; then
    report "$f contains a private key block"
  fi
done

if command -v gitleaks >/dev/null 2>&1; then
  gitleaks protect --staged --redact --no-banner >&2 || report "gitleaks found a likely secret (see above)"
fi

if [ "$fail" -ne 0 ]; then
  echo "pre-commit: commit refused. Unstage the file or remove the value; use --no-verify only for a confirmed false positive." >&2
  exit 1
fi
exit 0
