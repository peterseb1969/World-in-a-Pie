---
section: action-required
cases: [1243]
---

### `--http-port` and `network.http_port` are gone

`--http-port` (default 8080) was bound by no target. Preflight only reserved it, so an unrelated process on 8080 could stop an install of a port the install never used, and the drift guard compared it. The flag and the spec field are removed.

**Action:**

- Drop `--http-port` from install scripts; the deployer now refuses it as an unknown option.
- Saved install state moves to format 6 on the next apply, dropping the field. As before, an older deployer cannot read it.
