---
section: action-required
cases: [1035]
---

### A user's groups reach the Registry whole: a comma in a group name no longer grants another group

Every service asks the Registry what a user may do, and sent the user's groups
in one `X-User-Groups` header joined with commas, which the Registry split
again. A group name is the identity provider's to choose. In `hybrid` auth
mode, where the services validate user tokens themselves, a token group named
`staff,wip-admins` reached the Registry as the group `wip-admins`, and the
Registry answered administrator on every namespace. (auth-gateway already
drops such names from the sessions it creates at login.)

`X-User-Groups` now carries a JSON array of strings, and the Registry takes
each name whole. A header that is not such an array is refused with 400
rather than read as "no groups". The deprecated `groups` query parameter on
`/api/registry/my/check-permission` and `/api/registry/my/accessible-namespaces`
is removed. No service sent it.

**Action:** roll the Registry and every other service to the same image tag in
one apply (`wip-deploy redeploy --name <install> --tag <tag>`). A service still
on an older image sends the comma form; the Registry refuses it, and that
service answers 503 to every caller that has groups (users and API keys
alike) until it is rolled too. A hand-written caller of these two internal
endpoints must send the groups as a JSON array in `X-User-Groups`.
