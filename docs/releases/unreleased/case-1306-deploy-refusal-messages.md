---
section: fixes
cases: [1306]
packages: [wip-deploy]
---

### `status --diff` reports a render refusal as a message, and a refused registry names a value that works

`wip-deploy status --diff` printed a Python traceback when the render refused,
for instance when a Caddyfile routes to a service the render did not produce or
an env reference names an inactive component. It now prints `error: <reason>`
and exits 2, like its other refusals. The verbs that change an install
(`add-app`, `remove-app`, `add-module`, `remove-module` and the rest) printed a
traceback for an env reference to an inactive component too; they now print
`✗ not done: <the change> — the render refused: <reason>` (for example
`✗ not done: Added app 'foo' to wip-local — the render refused: …`) and exit 2,
as `install` does.

A refused `images.registry` used to suggest a replacement that could itself be
refused (`ghcr.io/x/ /` suggested `ghcr.io/x/`, `https://https://x` kept a
scheme). The suggestion is now always a value the check accepts. When nothing
would be left, for example `https://`, the message says so instead of
suggesting an empty registry.
