---
section: fixes
cases: [1228]
---

### `remove-app` removes the app's container even when the apply fails after changing the install

When `remove-app`'s apply failed after it had already changed the running install
(a proxy reload, a post-install hook or the health wait failing), the deployer
saved the spec without the app but left the app's container running. Running
`remove-app` again then answered "not in this install — no change", and only the
next full `up` removed the container. Now the container is removed whenever the
spec that drops the app is saved. A failure that changed nothing still leaves
both the saved spec and the container as they were.

The warning `install --reconcile` prints over an unreadable state file no longer
names `.retired` as the only name the file is set aside under. When that name is
taken, the file goes to `.retired2` and on, and the step that moves it prints
the real name.
