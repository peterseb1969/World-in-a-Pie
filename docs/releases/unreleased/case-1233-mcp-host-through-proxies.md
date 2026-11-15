---
section: fixes
cases: [1233]
packages: [wip-deploy]
---

### MCP through the router and the public URL no longer answers 421 "Invalid Host header"

The MCP server accepts only the hosts on its DNS-rebinding allow-list, and the
two proxies in front of it forwarded the caller's own Host. An app following
the contract's `MCP_URL=http://wip-router:8080/mcp` got `421 Misdirected
Request` on `initialize`. So did a client using the install's public
`https://<hostname>/mcp`, unless it used `localhost`. Only a client dialling
`wip-mcp-server:8007` directly got through.

The `/mcp` route now sends the MCP server its own host as `Host`, at the
router and at the public edge, on every target. The allow-list also holds the
name the public edge dials: the Service FQDN on k8s. That name comes from a
new env var on the mcp-server, `MCP_ALLOWED_SERVICE_HOST`, which the
deployer sets.

**Action:** none for apps. `MCP_URL=http://wip-router:8080/mcp`, as the
contract documents, works now; `http://wip-mcp-server:8007/mcp` keeps
working. Roll the platform with `wip-deploy redeploy --name <install>` so the
router, the edge and the mcp-server pick up the change together.
