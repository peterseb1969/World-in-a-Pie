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
`--no-wait` waits again from then on, and the deployer says so when it reads the
state. A state hand-edited to `on_timeout: warn` or `continue` loads with the
setting dropped and a warning naming it.

`up` and `rebuild` wait as long as the install's saved timeout (`install
--wait-timeout`, default 300 s) unless their own `--wait-timeout` is given; they
used to wait a fixed 120 s. A health timeout now says "not confirmed healthy",
and marks a container `compose ps` did not list, or a poll where `compose ps`
itself failed, instead of calling its health bad.
**Action:** none for normal installs. An older deployer cannot read a format-4
state, so do not downgrade the deployer after an apply.
