# /deploy — Routinized app-side redeploy with mandatory pre-flight

Two modes, scoped to the dev-loop. Each runs **mandatory pre-flight** before any destructive operation, the operation itself, then **mandatory smoke** after. Pre-flight refuses on failure; smoke reports without auto-rollback.

| Invocation | Use |
|---|---|
| `/deploy redeploy` | Redeploy this YAC's own source to the currently-running dev install. Most routine. |
| `/deploy verify` | Smoke the currently-running install. No change. Used after a deploy or when diagnosing. |

This skill is a strict subset of BE-YAC's `/deploy` (see the backend equivalent at `docs/slash-commands/backend/deploy.md` in the WIP repo). APP-YACs do not own `install` — fresh installs go through BE-YAC or Peter.

Pre-flight catches the recurring failure modes APP-YACs have hit:

- APP-RC's "wip-deploy fumble" (Day 22): re-ran `install` without replaying the original flag set
- APP-KB's secrets-dir trap (Day 47): `~/.wip-deploy/<namespace>/secrets/` missing because `--name` defaulted to `default`
- APP-KB's manifest-pin loop (Day 51 / CASE-288 round-trip): pushed new build, cluster didn't re-pull

If any pre-flight check fails, output the punch list and stop. Operator addresses the failure or overrides with `--skip-preflight=<check>` (logged in the YAC's session report).

---

## Mode 1 — `/deploy redeploy`

The "I just changed code, get the cluster to re-pull my app" path.

### Pre-flight (all run; output punch list at end)

The recipe branches by **install target** because APP-YACs commonly run against compose dev (`wip-dev-local`), not k8s. `kubectl get nodes` against a compose dev install would silently check the wrong cluster (the operator's k8s context, e.g. wip-kb), giving a false [ok]. Step 0 detects the target so subsequent checks pick the right probe.

```bash
# 0. Detect target from the install directory shape
INSTALL_DIR=~/.wip-deploy/<current-name>
if [ -f "$INSTALL_DIR/docker-compose.yaml" ]; then
  TARGET=compose
elif [ -d "$INSTALL_DIR/services" ]; then
  TARGET=k8s
else
  echo "[FAIL] target detection: $INSTALL_DIR shape doesn't match compose (no docker-compose.yaml) or k8s (no services/ dir)"
  exit 1
fi
echo "[ok] target: $TARGET"

# 1. Runtime reachable + this stack healthy
case "$TARGET" in
  compose)
    # All wip-* containers Up; healthchecks reporting healthy where declared.
    podman ps --format "{{.Names}}  {{.Status}}" | grep -E "^wip-" | awk '
      /healthy/   { ok++ }
      /unhealthy/ { bad++; print "  unhealthy: " $1 }
      /Up [0-9]/  { up++ }
      END { if (bad) exit 1; print "  " up " up, " ok " healthy" }
    '
    ;;
  k8s)
    # Cluster nodes Ready (kubectl points at the correct cluster — verify
    # before relying on this in scripts; --context is the explicit override).
    kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{":"}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}'
    # Expected: all True. If any False/missing → STOP.
    ;;
esac

# 2. Current install state
wip-deploy status --name <current-name>
# Capture: namespace, current image SHA for THIS app's component. Persist for diff in smoke.

# 3. Source state — own repo HEAD
git -C <YAC-repo-root> rev-parse --short HEAD
# Persist for diff.

# 4. Local secrets present
test -f "$INSTALL_DIR/secrets/api-key" && echo "secrets ok" || echo "MISSING — see Day 47 lesson"
# Per Day 47: --name defaults to "default", not the namespace. If missing, instruct: ls ~/.wip-deploy/ to find actual dir.

# 6. Manifest-pin freshness check
# Read components/<self>/wip-component.yaml pin (in the WIP repo) for THIS YAC's own component.
# If pin equals what's currently running per step 2 AND source has changed since: emit
#   [NEEDS CONFIRMATION] punch-list line proposing the new pin. STOP.
# Otherwise: report "no pin bump needed" explicitly.
# Per CASE-298 Departure 1: never auto-bump. The pin file is editorial.
# Compose dev uses bind-mounted source — pin freshness only matters for k8s redeploys
# AND for compose redeploys that fall back to registry images (no --app-source). For
# bind-mounted compose dev runs, this check reports "n/a — bind-mounted source".

# 7. Image-pull hostnames resolvable (k8s only)
case "$TARGET" in
  k8s)
    # For each gitea-class hostname referenced in components/<svc>/wip-component.yaml `image:`:
    #   for node in $(kubectl get nodes -o jsonpath='{.items[*].metadata.name}'); do
    #     ssh "$node" "getent hosts <host> || echo UNRESOLVED on $(hostname)"
    #   done
    # Slim k8s pods don't have getent on PATH; SSH to the node is the reliable probe.
    # If unresolvable on any node: "fix /etc/hosts on <node>" with the exact line to add.
    ;;
  compose)
    # On compose dev, image pulls go through the host's resolver — already working
    # if podman can reach the registry. Skip this check.
    echo "[ok] hostnames: skipped (compose dev uses host resolver)"
    ;;
esac

# A. --app-source points at this YAC's own repo (APP-only check)
case "$TARGET" in
  compose)
    # Inspect $INSTALL_DIR/docker-compose.yaml's build directive for THIS YAC's
    # component. The build context must point at <YAC-repo-root>.
    grep -A 3 "^  <self>:" "$INSTALL_DIR/docker-compose.yaml" | grep -q "context: <YAC-repo-root>"
    # If it points at a registry image instead (image: gitea.local:.../<self>:...
    # without a build directive), REFUSE: "this skill redeploys bind-mounted
    # source, not registry images. Ask BE-YAC for a registry-image refresh."
    ;;
  k8s)
    # K8s installs of APP-YAC components run as registry-image deployments by
    # default. Bind-mounted source on k8s would require kubectl debug or
    # similar non-default plumbing. If the install runs THIS YAC's component
    # off a baked image, REFUSE — the redeploy needs to happen at the BE-YAC
    # level (build, push, manifest-pin bump).
    ;;
esac
```

Pre-flight output is a single punch-list block:

```
=== /deploy redeploy preflight (APP-RC) ===
[ok] cluster reachable
[ok] current install: wip-dev-local namespace, react-console at SHA 7f3e2a1
[ok] source HEAD: 9f5f29f
[ok] secrets present at ~/.wip-deploy/wip-dev-local/secrets/
[NEEDS CONFIRMATION] manifest pin: components/react-console/wip-component.yaml is v1.2.6, source has changed since v1.2.6 was tagged. Bump to v1.2.7? Re-run with --auto-pin to confirm.
[ok] hostnames resolvable
[ok] --app-source points at /Users/peter/Development/WIP-ReactConsole
=== 1 of 7 pre-flight checks NEEDS CONFIRMATION. Refusing redeploy. ===
```

### Operation (only after pre-flight passes — including any operator confirmations)

```bash
# Apply manifest pin bumps the operator confirmed
# (the components/<self>/wip-component.yaml edit surfaced and approved in pre-flight)

# Build + push (or skip-build if --no-build)
scripts/build-release.sh --registry gitea.local:3000/peter --tag <new-pin> --push --insecure <self>

# Redeploy this app's component via the existing dev install
wip-deploy install --name <current-name> [original flags from current install] --app-source <self>=<repo-root>
```

### Smoke (mandatory)

```bash
# 1. Container/pod readiness — the truth-source. kubelet (k8s) and
# podman healthcheck (compose) both run the same /health endpoint a
# curl loop would hit, so this is comprehensive AND low-noise.
case "$TARGET" in
  compose)
    podman ps --format "{{.Names}}  {{.Status}}" | grep -E "^wip-"
    # Expected: all "Up X (healthy)" where a healthcheck is declared.
    # Containers without a healthcheck (e.g. wip-dex, wip-caddy) just
    # show "Up X" — that's fine.
    ;;
  k8s)
    kubectl get pods -n <namespace>
    # Expected: all Running, no CrashLoopBackOff, all 1/1 in READY column.
    ;;
esac

# 2. Bellwether — proves gateway-to-service routing chain is wired end-to-end.
# Hostname differs by target: compose dev defaults to localhost:8443,
# k8s uses the cluster's public hostname (e.g. wip-kb.local).
case "$TARGET" in
  compose) HOST=https://localhost:8443 ;;
  k8s)     HOST=https://<hostname> ;;
esac
curl -sk --max-time 5 "$HOST/api/registry/health" \
  | python3 -c "import sys,json;print(json.loads(sys.stdin.read()).get('status','unknown'))" \
  || echo "registry: BELLWETHER UNREACHABLE"

# 3. YAC-specific (if THIS component exposes /api/<self>/health)
curl -sk --max-time 5 "$HOST/api/<self>/health" 2>&1 \
  | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('status','unknown'))" \
  || echo "<self>: HEALTH UNREACHABLE — verify in browser"

# 4. Browser-side check (human verifies)
echo "Peter: open $HOST/<base-path>/ in browser, verify the YAC's smoke path (e.g., dashboard loads, one read-only page renders)"
```

Smoke output is a checklist; failures don't auto-rollback (the operator decides) but are surfaced clearly.

---

## Mode 2 — `/deploy verify`

Pre-flight steps 1, 2, 4, 7 + the entire Mode-1 smoke section. No change. Used to confirm "is the install still up and healthy" or to gather state before a planned change.

Output: just the punch list. No operation runs.

---

## When NOT to use this skill

- **Net-new install** — APP-YACs do not own `install`. Ask BE-YAC or Peter.
- **Cross-app deployments** — this skill only redeploys the YAC's own component. Multi-app changes go through BE-YAC.
- **Production cluster operations** — APP-YAC's `/deploy` is dev-loop only. Production cutovers go through BE-YAC.
- **Refresh of a registry-image deployment** — if THIS app is running off a baked gitea-registry image (no `--app-source`), redeploy needs to happen at the BE-YAC level (build, push, manifest-pin bump). Pre-flight check A refuses these to make that boundary explicit.

---

## What this does NOT replace

The skill encapsulates the recipe. It does not replace the YAC's judgment when something unusual happens — the skill's job is to surface unusual things in pre-flight or smoke, not to handle them. If pre-flight fails or smoke shows surprises, the YAC reads the output and decides the fix.
