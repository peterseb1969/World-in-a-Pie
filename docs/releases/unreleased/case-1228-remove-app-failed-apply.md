---
section: fixes
cases: [1228]
---

### `remove-app` removes the app's container even when the apply fails after changing the install

When `remove-app`'s apply failed after it had already changed the running install
(a post-install hook or Garage provisioning failing), the deployer saved the spec
without the app but left the app's container running. Running `remove-app` again
then answered "not in this install — no change", and only a full `redeploy` or
`install` removed the container. Now the container is removed whenever the spec
that drops the app is saved. A failure that changed nothing still leaves both the
saved spec and the container as they were.

Such a failure no longer reads as if the removal had not happened: the error line
says the change is applied and saved and that a later step failed. This holds for
every verb that changes a saved install (`add-app`, `remove-app`, `app-deploy`,
the module, user and namespace verbs, `rotate-key`).

On compose and dev, `remove-app` for an app the install no longer lists removes
the app's container if an earlier removal left it running, so an install already
in that state is repaired by running the verb again.

The warning `install --reconcile` prints over an unreadable state file no longer
names `.retired` as the only name the file is set aside under. When that name is
taken, the file goes to `.retired2` and on, and the step that moves it prints
the real name.
