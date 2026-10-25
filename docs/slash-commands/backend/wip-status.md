Check the current state of a running WIP instance — service health, container health, data counts.

**Prerequisite:** MCP tools must be connected. If they aren't available, tell the user: "MCP tools aren't connected. Run `/wip-setup` to diagnose and fix your environment." Do NOT try to debug infrastructure problems — `/wip-setup` handles that.

### Steps

1. **Verify WIP connectivity:**
   - Call `get_wip_status` — confirms the **backend services** it holds clients for are healthy (registry, def-store, template-store, document-store, reporting-sync). It does not see app containers or infrastructure, so its `overall: all healthy` is not the whole picture — step 2 is.
   - If it fails: "WIP services aren't reachable. Run `/wip-setup` to check your environment." Stop here.

2. **Check container health** (every container of the attached install, apps included):
   ```bash
   RT=$(command -v podman || command -v docker)
   PROJECT=$("$RT" inspect wip-registry --format '{{index .Config.Labels "com.docker.compose.project"}}')
   "$RT" ps -a --filter "label=com.docker.compose.project=$PROJECT" --format '{{.Names}}\t{{.Status}}'
   ```
   - Report every container whose status is **not `Up …`** (Exited, Created, Restarting, Paused — **down**) or reads **`(unhealthy)`** (**unhealthy**). `(starting)` right after a restart is normal; say so only if it persists across two checks.
   - `Up …` with no health suffix is fine: that container has no healthcheck (dex, garage, caddy today).
   - Any down or unhealthy container makes the instance **DEGRADED**, whatever step 1 said. An unhealthy app container is the usual sign of a crash at startup while its SPA still loads — for a dev install, see the contract annex "Followed, not skipped: a dependency change reaches the dev container only when it restarts" (`docs/wip-deployable-app-contract.md`).
   - If there is no container runtime (`RT` empty — a k8s install) or `wip-registry` is not a local container: say "container health not checked (no local container runtime — for k8s use `kubectl get pods`)" and continue.

3. **List terminologies:**
   - Call `list_terminologies`
   - Display as a table: value, label, status, number of terms

4. **List templates:**
   - Call `list_templates`
   - Display as a table: value, label, status, version, number of fields, extends (if any)

5. **Count documents per template:**
   - For each active template: `query_by_template(template_value)` and report the total count

6. **Summarize:**
   - Health: backend services (step 1) and containers (step 2) — name every down or unhealthy container
   - Total terminologies (active/inactive)
   - Total templates (active/inactive)
   - Total documents
   - Any issues or warnings

### When to Run
- **Always** at the start of a session (after `/wip-setup` has passed once)
- After infrastructure changes (wip-deploy install / rebuild / restart)
- Before and after running seed scripts
