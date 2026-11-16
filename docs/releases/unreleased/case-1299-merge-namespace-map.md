---
section: new
cases: [1299]
packages: [wip-client, wip-toolkit]
---

### A merge can redirect every namespace of a multi-namespace archive

`namespace_map` — the explicit `{source: target}` mapping that `mode=fresh`
already took — now works with `mode=merge`. An archive carrying `kb` and
`library` merges into `kb2` and `library2` in one job, keeping every canonical
id, where before the only routes there were splitting the archive and merging
each part, or a fresh restore that re-mints the ids you wanted kept:

```
POST /api/document-store/backup/namespaces/kb/restore   (mode=merge)
    namespace_map = {"kb": "kb2", "library": "library2"}
wip-toolkit restore kb-library.zip --mode merge \
    --map kb=kb2 --map library=library2 --dry-run
```

Each target must already exist and the caller must be admin on it, as on every
namespace the archive carries. One destination per source: folding two
namespaces into one stays `mode=fresh`, which re-mints ids and can therefore
check the folded sources against each other. A single-namespace archive can use
the map too — `{"kb": "kb2"}` places it exactly as `target_namespace` does.

A map that misses a namespace, names one the archive does not carry, shares a
destination between two sources, or arrives alongside `target_namespace` is
refused with a 400 before the job is created. Both restore doors take the map —
the upload and `POST /backup/jobs/{job_id}/restore`, which now also takes
`target_namespace`, so a redirect no longer needs the archive re-uploaded.

A redirect rewrites the arriving rows to carry the target's name: their
namespace fields, their Registry composite keys and hashes, and the references
between the namespaces being moved, so a document merged from `kb` points at
`library2` rather than at a `library` this install does not have. That rewrite
now includes qualified `<namespace>:<value>` strings inside document values, as
a fresh restore always has; for a redirected merge that is new, and a row whose
identity is computed from such a string gets the identity the rewritten value
produces. Ids are preserved, so the archive's ids must be free on this install:
a redirected merge serves an archive from another instance, or one whose
namespaces have since been deleted here.
