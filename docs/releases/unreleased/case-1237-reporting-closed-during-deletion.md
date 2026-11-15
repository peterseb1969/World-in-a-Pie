---
section: action-required
cases: [1237]
---

### A namespace's reporting data is closed as soon as its deletion begins

While the Registry deleted a namespace, its PostgreSQL schema stayed readable
to administrator and service keys until the deletion's drop step. That covered
`/query`, functions such as `query_to_xml`, CSV export and registered-view
reads, and registering a view inside the schema still succeeded. A deferred
step does not hold the later ones back, so when reporting-sync was down at the
drop, the data was readable even after the namespace record was gone.

The deletion's first step now closes the namespace in reporting-sync (`POST
/api/reporting-sync/namespace/{prefix}/close`, privileged). reporting-sync
revokes every role's `USAGE` on the schema except its owner's, marks it closed,
and refuses to reuse it or write a view into it. A reporting-sync that restarts
neither grants a closed schema again nor answers privileged SQL (503) until it
has closed every namespace the Registry is deleting or has deleted.

A reporting step that reporting-sync refuses now fails the deletion, instead of
completing with "rows may remain". This covers the close, the drop, a missing
service key, and a reporting-sync without the endpoint (404). The namespace
stays locked and its prefix held until `resume-delete` finishes it.
File-storage cleanup is unchanged.

**Action:** roll the Registry and reporting-sync together (`wip-deploy redeploy
--name <install> --tag <tag>`). A new Registry against an older reporting-sync
gets 404 from the close route, so every namespace deletion would fail at its
first step until reporting-sync is rolled too.
