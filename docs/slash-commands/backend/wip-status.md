Check the current state of a running WIP instance — service health, data counts.

**Prerequisite:** MCP tools must be connected. If they aren't available, tell the user: "MCP tools aren't connected. Run `/setup` to diagnose and fix your environment." Do NOT try to debug infrastructure problems — `/setup` handles that.

### Steps

1. **Verify WIP connectivity:**
   - Call `get_wip_status` — confirms all services are healthy
   - If it fails: "WIP services aren't reachable. Run `/setup` to check your environment." Stop here.

2. **List terminologies:**
   - Call `list_terminologies`
   - Display as a table: value, label, status, number of terms

3. **List templates:**
   - Call `list_templates`
   - Display as a table: value, label, status, version, number of fields, extends (if any)

4. **Count documents per template:**
   - For each active template: `query_by_template(template_value)` and report the total count

5. **Summarize:**
   - Total terminologies (active/inactive)
   - Total templates (active/inactive)
   - Total documents
   - Any issues or warnings

### When to Run
- **Always** at the start of a session (after `/setup` has passed once)
- After infrastructure changes (setup.sh, docker-compose changes)
- Before and after running seed scripts
