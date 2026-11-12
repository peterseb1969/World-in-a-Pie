---
section: fixes
cases: [1229]
---

### `install --no-wait` applies to its own run only; a health wait that runs out always fails

`install --no-wait` used to be saved with the install, so every later apply
(`redeploy`, `add-app`, `remove-app`, `app-deploy`, the user and namespace
verbs) silently skipped both the health wait and the post-install hooks. The
flag now affects only the run it is given to. `--wait-timeout` is still saved:
it is the install's own pace.

`spec.apply.on_timeout` is gone: a wait that runs out always fails the apply and
names the services that were not healthy. `up` now fails on its timeout too
(it used to print "Brought up" regardless), and `up` and `rebuild` check that
compose created every container they were asked for.

Saved install state moves to format 4 on the next apply. An install saved with
`--no-wait` waits again from then on. A state hand-edited to
`on_timeout: warn` or `continue` is refused by name; delete the line.
**Action:** none for normal installs. An older deployer cannot read a format-4
state, so do not downgrade the deployer after an apply.
