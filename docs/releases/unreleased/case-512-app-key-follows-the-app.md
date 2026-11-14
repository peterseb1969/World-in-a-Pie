---
section: deployment
cases: [512]
packages: [wip-deploy]
---

### Removing an app deletes its key; adding it again gives it a new one

`wip-deploy remove-app` left the app's own key (`app-<name>`) in the install's
secret store, so a later `add-app` brought the same key back. The Registry had
already revoked the key (the key file no longer named it), but the plaintext
stayed on disk and returned with the app.

Now `remove-app` deletes the app's key secret once the spec that drops the app is
saved, at the same moment its container goes: after a successful apply, and after
a failed one that had already changed the install. A removal that saved nothing
keeps the secret, and the app keeps running with its key. `add-app` of an app the
install does not run generates a new key, also when the store still holds one from
a removal made before this change. Every other apply (redeploy, rebuild, a change
to another app) keeps the key. The master key (`api-key`) is never touched, and an
app that holds it (`privileged_key`, React Console) has no key of its own.

On k8s an apply cannot drop a value from the live `wip-secrets` Secret, so
`remove-app` also removes the removed app's value from it (a JSON patch on that
one key). When the cluster refuses the patch, the removal still completes with a
warning naming the `kubectl patch` to run; the value left there names a revoked
key, and `add-app` replaces it rather than stopping as secret drift.

**Action:** none. A holder of a removed app's key outside the install (a script,
another machine) must be handed the new key after the app is added again.
