Check the current state of the WIP instance. Run this at the start of any session to verify services are healthy and understand the current data state.

### Steps

1. **Verify WIP connectivity:**
   - Call `get_wip_status` — confirms all services are healthy
   - If it fails, alert the user that WIP may be down

2. **Check container status:**
   - Run `podman ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null`
   - Report any containers that are not running or are restarting

3. **List terminologies:**
   - Call `list_terminologies`
   - Display as a table: value, label, status, number of terms

4. **List templates:**
   - Call `list_templates`
   - Display as a table: value, label, status, version, number of fields, extends (if any)

5. **Count documents per template:**
   - For each active template: `query_by_template(template_value)` and report the total count

6. **Check NATS JetStream health (if available):**
   - Run `podman exec wip-nats nats stream ls 2>/dev/null || true`
   - Report stream names and message counts if accessible

7. **Summarize:**
   - Total terminologies (active/inactive)
   - Total templates (active/inactive)
   - Total documents
   - Container health overview
   - Any issues or warnings

### When to Run
- **Always** at the start of a new session
- After infrastructure changes (setup.sh, docker-compose changes)
- When debugging service connectivity issues
- Before and after running seed scripts
