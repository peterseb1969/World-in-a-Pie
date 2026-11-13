---
section: action-required
cases: [1244]
---

### Apps-only installs are retired: `--apps-only`, `--remote-wip` and `import-bundle` are gone

An apps-only install ran an app's containers with no WIP backend of its own, against a remote WIP. It was the only install shape with no login in front of its apps, and the only one whose apps ran on the install's `api-key` instead of a key of their own. In a non-privileged-only apps-only install, the render also died with a traceback after writing the secret store.

An app that runs away from its WIP is now **attached** to it instead: the app runs with its own deployment and its own Console key, and the WIP's platform admin records it (`POST /api/registry/attachments`, with the app-edge module on). The WIP then serves it at `https://<name>.<hostname>/`, behind the auth gateway.

Removed:

- `install`/`validate`/`show-spec`/`render --apps-only` and `--remote-wip`
- the `import-bundle` verb
- the external-CA mount into app containers
- the spec fields `modules.suppress_core`, `network.remote_wip_url` and `network.external_ca_mount`
- the activation predicate `requires_core`

**Action:**

- **An apps-only install** no longer loads: every verb refuses it, naming the attach path. Remove it with `wip-deploy nuke --name <install>`, and attach its apps to the WIP they used.
- **Every other install** moves to saved-state format 5 on its next apply, dropping those fields at their defaults. As with format 4, an older deployer cannot read the result.
