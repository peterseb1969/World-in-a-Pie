---
section: deployment
---

- **App attachments in the Registry (not served yet).** `POST
  /api/registry/attachments` records an app put under the umbrella through
  the API: name, upstream, gateway protection and proxy options. Also
  `GET /attachments[/{name}]` and `POST /attachments/deactivate`. Writes are
  platform admin only; reads are also open to the app edge's key group
  `wip-app-edge`. Never hard-deleted. A write replaces the whole record, so
  `auth_required` and `allowed_groups` are required on every write.
  **Served by a new component, `app-edge`** (rendered whenever the auth
  gateway is on): it reads the attachments every 10 s with its own key
  `wip-app-edge` (reads attachments only) and serves `/apps/<name>` behind
  the gateway. It dials the address it checked, removes browser-sent
  identity headers and the gateway's session cookie, and answers 503 for
  every attached app at the first poll (every 10 s) that finds the Registry
  silent for more than 30 s, in practice 30 to about 45 s. The public
  router hands it every `/apps` path no deployed app's route claims. On k8s
  a NetworkPolicy, `app-edge-egress`, limits its egress. New install files:
  none. New secret: `wip-app-edge-api-key`, generated on the next apply. The deployer renders a new file
  beside the namespace reservations, `config/auth/attach-policy.json`
  (`WIP_ATTACH_POLICY_FILE`): the install's own hosts, which an attachment may
  not proxy to, and its apps' route names, which it may not take. An apply
  that would add an app on an attachment's route is refused. Existing installs
  gain the file and the Registry's env var on their next redeploy; until
  then, the Registry refuses only loopback, link-local, unspecified and
  multicast upstreams. See `docs/wip-guide.md` §6.5.
- **Scoped applies on k8s carry the key file and the namespace
  reservations.** `add-app`, `app-deploy` and `app-namespace` on a k8s
  install applied a fixed list of shared manifests that did not include
  `api-keys-secret.yaml`, so an app added that way ran with a key no service
  knew (401 everywhere) and its namespaces were not reserved. A scoped apply
  now sends every top-level rendered manifest. Services pick up a new key
  once the kubelet refreshes the mounted Secret, typically within a minute or
  two. **Action:** on a k8s install that gained an app through a scoped verb,
  run one `wip-deploy redeploy` (or any scoped verb) with the new deployer.
- **`verify --security --live` checks each app's own key.** For every
  enabled app it sends the app's key across its namespace boundaries (another
  app's documents and registry entries, a view in another namespace, a
  namespace outside its reservations, its own namespace against the
  reservation, a terminology in `wip`) and requires a refusal, each paired
  with a request inside its own namespaces that must pass. A refused registry
  entry must read exactly like a missing one. Write probes carry
  recognisable values (`wip-verify-unreserved`, `WIP_VERIFY_PROBE`,
  `v_wip_verify_probe`) that a failure names if one got through. A static
  check reads the rendered install: only an app declaring `privileged_key`
  holds the master key. The canary's namespace-upsert probe now sends a valid
  body: the route checks permission after validating the body, so its old
  invalid-body probe failed the check on every install.
- **`wip-deploy images` shows which image each service runs, and why.**
  Read-only, from the saved spec. One row per enabled component and app, with
  its image and SOURCE: the rule that decided its tag. The rules are `pin` (a
  per-service `--image-tag`, also written by `add-app --tag` and `app-deploy
  --tag`), `base` (the deployment-wide `--tag`), `manifest`, `latest`,
  `upstream` (an infra image's own version, which a base tag never touches)
  and `source` (dev target: built locally as `<name>:dev`, tags do not
  apply). `--tag` and `--image-tag` preview exactly the roll `redeploy` makes
  with the same flags, without applying it. Each row says whether it moves or
  stays, the apps that stay on their current images are named, and the
  preview ends with the `redeploy` command that applies it. `--format json`
  is available.
- **`redeploy --tag` no longer moves apps.** Apps are built and tagged in
  their own repos, so a new platform tag rarely names an image that exists
  for them. Before this change, every app without its own pin rendered at
  the new tag and failed to pull. Now each such app is pinned to the image it
  runs today, and the command names it. To move an app, pass `--image-tag
  APP=TAG` in the same call, or use `app-deploy`. A redeploy that does not
  change the base tag pins nothing.
- **`nuke --remove-data` also removes the saved spec.** After a teardown
  that destroyed an install's data, the next `install` was refused as a
  reshape of an install that no longer existed: "install 'default' already
  exists and this run would reshape it". The cause was
  `~/.wip-deploy/<name>/deployment.deployer-state`, which no `nuke` removed.
  Now `--remove-data` deletes it too: for the one install in the scoped
  modes (compose and k8s), and for every install under `~/.wip-deploy/`
  with `--purge-all`. The report and the confirmation prompt both say so.
  Without `--remove-data` the saved spec stays, so `redeploy` brings an
  install back on its surviving volumes. Secrets still go only with
  `--remove-secrets`, so the next install reuses them by default.
- **External identity providers (Dex connectors).** `wip-deploy connector
  add|set-secret|remove|list` declares Google, GitHub, a corporate OIDC or
  LDAP provider in the spec (`auth.connectors`), rendered into Dex on every
  target. The redirect URI is derived (`<issuer>/callback`); the client
  secret is operator-supplied into the secret backend (`dex-connector-<id>`)
  and never generated, so an apply with a missing one stops with
  instructions. `wip-deploy group-map add|remove|list` grants WIP groups to
  federated users by verified email or domain (`auth.group_mappings`).
  `wip-admins` and `wip-services` are never granted to a federated login,
  neither by mapping nor by the provider's own groups: every token-validating
  service and the auth-gateway drop them (`WIP_AUTH_FEDERATED_GROUPS`, set
  only when a connector is declared). See wip-guide §4.6.
- **Dex static users hold several groups.** A spec user carries `groups`
  (a list) instead of `group`; `wip-deploy user add --group` takes a
  comma-separated list, and the new `wip-deploy user set-groups <user>
  --group a,b` replaces a user's groups in place, keeping the password.
  Saved installs whose users still say `group` load unchanged. The first
  user verb or re-render writes `groups` into `deployment.deployer-state`,
  which an older `wip-deploy` then refuses to load: upgrade the deployer on
  every machine that drives the install.
- **A stale or incomplete secret store stops the apply.** Every apply now
  records a fingerprint of each secret it ran with (`secret_digests` in
  `deployment.deployer-state`, never the values) and checks the store
  against it, and on k8s against the live `wip-secrets`, before generating
  anything. A known secret that is missing or different stops the apply
  with the names. Previously the deployer generated fresh datastore
  passwords from an old copy of the install folder and wrote them into the
  cluster while the pods kept the old ones. New verbs: `wip-deploy secrets
  pull` (k8s: adopt the running values) and `wip-deploy secrets accept`
  (record the folder as right). Every apply names the secrets it generated.
  See wip-guide §7.2.
- **Scoped verbs also restart what they change elsewhere.** `add-app`,
  `app-deploy`, `remove-app` and a scoped `redeploy` compare every
  workload's config hash with the previous render and add the out-of-scope
  services whose configuration changed — a secret they read, the router's
  routes, MongoDB's user set — printing their names. Before, those changes
  went live only at the services' next unrelated restart.
- **A failed dev-target build no longer takes the stack down.** A full dev
  apply removed every `wip-*` container and then built the images inside
  `compose up --build`, so a failed build left nothing running. The apply
  now builds first and touches containers only after the build succeeded.
  podman-compose also runs with `--parallel 4`: unbounded, a dozen
  simultaneous builds each opened an SSH session into the macOS podman
  machine, and sshd's MaxStartups limit (10) dropped the surplus builds
  with `ssh: handshake failed: EOF`.
- **The MCP server's network transports authenticate on every install.**
  They enabled their key check only when `API_KEY` or
  `WIP_AUTH_LEGACY_API_KEY` was set; the deployer provides the key as
  `MASTER_API_KEY`, so on k8s installs any workload in the namespace got an
  MCP session — acting with the server's admin key — without credentials
  (compose/dev were covered only because their `.env` happens to carry
  `API_KEY`). The transports now require `X-API-Key` equal to the key the
  server itself uses (`WIP_API_KEY` > `MASTER_API_KEY` > `API_KEY` >
  `WIP_API_KEY_FILE`), compare it in constant time, no longer accept it as a
  `?api_key=` query parameter, and **refuse to start without a key** unless
  `MCP_ALLOW_UNAUTHENTICATED=1` is set for local development. In-cluster
  clients (WIP-KB's query agent) already send `X-API-Key`. Takes effect with
  the next mcp-server image.
- **A rotated key now reaches the services that read it, and the master
  key can be rotated.** Services read their secrets and `api-keys.json`
  only at start, and a change to that content alone left every rendered
  workload identical — k8s rolled nothing (and `subPath` mounts never
  refresh in a running pod), compose recreated nothing for a changed key
  file. Each non-storage workload now carries a hash of those inputs (k8s
  pod-template annotation, compose label `wip.dev/config-hash`), so an
  apply restarts exactly the services whose key changed. `wip-deploy
  rotate-key --secret api-key` rotates the install's master key;
  `rotate-key` names the services it restarted and then probes the install
  (new key accepted, old refused; `--no-check` skips it). Database
  passwords and other generated secrets are refused — they need a
  store-side change. **First apply after upgrading the deployer:** the
  hash is new on every non-storage workload, so that one apply rolls all
  Deployments (k8s) or recreates those containers (compose) once.
- **Dev installs mount the checkout they were installed from, whatever
  the cwd.** A dev redeploy (or any other mutation verb) used to bind-mount
  the backend source of whichever git checkout the command ran in, while
  the deployer-state kept recording the original one — running wip-deploy
  from a second clone silently re-pointed a running dev install at that
  clone. The mounts now follow the same root as discovery: `--repo-root` /
  `WIP_REPO_ROOT`, else the root stamped at install, else the cwd. An
  explicit `--repo-root` that differs from the stamped root still moves the
  install, and now prints a `note:` naming both. `render` and `install`
  with `--repo-root` also mount from the flag (before, always from the cwd).
  `status --diff` discovers from the stamped root before the cwd.
- **Compose and dev containers receive only the secrets they declare.**
  Every service or app that referenced any secret also got the install's
  whole `.env` as `env_file`, so each such container — apps included —
  held the PostgreSQL superuser and MinIO root passwords, the Dex user
  passwords and the master key, whatever its manifest declared. `.env` is
  now only the `${VAR}` interpolation source (as it already was for the
  values themselves), so a container sees exactly its manifest's
  `from_secret` variables — the same set k8s gives it. Code that read an
  undeclared variable worked only through the leak; none was found in the
  platform services or the local apps. The next apply recreates every
  container that referenced a secret.
- **MinIO image** is `ghcr.io/peterseb1969/minio:RELEASE.2025-09-07T16-13-09Z`.
  MinIO no longer publishes pullable images (Docker Hub answers with HTML,
  quay.io with 401); this is a copy of the last release in use. It is
  **arm64-only** — an amd64 cluster needs a multi-arch copy before a fresh
  install.
- **`wip-deploy verify --security --live`** probes a running install through
  its public URL: every protected route must refuse a request without
  credentials (JSON 401/403/404), every health probe must answer 200, and
  reporting-sync must report `postgres_service_roles_ok: true`. Certificates
  are verified only for `tls=letsencrypt`.
