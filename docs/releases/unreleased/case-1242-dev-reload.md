---
section: fixes
cases: [1242]
---

### On a dev install, auth-gateway hot-reloads, and the render says what doesn't

- auth-gateway's source was mounted on a dev install but its edits never ran. It now runs under the reloading launcher like the other uvicorn services. The next apply recreates it once.
- A dev render now notes, on stderr, each mounted service that has no watcher, so an edit needs `wip-deploy restart <svc>`. Today that is mcp-server and app-edge.
- It also notes each `--app-source` app built from its production Dockerfile because the checkout has no `Dockerfile.dev`.
