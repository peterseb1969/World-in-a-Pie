# wip-deploy v2 — Intent audit and reusable-pattern analysis

**Date:** 2026-04-17
**Trigger:** Validating the k8s renderer end-to-end against kubi5-1
surfaced three classes of bug that were all the same underlying issue —
the intent layer was incomplete, and the compose renderer had hidden
special-cases filling the gaps. Each special-case blew up when the k8s
renderer tried to emit equivalent output from the "same" intent.
**Question to answer:** Does wip-deploy actually help, or does it get
in the way? What's genuinely reusable, what's target-specific, and
where do the gaps live?

---

## The headline

**wip-deploy helps — but only if we strictly enforce "no implicit
behavior."** Every hidden assumption in a renderer is a portability bug
waiting to fire when a second renderer tries the same intent. Today we
have three such bugs and Peter caught the headless-chicken pattern:
patching symptoms without fixing the structural cause.

The structural cause: **the compose path was treated as the source of
truth, and the intent layer was reverse-engineered from it.** Anything
the compose Caddyfile did implicitly (special-case route blocks,
hardcoded internal gateway, hidden port conventions) didn't make it
into the intent. The k8s renderer, asked to "emit from the same intent,"
found those things missing.

---

## Inventory by layer

Five layers. Each layer's responsibility is clean only if the one below
it is complete.

### Layer 1 — Component intent (fully reusable)

What a single service needs, independent of target. Lives in
`components/<name>/wip-component.yaml` or `apps/<name>/wip-app.yaml`.

| Concern | Status | Notes |
|---|---|---|
| Identity (name, category, description) | ✅ | Stable |
| Image reference (name, tag, build_context) | ✅ | Stable |
| Command override | ✅ | Recently promoted — uvicorn heuristic removed |
| Ports (named, numbered, protocol) | ✅ | Stable |
| Storage (name, mount, size, access mode) | ✅ | Stable |
| Env vars (required + optional, source pointers) | ✅ | 6 source types |
| Depends_on (hard-dep semantics) | ✅ | Soft deps via optional envs |
| Routes (path, auth_required, streaming) | ⚠️ | See gap #1 below — incomplete for some components |
| Healthcheck (endpoint/command + probe tool) | ✅ | Recently added probe: curl\|wget\|auto |
| OIDC client (client_id, redirect_paths) | ✅ | Stable |
| Activation (predicates for conditional components) | ✅ | Stable |
| Resources (cpu/memory requests + limits) | ✅ | Stable |
| Post-install hooks (shell + timing) | ✅ | Stable, escape hatch |

### Layer 2 — Deployment intent (fully reusable)

The *what* of a whole deployment. User-configurable via CLI flags +
preset + optional `--save-spec`.

| Concern | Status |
|---|---|
| Target (compose\|k8s\|dev) | ✅ |
| Preset (headless\|core\|standard\|analytics\|full) | ✅ |
| Module selection (optional components on/off) | ✅ |
| App selection (which apps enabled) | ✅ |
| Auth mode + gateway on/off | ✅ |
| Hostname | ✅ |
| TLS mode (internal\|letsencrypt\|external) | ✅ |
| Image source (registry + tag) | ✅ |
| Secret backend (file\|k8s\|sops) | ⚠️ Only file implemented |
| Apply-wait behavior | ✅ |

### Layer 3 — Target-aware config generation (shared functions with target branches)

Pure functions. Same inputs (intent) → target-branched outputs. Lives
in `deployer/src/wip_deploy/config_gen/`.

| Concern | Status | Notes |
|---|---|---|
| URL scheme+port construction | ✅ Just fixed | `_format_url` strips scheme-default ports |
| Service DNS name resolution | ✅ | `wip-foo` (compose) vs `wip-foo.ns.svc.cluster.local` (k8s) |
| Env var source resolution | ✅ | `from_component`, `from_secret`, `from_spec`, etc. |
| Collected-secret filtering | ✅ Recently added | `UncollectedSecretRef` |
| Inactive-component filtering | ✅ | `InactiveComponentRef` |
| Route resolution | ✅ | Shared `resolve_routes` feeds both Caddy and nginx |
| Dex user/client generation | ✅ | bcrypt hashed passwords |

### Layer 4 — Target-specific renderers (pure translation)

Emit native config. Lives in `deployer/src/wip_deploy/renderers/`.

| Target | Status |
|---|---|
| Compose (`compose.py` + `compose_caddy.py` + `compose_dex.py`) | ✅ End-to-end validated |
| K8s (`k8s.py`) | ⚠️ 90% working; 1 gap (see below) |
| Dev-simple (`dev_simple.py`) | ✅ Renders; not end-to-end validated |
| Dev-tilt | ❌ Not implemented |

### Layer 5 — Apply/operate (target-specific shell-out)

Deploy the tree. Lives in `apply.py`, CLI install/status verbs.

| Target | Status |
|---|---|
| Compose apply (podman-compose up + health wait) | ✅ |
| K8s apply (kubectl apply + rollout wait) | ❌ Tracked as Priority 4 #12 |
| Dev apply | ⚠️ Reuses compose |
| Status verb (compose + k8s) | ✅ |
| Preflight (ports + stale containers) | ✅ |
| Nuke | ✅ |

---

## The current gaps — diagnosis and fix plan

### Gap #1 — Route declarations incomplete on some components

**Symptom:** `/dex/*` and `/auth/*` routes got 404 in k8s because the
Dex and auth-gateway manifests didn't declare them. The Caddy renderer
had hardcoded special-cases so compose worked regardless.

**Root cause:** Component manifests were written focused on API routes;
auxiliary routes (OIDC endpoints, auth flow) lived only in the
Caddyfile.

**Where this belongs:** Layer 1 (component intent). Every browser-
facing route a component handles must be in its manifest.

**Fix status:** Done today (commit pending). Dex manifest now has
`/dex`, auth-gateway has `/auth`. Caddy renderer special-cases removed.

**Generalization:** Invariant test — "no renderer may hardcode a path."
Add a lint rule or code-review checklist.

---

### Gap #2 — Internal API gateway (the current blocker)

**Symptom:** react-console's SSR proxy uses `WIP_BASE_URL` to reach
WIP services. In compose, this is `http://wip-caddy:8080` — Caddy has
an internal :8080 listener that multiplexes `/api/*` to services. In
k8s, no such thing exists; the URL resolves to nothing; SSR calls
return 502.

**Root cause:** `network.internal_base_url` in SpecContext was
hardcoded to `http://wip-caddy:8080` regardless of target. This is a
compose-specific implementation detail leaking into the intent layer.

**What the concept actually is:** Apps that proxy API calls
server-side need an **in-cluster HTTP aggregator** that routes `/api/*`
paths to the right backend service. Common pattern — not WIP-specific.
A reverse proxy like Caddy, nginx, HAProxy, Envoy, or the ingress
controller can fill this role.

**Where this belongs:** Layer 1 (component intent) as a first-class
concept. The aggregator is a real deployment artifact that differs per
target in IMPLEMENTATION but not in PURPOSE.

**Fix plan (two options, recommendation below):**

#### Option A — Promote wip-caddy to an explicit Component (recommended)

Add `components/wip-caddy/wip-component.yaml`:
```yaml
metadata:
  name: wip-caddy
  category: infrastructure
  description: Internal HTTP aggregator for SSR proxies.

spec:
  image:
    name: caddy
    tag: "2"
  ports:
    - {name: http, container_port: 8080}
  # NEW field — renderer computes the Caddyfile from other components'
  # routes (auth_required=false, path starts with /api/) and mounts it
  # as config. Same mechanism Dex uses.
  config:
    template: aggregator
```

The renderer scans all active components for API routes and emits a
Caddy config block per route.

`network.internal_base_url` changes from `http://wip-caddy:8080`
(hardcoded) to `from_component: wip-caddy` — target-aware resolution
happens for free.

**What each renderer emits:**
- Compose: wip-caddy as a service (as today, but now from an explicit
  manifest instead of a hardcoded service block).
- K8s: wip-caddy as a Deployment + ClusterIP Service.
- Dev: same as compose.

**Cost:** One new component manifest. Small change to
`config_gen/spec_context.py` (`internal_base_url` uses `from_component`
resolution). Moderate change to `compose.py` (remove implicit Caddy
service block, rely on the component instead). No change to k8s
renderer — it just picks up the new component automatically.

#### Option B — Add a first-class `InternalGateway` concept

Heavier. Would add a new spec primitive with its own fields. Overkill
for one use case.

**Recommendation:** A. Uses existing concepts. Fits the "no special
cases" rule. Can be refactored to B later if a second gateway type
emerges.

---

### Gap #3 — Auth protocol response (fixed, but instructive)

**Symptom:** Gateway returned 302 on unauth; nginx auth-request rejects
3xx; Caddy passes it through.

**Root cause:** The gateway was written against Caddy's semantics.
Target semantics bled into the service.

**Where this belongs:** Layer 4 (renderer). The gateway speaks standard
HTTP (200/401); each renderer translates 401 into the target's redirect
idiom (nginx `auth-signin` annotation, Caddy `handle_response @401`).

**Fix status:** Done today. Gateway returns 401 with `X-Auth-Redirect`
hint. Compose Caddyfile wraps with `handle_response`. Nginx uses
`auth-signin` annotation.

**Generalization:** Service components should speak portable HTTP,
never target-idiomatic response codes. Renderers adapt.

---

### Gap #4 — NetworkPolicies / security layer (k8s-specific, not reused)

**Status:** Deliberately deferred to Priority 4 #11. The hand-written
v1 policies had fundamental bugs (cross-namespace blocking).

**Where it would belong if added:** Layer 1 could grow a concept like
"this component accepts traffic from: [ingress, X, Y]" — reusable. The
k8s renderer would emit NetworkPolicies; compose is flatter-networked
and would mostly ignore it (or emit podman network rules).

**Generalization note:** Security is a candidate for intent-layer
representation. Today it isn't there. Not blocking.

---

### Gap #5 — Observability hooks (reserved, not implemented)

`ObservabilitySpec` exists in the component model as a reserved field.
No renderer consumes it. Fine for now.

---

### Gap #6 — Bootstrap requirements not expressed

**Symptom:** Deployer didn't create the k8s TLS secret; I did it
manually with openssl. Same for the cluster's requirement to have
ingress-nginx + MetalLB installed.

**Where this belongs:** Layer 2 (deployment intent) or a new
"prerequisites" concept. The deployer could check prerequisites before
apply and either fail with a clear message, or offer to bootstrap them.

**Fix plan:** Tracked. Not blocking.

---

## Reusable patterns — what's actually portable

If wip-deploy were to be generalized beyond WIP (hypothetical future),
the patterns that travel well:

| Pattern | Portable? | Why |
|---|---|---|
| Component manifest shape (image + ports + env + storage + deps) | ✅ | Standard service model |
| Preset system (named starting points) | ✅ | Ergonomic for any toolkit |
| Target-aware URL / DNS / secret resolution | ✅ | Solves a real multi-target problem |
| Route declarations as component-level fields | ✅ | Matches HTTP service design |
| Depends_on with hard/soft semantics | ✅ | Common need |
| Activation predicates | ✅ | Common need (feature flags, modules) |
| Probe-tool abstraction (curl/wget/auto) | ✅ | Solves distroless + variant-image realities |
| Post-install hooks | ⚠️ | Works, but is an escape hatch — too many uses = design gap |
| OIDC-specific client fields | ❌ | WIP-specific. Would split into a "auth integrations" layer |
| WIP-specific presets (headless/core/standard) | ❌ | WIP topology assumptions baked in |

The core machinery (spec + components + config_gen + renderers) is
reusable. The WIP-specific content (component names, presets, auth
flow assumptions) is not. That's as it should be — the reusable tool
wouldn't ship with WIP's components; users would supply their own.

---

## Where wip-deploy HELPS a general audience

1. **Single declarative source, multiple targets** — same manifests
   drive compose/k8s/dev. One change, three outputs. Hand-maintaining
   three separate descriptions guarantees drift (cf. `install-path-
   drift.md`).
2. **Type-checked spec** — Pydantic catches config errors at validate
   time, not at `kubectl apply` time.
3. **Target-aware plumbing is shared** — URL/secret resolution doesn't
   belong in renderer code; it belongs in a function the renderers
   consume. wip-deploy gets this right.
4. **Ergonomic CLI** — `install`, `status`, `nuke`, `render` are
   uniform across targets.
5. **Preset ergonomics** — most users don't want to wire every module;
   they want "give me standard." Presets solve this.
6. **Portability as a design principle** — adding a new target is a
   new renderer, not a rewrite of the whole stack.

## Where wip-deploy could GET IN THE WAY

1. **Learning curve** — one more tool to learn vs known Docker Compose
   or raw Helm. Mitigation: CLI is small; intent layer reads like YAML
   most users already know.
2. **Less flexibility than raw YAML** — if a user wants to tweak a k8s
   field we haven't exposed, they're stuck (or must fork). Mitigation:
   add escape hatches per target (e.g., `extra_spec` merge).
3. **Missing production features** — no HPAs, PDBs, kustomize overlays,
   NetworkPolicies (by design — tracked as follow-ups).
4. **Hidden assumptions break portability** — the `wip-caddy:8080`
   issue today. Mitigation: strict "no implicit renderer behavior"
   rule; invariant tests across targets.
5. **Tight coupling to WIP specifics** — if generalized, the reusable
   machinery needs to be separable from WIP-specific manifests and
   presets. Currently not cleanly split. Not today's problem.

---

## Plan — ordered by dependency

### Step 1 — Fix gap #2 (internal API gateway) properly

- Promote `wip-caddy` to an explicit component manifest.
- `compose.py` stops emitting Caddy implicitly; reads from manifest.
- `spec_context.internal_base_url` uses `from_component: wip-caddy`.
- K8s renderer automatically emits Deployment + Service for wip-caddy.
- Dev renderer inherits from compose behavior.
- Add an invariant test: "every env var's source must resolve to
  something declared in intent; no renderer may fabricate URLs."

**Est:** 2 hours, bulk of the fix + tests.

### Step 2 — Validate k8s end-to-end (continuation of today)

- With gap #2 fixed, re-render and re-apply wip-dev.
- Browse to `https://wip-dev-kubi.local/apps/rc/`, confirm SSR proxy
  renders real data.
- If clean: declare k8s target validated.

**Est:** 30 min.

### Step 3 — Write the "no implicit renderer behavior" invariant

- Codify as test: for each target, diff the rendered output's
  referenced hosts/paths against the set of declared component routes
  + known target-native resources (ingress controller, DNS). Fail on
  anything unexplained.
- Would have caught all three bugs today as test failures instead of
  browser 500s.

**Est:** 3 hours. Valuable as a structural safeguard.

### Step 4 — Design memo: "separable core vs WIP-specific"

- For a future where wip-deploy becomes `ship-deploy` (generic):
  - Core package: `deploy/` with spec models, config_gen, renderers.
  - WIP-specific: `wip-manifests/` with components, apps, presets.
- Not urgent. A v3 concern if the tool gains audience.

### Step 5 — Priority 4 follow-ups (k8s QoL)

Already tracked — not relitigated here:
- `apply_k8s` with rollout wait
- k8s-secret backend
- NetworkPolicies (with intent-layer representation)
- kustomize overlays

---

## Answering Peter's question directly

> Does wip-deploy help, or does it get in the way?

**Helps — net positive — IF the "no implicit behavior" rule is enforced.**

The alternative (hand-maintain three paths) has a documented failure
mode: `install-path-drift.md`. The wip-deploy approach's failure mode
(intent gaps leak into implicit renderer behavior) is detectable via
invariants and fixable in the intent layer, not by abandoning the
approach.

Today's frustration was valid: I'd been patching symptoms instead of
closing the intent gap. That's a process failure I should have caught
— the plan for tomorrow is to close the last gap properly (step 1),
validate end-to-end (step 2), and add the structural safeguard (step 3)
so we don't repeat this.
