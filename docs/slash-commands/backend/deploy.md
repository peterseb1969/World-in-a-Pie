# /deploy — Routinized deployment with mandatory pre-flight

Three modes. Each runs a **mandatory pre-flight** before any destructive operation, the operation itself, then a **mandatory smoke** after. Pre-flight refuses to proceed on failure; smoke reports failures but does not auto-rollback.

| Invocation | Use |
|---|---|
| `/deploy redeploy [<service-name>]` | Redeploy current source to currently-running namespace. With service name: single-service redeploy. Without: full preset reapply. |
| `/deploy install --preset <p> --target <t> [more flags]` | Full install. Wraps `wip-deploy install` with pre-flight. |
| `/deploy verify` | Smoke-only. No change. Used after a deploy or when diagnosing. |

Pre-flight catches the recurring failure modes the constellation has hit:

- CASE-282: `wip-deploy install --target dev` silently using stale registry images
- CASE-288 manifest-pin lesson: pushing same tag doesn't re-pull on k8s
- CASE-247: manual self-signed TLS ceremony
- Day 46/47 nss-mdns: `.local` resolution gaps on Pi nodes
- Day 47 secrets-dir-missing: `--name` defaulting to `default` not the namespace
- Day 51 deletion_mode-retain blocking nuke

If any pre-flight check fails, output the punch list and stop. Operator (Peter) addresses the failure or overrides with `--skip-preflight=<check>` (logged in commits.md).

---

## Mode 1 — `/deploy redeploy [<service-name>]`

The "I just changed code, get the cluster to re-pull" path. Most routine.

### Pre-flight (all run; output punch list at end)

```bash
# 1. Cluster reachable + healthy
kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{":"}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}'
# Expected: all True. If any False/missing → STOP.

# 2. Current install state — what's running, on what image, in what namespace
wip-deploy status --name <current-name>
# Capture: namespace, current image SHAs per service. Persist for diff in smoke.

# 3. Source state — git rev-parse HEAD on World-in-a-Pie + each --app-source path
cd /Users/peter/Development/World-in-a-Pie && git rev-parse --short HEAD
# Plus each app source if --target dev. Persist for diff.

# 4. Local secrets present
test -f ~/.wip-deploy/<name>/secrets/api-key && echo "secrets ok" || echo "MISSING — see Day 47 lesson"
# Per Day 47: --name defaults to "default", not the namespace. If missing, instruct: ls ~/.wip-deploy/ to find actual dir.

# 5. Image-source policy check (CASE-282 prevention)
# If --target dev AND --app-source NOT supplied for an app: REFUSE. State the rule.

# 6. Manifest-pin freshness check (CASE-288 prevention)
# Read components/<svc>/wip-component.yaml pin for each service to redeploy.
# If pin equals what's currently running per step 2 AND source has changed since:
# SURFACE the bump as a punch-list item — propose the new pin (e.g., v1.2.6 → v1.2.7)
# and STOP. The operator confirms before any pin file is edited. Do not auto-bump
# without explicit operator approval; the pin file is a tracked artifact and the
# bump is a discrete editorial decision, not a side effect of /deploy.
# (Operator can override the prompt with `/deploy redeploy --auto-pin <svc>`.)
# If pin is already ahead of running OR source unchanged: report "no pin bump needed".

# 7. Image-pull hostnames resolvable from cluster nodes (Day 46/47 nss-mdns prevention)
#
# Scope: ONLY hostnames the cluster's container runtime needs to resolve to
# pull images — that's the `gitea.local` style registry hostname referenced
# in `components/<svc>/wip-component.yaml` `image:` lines. The browser-facing
# ingress hostname (e.g., `wip-kb.local`) is NOT in scope — pods don't talk
# to it; only the operator's Mac and end-user browsers resolve it.
#
# Slim k8s pods don't have `getent`/`nslookup`/`dig` on PATH, so probe via
# direct SSH to each node (works on the Pi cluster):
#
#   for node in $(kubectl get nodes -o jsonpath='{.items[*].metadata.name}'); do
#     for host in gitea.local; do  # extend list per spec.images.registry
#       ssh "$node" "getent hosts $host || grep -F \" $host \" /etc/hosts || echo 'UNRESOLVED on '$(hostname)"
#     done
#   done
#
# Alternative when SSH is unavailable: `kubectl debug node/<name> --image=busybox
# -- nslookup <host>`, when kubectl debug is enabled on the cluster.
#
# Day 46/47 fix: /etc/hosts entries on every node for image-pull hostnames
# (e.g., `192.168.1.17  gitea.local`). Pi-Hole-driven local DNS would also
# work; whichever the operator chose, this check verifies it's actually live.

# 8. For k8s target: TLS secret present in target namespace (CASE-247 prevention)
kubectl -n <namespace> get secret <tls-secret-name> 2>&1 | grep -q "NotFound" && echo "TLS SECRET MISSING — pre-create with: openssl req ... && kubectl create secret tls"
```

Pre-flight output is a single punch-list block:

```
=== /deploy redeploy preflight ===
[ok] cluster reachable
[ok] current install: wip-kb namespace, 10 services on v1.2.6
[ok] source HEAD: 9f5f29f
[ok] secrets present at ~/.wip-deploy/wip-kb/secrets/
[ok] image-source policy
[NEEDS CONFIRMATION] manifest pin: components/mcp-server/wip-component.yaml is v1.2.6, source has changed since. Propose bump → v1.2.7. Confirm? (y / re-run with --auto-pin mcp-server / abort)
[ok] hostnames resolvable
[ok] tls secret present
=== 1 of 8 pre-flight checks needs confirmation. Refusing redeploy until resolved. ===
```

### Operation (only after pre-flight passes — including any operator confirmations)

```bash
# Apply manifest pin bumps the operator confirmed
# (the components/<svc>/wip-component.yaml edits surfaced and approved in pre-flight)

# Build + push (or skip-build if --no-build flag)
scripts/build-release.sh --registry gitea.local:3000/peter --tag <new-pin> --push --insecure [<service>]

# Redeploy
wip-deploy install --name <current-name> [other flags from current install]
```

### Smoke (mandatory)

```bash
# Pod readiness — the truth-source. kubelet runs each pod's
# readinessProbe / livenessProbe (the same /health endpoint a curl loop
# would hit), so this is comprehensive *and* low-noise.
kubectl get pods -n <namespace>
# Expected: all Running, no CrashLoopBackOff, all 1/1 in READY column.

# Bellwether curl — proves the gateway → ingress → service routing chain
# is wired end-to-end. If this works, the whole routing stack works for
# anything else under /api/. We use registry as the bellwether because
# every other service depends on it for identity resolution.
curl -sk --max-time 5 https://<hostname>/api/registry/health \
  | python3 -c "import sys,json;print(json.loads(sys.stdin.read()).get('status','unknown'))" \
  || echo "registry: BELLWETHER UNREACHABLE"

# Service-specific routes have non-uniform shapes (mcp-server at /mcp,
# auth-gateway at /auth, etc.) so a hard-coded for-loop over /api/<svc>/health
# is fragile. If a specific service is in scope for the redeploy, curl that
# one's actual route per its components/<svc>/wip-component.yaml `routes:`
# entry. Otherwise, trust kubelet.

# Browser-side check (k8s only, human verifies):
echo "Peter: open https://<hostname>/ in browser, verify auth flow + at least one read-only data path"
```

Smoke output is a checklist; failures don't auto-rollback (the operator decides) but are surfaced clearly.

---

## Mode 2 — `/deploy install --preset <p> --target <t> [more flags]`

Full install. Wraps `wip-deploy install` with the same pre-flight (steps 1, 4, 6, 7, 8 above) plus install-specific checks:

- **9. Target consistency** — if `--target dev` AND any app references gitea-registry images, REFUSE (CASE-282 prevention). Dev-target apps must use `--app-source`.
- **10. Namespace pre-existence** — if the namespace exists already AND the install command would change its `deletion_mode` or `isolation_mode`, surface the change explicitly. Day 51 deletion_mode lesson.
- **11. Required apps' `wip-app.yaml` reachable** — for each `--app NAME`, verify `apps/<name>/wip-app.yaml` exists AND parses.

Pre-flight punch list, install command, smoke. Same shape as Mode 1.

---

## Mode 3 — `/deploy verify`

Pre-flight steps 1, 2, 4, 7 + the entire Mode-1 smoke section. No change. Used to confirm "is the install still up and healthy" or to gather state before a planned change.

Output: just the punch list. No operation runs.

---

## When NOT to use `/deploy`

- **Net-new install design** — designing a new install shape (target type, preset, hostname strategy) is a fireside, not a routine deploy. Use `/report` for the design discussion; use `/deploy` once the parameters are decided.
- **Multi-cluster orchestration** — out of scope for v1. Today's deploys are single-cluster (`wip-stable` and `wip-kb` are separate manual ops).
- **Backup/restore** — separate set of operations; `/deploy` doesn't touch persistent state.

---

## What this does NOT replace

This skill encapsulates the *recipe*. It does not replace BE-YAC's judgment when something unusual happens — the skill's job is to surface unusual things in pre-flight or smoke, not to handle them. If pre-flight fails or smoke shows surprises, BE-YAC reads the output and decides the fix. The skill is a force multiplier, not a replacement.

---

## Backgrounding (future, not in v1 of this skill)

Once the skill ships and pre-flight has been hardened against ~10 deploy cycles, wrapping invocations in `Task` subagents with `run_in_background` is a separate change — call it `/deploy redeploy --background`. Not in scope for this case; mentioned for the implementer's awareness so the skill's exit codes and output formats stay machine-parseable.
