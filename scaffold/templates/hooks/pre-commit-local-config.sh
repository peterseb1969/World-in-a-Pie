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

staged="$(git diff --cached --name-only --diff-filter=ACMR)"
[ -z "$staged" ] && exit 0

# 1. Forbidden paths
while IFS= read -r f; do
  case "$f" in
    .env.example|*.example) ;;
    .env|.env.*|*/.env|*/.env.*) report "$f is local environment config — keep it untracked" ;;
    .mcp.json|*/.mcp.json) report "$f carries instance URLs and key paths — keep it untracked" ;;
    .claude/kb.json) report "$f is per-installation KB config — commit .claude/kb.json.example instead" ;;
    secrets/*|*/secrets/*) report "$f is under a secrets/ directory" ;;
    *.pem|*.key|*.p12|*.pfx|id_rsa*|id_ed25519*) report "$f looks like a private key or certificate bundle" ;;
  esac
done <<< "$staged"

# 2. Literal secret values from this machine
vals="$(mktemp)"; chmod 600 "$vals"
trap 'rm -f "$vals"' EXIT
add_val_file() {
  local p="$1"
  p="${p/#\~/$HOME}"
  [ -f "$p" ] && [ -r "$p" ] || return 0
  # Short values (and empty files) would match ordinary text.
  local v; v="$(tr -d '\r\n' < "$p")"
  [ "${#v}" -ge 12 ] && printf '%s\n' "$v" >> "$vals"
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
if [ -s "$vals" ]; then
  while IFS= read -r f; do
    if git diff --cached -U0 -- "$f" | grep '^+' | grep -qF -f "$vals"; then
      report "$f contains the value of a secret from this machine (value not shown)"
    fi
  done <<< "$staged"
fi

# 3. Known credential shapes
while IFS= read -r f; do
  [ "$f" = ".githooks/pre-commit" ] && continue
  added="$(git diff --cached -U0 -- "$f" | grep '^+' | grep -v '^+++')"
  if grep -qE 'sk-ant-[A-Za-z0-9_-]{20,}' <<< "$added"; then
    report "$f contains what looks like an Anthropic API key"
  fi
  if grep -qE -- '-----BEGIN [A-Z ]*PRIVATE KEY-----' <<< "$added"; then
    report "$f contains a private key block"
  fi
done <<< "$staged"

if command -v gitleaks >/dev/null 2>&1; then
  gitleaks protect --staged --redact --no-banner >&2 || report "gitleaks found a likely secret (see above)"
fi

if [ "$fail" -ne 0 ]; then
  echo "pre-commit: commit refused. Unstage the file or remove the value; use --no-verify only for a confirmed false positive." >&2
  exit 1
fi
exit 0
