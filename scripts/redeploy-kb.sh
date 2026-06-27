#!/usr/bin/env bash
# redeploy-kb.sh — build the KB app image from WIP-KB, push to GHCR, and roll it on
# the kb.internal k8s cluster, with the bits agents kept forgetting BAKED IN and
# every failure made LOUD. Born from two compounding mistakes on 2026-06-27:
#   1. a manual build omitted --build-arg VITE_BASE_PATH=/apps/kb/  → assets at "/"
#      → blank page (MIME error). This script always passes it AND verifies the
#      built image's index.html actually references /apps/kb/assets/ BEFORE deploy.
#   2. a build silently no-op'd (script file didn't exist, exit 127) so the tag was
#      never pushed, then `kubectl set image` pointed at a missing tag →
#      ImagePullBackOff. This script REFUSES to set the image until skopeo confirms
#      the tag is in GHCR.
#
# Usage:
#   scripts/redeploy-kb.sh                    # build HEAD, auto-tag, verify, deploy
#   scripts/redeploy-kb.sh --tag 20260627c    # explicit tag
#   scripts/redeploy-kb.sh --no-build --tag T # deploy an already-pushed tag (verify+roll)
#   scripts/redeploy-kb.sh --rollback 20260626g   # roll back to a known-good tag
#   scripts/redeploy-kb.sh --dry-run          # print the plan, touch nothing
#
# Env overrides (defaults target Peter's canonical setup):
#   WIP_KB_DIR   (default: $HOME/Development/WIP-KB)
#   KUBE_CONTEXT (default: microk8s)   KB_NAMESPACE (default: kb)
set -euo pipefail

# ── Config (one place; not scattered across an agent's memory) ──────────────────
WIP_KB_DIR="${WIP_KB_DIR:-$HOME/Development/WIP-KB}"
IMAGE="ghcr.io/peterseb1969/wip-kb"
DEPLOYMENT="wip-wip-kb"        # k8s deployment name
CONTAINER="wip-kb"            # container within the deployment
KB_NAMESPACE="${KB_NAMESPACE:-kb}"
KUBE_CONTEXT="${KUBE_CONTEXT:-microk8s}"
PLATFORM="linux/arm64"        # Pi nodes are arm64; native on Apple Silicon
BASE_PATH="/apps/kb/"          # the forgotten build-arg — the app is ALWAYS mounted here
ASSET_MARKER="/apps/kb/assets/"   # what a correct index.html must reference

TAG=""; DO_BUILD=true; ROLLBACK=""; DRY_RUN=false
while [ $# -gt 0 ]; do
    case "$1" in
        --tag) TAG="$2"; shift 2 ;;
        --no-build) DO_BUILD=false; shift ;;
        --rollback) ROLLBACK="$2"; DO_BUILD=false; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
        *) echo "Unknown arg: $1" >&2; exit 2 ;;
    esac
done

die()  { echo "ERROR: $*" >&2; exit 1; }
step() { echo; echo "▶ $*"; }
run()  { if $DRY_RUN; then echo "  [dry-run] $*"; else eval "$*"; fi; }

command -v podman >/dev/null || die "podman not found"
command -v skopeo >/dev/null || die "skopeo not found (the image-existence gate needs it)"
command -v kubectl >/dev/null || die "kubectl not found"

kube() { kubectl --context "$KUBE_CONTEXT" -n "$KB_NAMESPACE" "$@"; }

# --raw works for BOTH single-arch images AND multi-arch manifest lists; plain
# `skopeo inspect` false-negatives on a list (can't resolve one platform), which
# would let auto-tag overwrite a good tag and make the deploy-gate reject valid
# multi-arch images. Do not "simplify" this back to a bare inspect.
image_in_ghcr() { skopeo inspect --raw "docker://$IMAGE:$1" >/dev/null 2>&1; }

current_deployed_tag() {
    kube get deploy "$DEPLOYMENT" -o jsonpath='{.spec.template.spec.containers[0].image}' --request-timeout=15s 2>/dev/null
}

# ── Rollback mode: verify the target exists, capture current, roll ──────────────
if [ -n "$ROLLBACK" ]; then
    step "Rollback to $IMAGE:$ROLLBACK"
    image_in_ghcr "$ROLLBACK" || die "rollback tag $ROLLBACK is not in GHCR — refusing"
    echo "  current: $(current_deployed_tag)"
    run "kube set image deployment/$DEPLOYMENT $CONTAINER=$IMAGE:$ROLLBACK --request-timeout=15s"
    run "kube rollout status deployment/$DEPLOYMENT --timeout=180s"
    echo "Rolled back to $ROLLBACK."
    exit 0
fi

# ── Resolve the tag (auto-pick next free YYYYMMDD<letter> if not given) ─────────
if [ -z "$TAG" ]; then
    today="$(date +%Y%m%d)"
    for L in a b c d e f g h i j k l m n o p q r s t u v w x y z; do
        if ! image_in_ghcr "$today$L"; then TAG="$today$L"; break; fi
    done
    [ -n "$TAG" ] || die "could not auto-pick a free tag for $today — pass --tag"
    echo "Auto-tag: $TAG"
fi

# ── Build (with the build-arg that must never be forgotten) ─────────────────────
if $DO_BUILD; then
    [ -f "$WIP_KB_DIR/Dockerfile" ] || die "no Dockerfile at $WIP_KB_DIR (set WIP_KB_DIR)"
    SHA="$(git -C "$WIP_KB_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    step "Build $IMAGE:$TAG from $WIP_KB_DIR @ $SHA (VITE_BASE_PATH=$BASE_PATH, $PLATFORM)"
    run "echo \"\$(gh auth token)\" | podman login ghcr.io -u peterseb1969 --password-stdin >/dev/null"
    run "podman build --platform $PLATFORM \
        --build-arg VITE_BASE_PATH=$BASE_PATH \
        --build-arg VITE_BUILD_SHA=$SHA \
        -t $IMAGE:$TAG -f $WIP_KB_DIR/Dockerfile $WIP_KB_DIR"

    # GATE 1 — the built artifact must reference the correct asset base. This is
    # the exact check that would have caught the blank-page bug at its source.
    step "Verify built image references $ASSET_MARKER (the asset-base gate)"
    if ! $DRY_RUN; then
        html="$(podman run --rm --entrypoint sh "$IMAGE:$TAG" -c \
            'cat "$(find / -name index.html -path "*dist*" 2>/dev/null | head -1)"' 2>/dev/null || true)"
        echo "$html" | grep -q "$ASSET_MARKER" \
            || die "built index.html does NOT reference $ASSET_MARKER — base path is wrong, NOT deploying"
        echo "  ok: index.html references $ASSET_MARKER"
    fi

    step "Push $IMAGE:$TAG"
    run "podman push $IMAGE:$TAG"
fi

# GATE 2 — never set the cluster image to a tag that isn't in GHCR.
step "Verify $IMAGE:$TAG is in GHCR before touching the cluster"
if ! $DRY_RUN; then
    image_in_ghcr "$TAG" || die "$IMAGE:$TAG is NOT in GHCR — refusing to set image (would ImagePullBackOff)"
    echo "  ok: $IMAGE:$TAG present in GHCR"
fi

# ── Roll (capture rollback ref first) ───────────────────────────────────────────
ROLLBACK_REF="$(current_deployed_tag || true)"
step "Roll $DEPLOYMENT → $IMAGE:$TAG   (rollback ref: ${ROLLBACK_REF:-unknown})"
run "kube set image deployment/$DEPLOYMENT $CONTAINER=$IMAGE:$TAG --request-timeout=15s"
run "kube rollout status deployment/$DEPLOYMENT --timeout=180s"

if ! $DRY_RUN; then
    step "Confirm the running pod is on $TAG"
    kube get pods -l "app.kubernetes.io/name=$CONTAINER" \
        -o custom-columns='POD:.metadata.name,READY:.status.containerStatuses[0].ready,IMG:.spec.containers[0].image' \
        --request-timeout=10s | grep -v Terminating
fi

echo
echo "✅ Deployed $IMAGE:$TAG"
echo "   served-page check is OIDC-gated (302) — hard-refresh https://kb.internal/apps/kb/ to confirm in-browser."
echo "   rollback:  $0 --rollback ${ROLLBACK_REF##*:}"
